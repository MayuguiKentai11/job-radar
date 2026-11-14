/* JobRadar front-end (Alpine.js). All user data lives in this browser (localStorage);
   the server is stateless. The AI API key is kept server-side in an encrypted HttpOnly cookie. */
const LIST_FIELDS = ["keywords", "must_have", "nice_to_have", "exclude", "exclude_title", "blocked_companies"];
const ARRAY_FIELDS = ["modalities", "job_types", "sources", "levels", "languages", ...LIST_FIELDS];
const MAX_RUNS = 5, MAX_SEEN = 5000;

// ---------- browser storage (never throws) ----------
const store = {
  get(k, fallback) { try { const v = localStorage.getItem("jr_" + k); return v ? JSON.parse(v) : fallback; } catch (_) { return fallback; } },
  set(k, v) { try { localStorage.setItem("jr_" + k, JSON.stringify(v)); return true; } catch (_) { return false; } },
  del(k) { try { localStorage.removeItem("jr_" + k); } catch (_) { /* ignore */ } },
  clearAll() { try { Object.keys(localStorage).filter(k => k.startsWith("jr_")).forEach(k => localStorage.removeItem(k)); } catch (_) { /* ignore */ } },
};

function emptyForm(defaults) {
  const f = JSON.parse(JSON.stringify(defaults || {}));
  for (const k of ARRAY_FIELDS) if (!Array.isArray(f[k])) f[k] = [];
  for (const k of LIST_FIELDS) f[k + "Text"] = (f[k] || []).join(", ");
  f.hours_old = f.hours_old ?? 0;
  f.salary_min = f.salary_min ?? "";
  f.years_experience = f.years_experience ?? "";
  f.only_new = !!f.only_new; f.ai_match = !!f.ai_match; f.similar_titles = !!f.similar_titles;
  return f;
}

function formToParams(f) {
  const p = {};
  for (const [k, v] of Object.entries(f)) if (!k.endsWith("Text")) p[k] = v;
  for (const k of LIST_FIELDS) p[k] = f[k + "Text"] || "";
  p.hours_old = f.hours_old || null;
  p.salary_min = f.salary_min === "" ? null : f.salary_min;
  p.years_experience = f.years_experience === "" ? null : Number(f.years_experience);
  p.min_score = 0; // relevance only orders results; it never hides them
  return p;
}

function errorText(data, status) {
  const d = data && data.detail;
  return typeof d === "string" ? d : Array.isArray(d) ? d.map(x => x.msg).join("; ") : `Error ${status}`;
}

async function request(path, opts = {}) {
  const isForm = opts.body instanceof FormData;
  const headers = { "X-JobRadar": "1", ...(opts.body && !isForm ? { "Content-Type": "application/json" } : {}) };
  const res = await fetch(path, {
    method: opts.method || "GET", credentials: "same-origin", headers,
    body: opts.body && !isForm ? JSON.stringify(opts.body) : opts.body,
  });
  if (res.status === 401) { window.location.href = "/login"; throw new Error("Sesión expirada"); }
  return res;
}

async function api(path, opts = {}) {
  const res = await request(path, opts);
  let data = null;
  try { data = await res.json(); } catch (_) { /* empty */ }
  if (!res.ok) throw new Error(errorText(data, res.status));
  return data;
}

async function streamNdjson(path, body, onEvent) {
  const res = await request(path, { method: "POST", body });
  if (!res.ok) { let d = null; try { d = await res.json(); } catch (_) { /* */ } throw new Error(errorText(d, res.status)); }
  const reader = res.body.getReader(); const dec = new TextDecoder(); let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, i).trim(); buf = buf.slice(i + 1);
      if (line) onEvent(JSON.parse(line));
    }
  }
  if (buf.trim()) onEvent(JSON.parse(buf));
}

const ACCENTS = { a: "[aáà]", e: "[eéè]", i: "[iíì]", o: "[oóò]", u: "[uúüù]", n: "[nñ]" };
function accentRegex(term) {
  return term.toLowerCase().replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/[aeioun]/g, c => ACCENTS[c]);
}
function escapeHtml(s) {
  return (s || "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function safeUrl(u) { return /^https?:\/\//i.test(u || "") ? u : "#"; }

window.jobradar = function () {
  return {
    tab: "buscar",
    meta: { sources: [], modalities: [], levels: [], job_types: [], statuses: [], ai_providers: {}, limits: {} },
    labels: {
      mod: { remoto: "Remoto", hibrido: "Híbrido", presencial: "Presencial" },
      level: { practicante: "Practicante", junior: "Junior", "semi-senior": "Semi-senior", senior: "Senior", jefatura: "Jefatura" },
      type: { tiempo_completo: "Tiempo completo", medio_tiempo: "Medio tiempo", practicas: "Prácticas", temporal: "Temporal", freelance: "Freelance" },
      status: { nueva: "Nueva", interesante: "Interesante", postulada: "Postulé", entrevista: "Entrevista", descartada: "Descartada" },
      phase: { buscando: "buscando en portales…", filtrando: "filtrando y ordenando…", ia: "analizando con IA…", listo: "listo ✓", error: "error" },
    },
    form: emptyForm({}),
    settings: {}, cfg: { ai_provider: "gemini", api_key: "", ai_model: "", ai_top_n: 30, serpapi_key: "" },
    cv: store.get("cv", null),
    profiles: store.get("profiles", []), profileId: "", profileName: "",
    runs: [], runId: null, status: null, searching: false, summary: null,
    results: [], sel: null, tracked: [], aiRunning: false, aiProgress: null, toast: "",
    qf: { text: "", sort: "score", sources: [], modalities: [], statuses: [], onlyNew: false, withSalary: false, hideDiscarded: true },

    async init() {
      this.meta = await api("/api/meta");
      this.form = emptyForm(this.meta.defaults);
      await this.loadSettings();
      this.form.sources = this.defaultSources();
      this.loadRuns();
    },

    notify(msg) { this.toast = msg; clearTimeout(this._t); this._t = setTimeout(() => (this.toast = ""), 4000); },
    toggle(arr, v) { const i = arr.indexOf(v); i >= 0 ? arr.splice(i, 1) : arr.push(v); },
    sourceLabel(k) { return (this.meta.sources.find(s => s.key === k) || {}).label || k; },
    affinity(s) { return s == null ? "" : s >= 75 ? "alta" : s >= 50 ? "media" : "baja"; },
    safeUrl,
    ago(d) {
      const days = Math.round((new Date().setHours(0, 0, 0, 0) - new Date(d + "T00:00:00")) / 86400000);
      return days <= 0 ? "hoy" : days === 1 ? "ayer" : `hace ${days} días`;
    },
    salaryText(j) {
      if (!j.salary_min && !j.salary_max) return "";
      const cur = j.salary_currency === "USD" ? "US$" : "S/";
      const fmt = n => Number(n).toLocaleString("es-PE", { maximumFractionDigits: 0 });
      const per = { monthly: "/mes", yearly: "/año", hourly: "/hora", daily: "/día", weekly: "/sem" }[j.salary_period] || "";
      return j.salary_max && j.salary_min && j.salary_max !== j.salary_min
        ? `${cur} ${fmt(j.salary_min)}–${fmt(j.salary_max)}${per}` : `${cur} ${fmt(j.salary_min || j.salary_max)}${per}`;
    },

    // ---------- per-job data kept in the browser (status, notes, AI) ----------
    jobData() { return store.get("jobs", {}); },
    saveJob(j, patch) {
      const all = this.jobData();
      const cur = all[j.id] || {};
      all[j.id] = { ...cur, ...patch, title: j.title, company: j.company, url: j.url, source: j.source, location: j.location };
      if (!store.set("jobs", all)) this.notify("El almacenamiento del navegador está lleno. Borra búsquedas antiguas.");
    },
    mergeLocal(results) {
      const all = this.jobData();
      for (const r of results) {
        const d = all[r.id] || {};
        r.status = d.status || "nueva"; r.notes = d.notes || "";
        r.ai = d.ai || null; r.ai_score = d.ai ? d.ai.score : null; r.ai_model = d.ai_model || null;
      }
      return results;
    },

    // ---------- settings / CV ----------
    sourceOff(s) {
      // why a portal cannot be used right now ("" = available)
      if (s.unavailable) return s.unavailable;
      if (s.needs_key && !this.settings.has_serpapi_key) return "Requiere tu key gratuita de SerpApi (Configuración → Google Jobs).";
      return "";
    },
    defaultSources() { return this.meta.defaults.sources.filter(k => { const s = this.meta.sources.find(x => x.key === k); return s && !this.sourceOff(s); }); },
    async saveSerpApi(clear = false) {
      try {
        const key = (this.cfg.serpapi_key || "").trim();
        if (!clear && !key) return this.notify("Pega tu key de SerpApi");
        this.settings = await api("/api/settings", { method: "POST", body: clear ? { clear_serpapi_key: true } : { serpapi_key: key } });
        this.cfg.serpapi_key = "";
        if (!clear && !this.form.sources.includes("google")) this.form.sources.push("google");
        if (clear || !this.settings.has_serpapi_key) this.form.sources = this.form.sources.filter(k => k !== "google");
        this.notify(clear ? "Key de SerpApi eliminada" : "Key de SerpApi guardada: Google Jobs activado");
      } catch (e) { this.notify(e.message); }
    },
    async loadSettings() {
      this.settings = await api("/api/settings");
      this.cfg.ai_provider = this.settings.ai_provider;
      this.cfg.ai_model = this.settings.ai_model;
      this.cfg.ai_top_n = this.settings.ai_top_n;
    },
    onProviderChange() {
      const models = Object.keys(this.meta.ai_providers[this.cfg.ai_provider]?.models || {});
      if (!models.includes(this.cfg.ai_model)) this.cfg.ai_model = models[0] || "";
    },
    async saveSettings(clear = false) {
      try {
        const key = (this.cfg.api_key || "").trim();
        const prefix = this.meta.ai_providers[this.cfg.ai_provider]?.key_prefix;
        if (key && prefix && !key.startsWith(prefix)) return this.notify(`Esa key no parece de ${this.meta.ai_providers[this.cfg.ai_provider].label} (debería empezar con ${prefix})`);
        this.settings = await api("/api/settings", { method: "POST", body: { ai_provider: this.cfg.ai_provider, api_key: key || null, ai_model: this.cfg.ai_model, ai_top_n: this.cfg.ai_top_n, clear_api_key: clear } });
        this.cfg.api_key = ""; this.cfg.ai_model = this.settings.ai_model;
        this.notify(clear ? "API key eliminada" : "Configuración guardada");
      } catch (e) { this.notify(e.message); }
    },
    async uploadCV(ev) {
      const file = ev.target.files[0];
      if (!file) return;
      const fd = new FormData(); fd.append("file", file);
      try {
        const r = await api("/api/cv", { method: "POST", body: fd });
        this.cv = { text: r.cv_text, filename: r.cv_filename };
        store.set("cv", this.cv);
        this.notify(r.truncated ? "CV cargado (recortado a 30.000 caracteres)" : "CV cargado");
      } catch (e) { this.notify(e.message); }
      ev.target.value = "";
    },
    clearCV() { this.cv = null; store.del("cv"); this.notify("CV eliminado de este navegador"); },
    async wipeAll() {
      store.clearAll();
      try { await api("/api/settings", { method: "POST", body: { clear_api_key: true } }); } catch (_) { /* ignore */ }
      this.notify("Datos borrados"); setTimeout(() => window.location.reload(), 600);
    },
    async logout() { try { await api("/api/logout", { method: "POST" }); } catch (_) { /* */ } window.location.href = "/login"; },

    // ---------- profiles (browser) ----------
    newSearch() {
      // logo click: back to a clean search form (saved data, history and results are kept)
      this.profileId = ""; this.profileName = "";
      this.form = emptyForm(this.meta.defaults);
      this.form.sources = this.defaultSources();
      if (!this.searching) this.status = null;
      this.sel = null; this.tab = "buscar";
      window.scrollTo({ top: 0, behavior: "smooth" });
    },
    loadProfile() {
      const pr = this.profiles.find(p => String(p.id) === String(this.profileId));
      if (!pr) { this.form = emptyForm(this.meta.defaults); this.profileName = ""; return; }
      this.form = emptyForm({ ...this.meta.defaults, ...pr.params });
      this.profileName = pr.name;
    },
    saveProfile() {
      const name = this.profileName.trim().slice(0, 80);
      if (!name) return this.notify("Escribe un nombre para el perfil");
      const params = formToParams(this.form);
      let pr = this.profiles.find(p => p.name === name);
      if (pr) pr.params = params;
      else { pr = { id: Date.now(), name, params }; this.profiles.push(pr); }
      this.profiles.sort((a, b) => a.name.localeCompare(b.name));
      store.set("profiles", this.profiles);
      this.profileId = String(pr.id);
      this.notify("Perfil guardado");
    },
    deleteProfile() {
      this.profiles = this.profiles.filter(p => String(p.id) !== String(this.profileId));
      store.set("profiles", this.profiles); this.profileId = ""; this.profileName = ""; this.notify("Perfil eliminado");
    },

    // ---------- search ----------
    loadRuns() { this.runs = store.get("runs", []).map(r => ({ id: r.id, started: r.started, keywords: r.keywords, kept: r.results.length })); },
    saveRun(run) {
      let runs = store.get("runs", []).filter(r => r.id !== run.id);
      runs.unshift(run); runs = runs.slice(0, MAX_RUNS);
      // drop the oldest runs while the browser storage is full
      while (!store.set("runs", runs)) {
        if (runs.length <= 1) { this.notify("El almacenamiento del navegador está lleno: esta búsqueda no se guardará."); break; }
        runs.pop();
      }
      this.loadRuns();
    },
    async startSearch() {
      if (this.searching) return;
      const params = formToParams(this.form);
      this.searching = true;
      this.status = { phase: "buscando", sources: Object.fromEntries((params.sources || []).map(k => [k, { status: "pendiente", count: 0, message: "" }])), error: null };
      try {
        let done = null;
        await streamNdjson("/api/search", params, ev => {
          if (ev.type === "phase") this.status.phase = ev.phase;
          else if (ev.type === "source") this.status.sources[ev.source] = { status: ev.status, count: ev.count, message: ev.message };
          else if (ev.type === "error") { this.status.phase = "error"; this.status.error = ev.message; }
          else if (ev.type === "done") done = ev;
        });
        if (!done) throw new Error(this.status.error || "La búsqueda no terminó. Intenta de nuevo.");
        this.finishSearch(done, params);
      } catch (e) {
        this.status.phase = "error"; this.status.error = e.message; this.notify(e.message);
      } finally { this.searching = false; }
    },
    finishSearch(done, params) {
      const seen = new Set(store.get("seen", []));
      let results = done.results.map(r => ({ ...r, is_new: !seen.has(r.id) }));
      const summary = { ...done.summary, sources: this.status.sources, new: results.filter(r => r.is_new).length };
      let hidden = []; // offers hidden as already seen; "Mostrarlas" brings them back
      if (params.only_new) {
        hidden = results.filter(r => !r.is_new);
        results = results.filter(r => r.is_new);
        if (hidden.length) summary.rejected = { ...summary.rejected, "Ya vista en búsquedas anteriores": hidden.length };
      }
      summary.kept = results.length;
      const allSeen = [...seen, ...done.results.map(r => r.id).filter(id => !seen.has(id))];
      store.set("seen", allSeen.slice(-MAX_SEEN));
      const run = {
        id: Date.now(), started: new Date().toLocaleString("es-PE", { dateStyle: "short", timeStyle: "short" }),
        keywords: String(params.keywords).split(",").map(s => s.trim()).filter(Boolean), params, summary, results, hidden,
      };
      this.saveRun(run);
      this.status.phase = "listo";
      this.openRun(run.id, run);
      this.notify(`${results.length} ofertas encontradas`);
      if (params.ai_match && this.canUseAI()) this.runAI();
    },
    openRun(id, run) {
      run = run || store.get("runs", []).find(r => r.id === id);
      if (!run) return this.notify("Esa búsqueda ya no está guardada en este navegador");
      this.runId = id; this.results = this.mergeLocal(run.results); this.summary = run.summary; this.sel = null;
      this.qf.sources = []; this.qf.modalities = []; this.qf.statuses = [];
      this.tab = "resultados";
    },
    showSeen() {
      const run = store.get("runs", []).find(r => r.id === this.runId);
      if (!run?.hidden?.length) return this.notify("Esa información ya no está disponible");
      this.results = this.mergeLocal([...run.results, ...run.hidden]);
      const rej = { ...this.summary.rejected }; delete rej["Ya vista en búsquedas anteriores"];
      this.summary = { ...this.summary, rejected: rej };
    },
    deleteRun(id) {
      store.set("runs", store.get("runs", []).filter(r => r.id !== id)); this.loadRuns();
      if (this.runId === id) { this.runId = null; this.results = []; this.summary = null; }
    },
    get resultSources() { return [...new Set(this.results.map(r => r.source))]; },
    get filtered() {
      const q = this.qf, t = q.text.trim().toLowerCase();
      const out = this.results.filter(j =>
        (!q.sources.length || q.sources.includes(j.source)) &&
        (!q.modalities.length || q.modalities.includes(j.modality)) &&
        (!q.statuses.length || q.statuses.includes(j.status)) &&
        (!q.onlyNew || j.is_new) &&
        (!q.withSalary || j.salary_min || j.salary_max) &&
        (!q.hideDiscarded || j.status !== "descartada" || q.statuses.includes("descartada")) &&
        (!t || `${j.title} ${j.company} ${j.location} ${j.description}`.toLowerCase().includes(t)));
      const key = { score: j => j.score, ai: j => j.ai_score ?? -1, date: j => j.date_posted || "", salary: j => j.salary_max || j.salary_min || 0 }[q.sort];
      return out.sort((a, b) => (key(b) > key(a) ? 1 : key(b) < key(a) ? -1 : b.score - a.score));
    },
    select(j) { this.sel = j; },
    highlight(text) {
      // escape first, then add our own <mark> tags: scraped HTML is never interpreted
      let html = escapeHtml(text || "Sin descripción disponible. Abre la oferta para ver el detalle.");
      const params = formToParams(this.form);
      const terms = [...String(params.must_have).split(","), ...String(params.nice_to_have).split(",")].map(s => s.trim()).filter(Boolean);
      const bad = String(params.exclude).split(",").map(s => s.trim()).filter(Boolean);
      const wrap = (list, cls) => {
        if (!list.length) return;
        const re = new RegExp(`(?<![\\wÀ-ÿ])(${list.map(accentRegex).join("|")})(?![\\wÀ-ÿ])`, "gi");
        html = html.replace(re, `<mark class="${cls}">$1</mark>`);
      };
      wrap(terms, "good"); wrap(bad, "bad");
      return html.replace(/\n/g, "<br>");
    },
    setStatus(j, st) {
      j.status = st; this.saveJob(j, { status: st, notes: j.notes || "" });
      const r = this.results.find(x => x.id === j.id); if (r) r.status = st;
      if (this.tab === "mis") this.loadTracked();
    },
    saveNotes(j) { this.saveJob(j, { status: j.status, notes: (j.notes || "").slice(0, 5000) }); this.notify("Nota guardada"); },
    loadTracked() {
      const all = this.jobData();
      this.tracked = Object.entries(all).filter(([, d]) => d.status && d.status !== "nueva").map(([id, d]) => ({ id, ...d }));
    },

    // ---------- export (server builds the file from the rows we send; nothing is stored) ----------
    async exportRows(fmt) {
      try {
        const rows = this.filtered.map(r => ({ ...r, description: undefined }));
        const res = await request("/api/export", { method: "POST", body: { rows, fmt } });
        if (!res.ok) throw new Error(`Error ${res.status}`);
        const blob = await res.blob();
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob); a.download = `jobradar.${fmt}`;
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(a.href), 2000);
      } catch (e) { this.notify(e.message); }
    },

    // ---------- AI ----------
    canUseAI() { return !!(this.settings.has_api_key && this.cv && this.cv.text); },
    async runAI() {
      if (!this.cv) return this.notify("Primero sube tu CV en Configuración.");
      if (!this.settings.has_api_key) return this.notify("Configura tu API key en Configuración.");
      const model = this.settings.ai_model;
      const top = [...this.results].sort((a, b) => b.score - a.score).slice(0, this.settings.ai_top_n || 30);
      const todo = top.filter(j => !(j.ai && j.ai_model === model));
      if (!todo.length) { this.qf.sort = "ai"; return this.notify("Estas ofertas ya fueron analizadas con este modelo."); }
      this.aiRunning = true; this.aiProgress = { done: 0, total: todo.length, errors: 0, lastError: null };
      if (this.status) this.status.phase = "ia";
      try {
        const jobs = todo.map(j => ({ id: j.id, title: j.title, company: j.company, location: j.location, modality: j.modality, description: (j.description || "").slice(0, 6000) }));
        await streamNdjson("/api/ai", { cv_text: this.cv.text, jobs }, ev => {
          if (ev.type === "result" || ev.type === "error") this.aiProgress.done++;
          if (ev.type === "result") {
            const j = this.results.find(r => r.id === ev.id);
            if (j) { j.ai = ev.ai; j.ai_score = ev.ai.score; j.ai_model = model; this.saveJob(j, { status: j.status, notes: j.notes, ai: ev.ai, ai_model: model }); }
          } else if (ev.type === "error") { this.aiProgress.errors++; this.aiProgress.lastError = ev.message; }
          else if (ev.type === "fatal") { this.aiProgress.errors = this.aiProgress.total; this.aiProgress.lastError = ev.message; }
        });
        this.qf.sort = "ai";
        const p = this.aiProgress;
        this.notify(p.lastError && p.errors >= p.total ? `IA: ${p.lastError}` : p.errors ? `IA lista (${p.errors} con error: ${p.lastError})` : "Análisis IA completado");
      } catch (e) { this.notify(e.message); }
      finally { this.aiRunning = false; if (this.status && this.status.phase === "ia") this.status.phase = "listo"; }
    },
  };
};
