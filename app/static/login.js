/* Login: password toggle, Caps Lock hint and the animated penguin that reacts to the password field. */
(() => {
  const form = document.getElementById("login");
  const pw = document.getElementById("password");
  const toggle = document.getElementById("togglePw");
  const caps = document.getElementById("caps");
  const err = document.getElementById("err");
  const btn = document.getElementById("submit");
  const label = btn.querySelector(".btn-label");
  const penguin = document.getElementById("penguin");
  const bubble = document.getElementById("bubble");

  const LINES = {
    idle: ["¡Hola! Soy Pingu, el guardián.", "Escribe la contraseña, yo vigilo 🐧", "¿Listo para buscar chamba?"],
    cover: ["¡No estoy mirando! 🙈", "Ojos cerrados, palabra de pingüino.", "Lalala… no veo nada…"],
    peek: ["Ups… solo un poquito 👀", "¡Oye, la estás mostrando!", "No le cuento a nadie 🤫"],
    error: ["Mmm… esa no es 🤔", "¡Brrr! Contraseña incorrecta.", "Casi… intenta otra vez."],
    happy: ["¡Adentro! 🎉", "¡Bienvenido! 🐟"],
  };
  const pick = (arr) => arr[Math.floor(Math.random() * arr.length)];
  let mood = "";

  function setMood(next, force = false) {
    if (next === mood && !force) return;
    mood = next;
    penguin.dataset.mood = next;
    bubble.textContent = pick(LINES[next]);
    // restart the bubble pop animation
    bubble.classList.remove("pop"); void bubble.offsetWidth; bubble.classList.add("pop");
  }

  function moodFromField() {
    // empty field = idle (even if focused); typing hidden = cover; shown = peek
    if (pw.type === "text") return "peek";
    return pw.value ? "cover" : "idle";
  }
  const refresh = () => setMood(moodFromField());

  // pupils follow the pointer while the penguin is idle
  const pupils = penguin.querySelectorAll(".pg-pupil");
  window.addEventListener("pointermove", (e) => {
    if (mood !== "idle") return;
    const r = penguin.getBoundingClientRect();
    const dx = (e.clientX - (r.left + r.width / 2)) / r.width;
    const dy = (e.clientY - (r.top + r.height * 0.35)) / r.height;
    const clamp = (v) => Math.max(-1, Math.min(1, v));
    const tx = clamp(dx) * 3.5, ty = clamp(dy) * 3;
    pupils.forEach((p) => p.setAttribute("transform", `translate(${tx.toFixed(1)} ${ty.toFixed(1)})`));
  });
  const centerPupils = () => pupils.forEach((p) => p.removeAttribute("transform"));

  // little giggle on every keystroke while covering/peeking
  pw.addEventListener("input", () => {
    err.textContent = "";
    refresh();
    penguin.classList.remove("giggle"); void penguin.getBoundingClientRect(); penguin.classList.add("giggle");
  });
  pw.addEventListener("focus", () => { centerPupils(); refresh(); });
  pw.addEventListener("blur", refresh);

  toggle.addEventListener("click", () => {
    const show = pw.type === "password";
    pw.type = show ? "text" : "password";
    toggle.textContent = show ? "Ocultar" : "Mostrar";
    toggle.setAttribute("aria-pressed", String(show));
    toggle.setAttribute("aria-label", show ? "Ocultar contraseña" : "Mostrar contraseña");
    centerPupils();
    setMood(show ? "peek" : (pw.value ? "cover" : "idle"));
    pw.focus({ preventScroll: true });
    const end = pw.value.length; try { pw.setSelectionRange(end, end); } catch (_) { /* ignore */ }
  });

  const capsCheck = (e) => { if (e.getModifierState) caps.hidden = !e.getModifierState("CapsLock"); };
  pw.addEventListener("keydown", capsCheck);
  pw.addEventListener("keyup", capsCheck);

  // tapping the penguin makes it wave (pure fun)
  penguin.parentElement.addEventListener("click", () => {
    penguin.classList.remove("wave"); void penguin.getBoundingClientRect(); penguin.classList.add("wave");
  });

  function busy(on) {
    btn.disabled = on;
    btn.classList.toggle("loading", on);
    label.textContent = on ? "Ingresando…" : "Ingresar";
  }

  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    err.textContent = "";
    if (!pw.value) {
      err.textContent = "Escribe la contraseña.";
      setMood("error", true); pw.focus();
      return;
    }
    busy(true);
    try {
      const res = await fetch("/api/login", {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-JobRadar": "1" },
        body: JSON.stringify({ password: pw.value }),
      });
      if (res.ok) {
        setMood("happy", true); label.textContent = "¡Listo!";
        setTimeout(() => { window.location.href = "/"; }, 650);
        return;
      }
      let msg = res.status === 401 ? "Contraseña incorrecta." : res.status === 429
        ? "Demasiados intentos. Espera unos minutos y vuelve a intentar." : `Error ${res.status}`;
      try { msg = (await res.json()).detail || msg; } catch (_) { /* ignore */ }
      err.textContent = msg;
      setMood("error", true);
      pw.select();
    } catch (_) {
      err.textContent = "No se pudo conectar. Si el servidor estaba dormido, espera unos segundos y reintenta.";
      setMood("error", true);
    }
    busy(false);
  });

  // after an error, go back to the field-driven mood once the shake has played
  penguin.addEventListener("animationend", (e) => {
    if (e.animationName === "pg-shake") setTimeout(refresh, 1800);
    if (e.animationName === "pg-giggle") penguin.classList.remove("giggle");
    if (e.animationName === "pg-wave") penguin.classList.remove("wave");
  });

  setMood(moodFromField(), true);
})();
