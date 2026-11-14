"""API tests for the stateless app: fake sources (no network) and fake AI clients."""
import io
import json
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import config, security
from app.engine import ai_match
from app.sources.base import JobPosting, Source, SourceError

CSRF = {"X-JobRadar": "1"}


class FakeSource(Source):
    def __init__(self, key, jobs=None, error=None):
        self.key, self.label, self._jobs, self._error = key, key.title(), jobs or [], error

    def search(self, q, progress):
        if self._error:
            raise SourceError(self._error)
        progress(len(self._jobs), "ok")
        return [JobPosting(**j) for j in self._jobs]


def mk(source, eid, title, company, desc="Analista con SQL, Power BI y Python. " * 4, **kw):
    base = dict(source=source, external_id=eid, title=title, url=f"https://{source}.example/{eid}", company=company,
                location="Lima", description=desc, date_posted=date.today())
    base.update(kw)
    return base


FAKE = {
    "indeed": FakeSource("indeed", [mk("indeed", "1", "Analista de Datos", "ACME", modality_text="Remoto"),
                                     mk("indeed", "2", "Ejecutivo de Ventas", "Ventas SA", desc="Ventas y comisiones " * 10),
                                     mk("indeed", "x", "Analista de Datos XSS", "Evil", url="javascript:alert(1)")]),
    "linkedin": FakeSource("linkedin", [mk("linkedin", "9", "Analista de Datos", "ACME"),  # duplicate of indeed/1
                                         mk("linkedin", "3", "Analista BI Jr", "Banco X")]),
    "computrabajo": FakeSource("computrabajo", error="bloqueado"),
}
PARAMS = {"keywords": "Analista de datos, Analista BI", "location": "Lima", "sources": ["indeed", "linkedin", "computrabajo"],
          "exclude": "comisiones", "nice_to_have": "Power BI, Python", "hours_old": 168}


@pytest.fixture()
def client(monkeypatch):
    import app.engine.search as search_mod
    import app.main as main
    import app.sources.registry as registry

    for mod in (search_mod, main, registry):
        monkeypatch.setattr(mod, "SOURCES", FAKE)
    monkeypatch.setattr(config, "AUTH_REQUIRED", False)
    for v in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(v, raising=False)
    return TestClient(main.app, headers=CSRF)


def ndjson(resp):
    return [json.loads(line) for line in resp.text.splitlines() if line.strip()]


# ---------- search ----------
def test_search_stream(client):
    assert client.get("/").status_code == 200
    r = client.post("/api/search", json=PARAMS)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/x-ndjson")
    events = ndjson(r)
    assert events[0]["type"] == "phase"
    src = {e["source"]: e for e in events if e["type"] == "source" and e["status"] != "buscando"}
    assert src["computrabajo"]["status"] == "error" and src["indeed"]["status"] == "ok"
    done = events[-1]
    assert done["type"] == "done"
    titles = [x["title"] for x in done["results"]]
    assert "Ejecutivo de Ventas" not in titles          # excluded word
    assert "Analista de Datos XSS" not in titles        # javascript: URL dropped
    assert len(done["results"]) == 2                    # cross-portal duplicate merged
    assert all(x["url"].startswith("https://") for x in done["results"])
    merged = next(x for x in done["results"] if x["company"] == "ACME")
    assert merged["also_on"] in (["linkedin"], ["indeed"])
    assert done["summary"]["duplicates"] == 1 and done["summary"]["rejected"]


def test_search_validation(client):
    assert client.post("/api/search", json={"keywords": "", "sources": ["indeed"]}).status_code == 400
    assert client.post("/api/search", json={"keywords": "x", "sources": ["nope"]}).status_code == 400
    r = client.post("/api/search", json={**PARAMS, "keywords": "a,b,c,d,e,f"})
    assert r.status_code == 422 and "Máximo 5" in r.json()["detail"]
    assert client.post("/api/search", json={**PARAMS, "min_score": 500}).status_code == 422


def test_search_rate_limit(client, monkeypatch):
    monkeypatch.setattr(config, "SEARCHES_PER_HOUR", 2)
    assert client.post("/api/search", json=PARAMS).status_code == 200
    assert client.post("/api/search", json=PARAMS).status_code == 200
    assert client.post("/api/search", json=PARAMS).status_code == 429


# ---------- security ----------
def test_security_headers(client):
    h = client.get("/").headers
    csp = h["content-security-policy"]
    assert "script-src 'self' 'unsafe-eval'" in csp and "frame-ancestors 'none'" in csp and "'unsafe-inline'" not in csp
    assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY"
    assert h["referrer-policy"] == "no-referrer" and h["cache-control"] == "no-store"
    assert client.get("/docs").status_code == 404 and client.get("/openapi.json").status_code == 404


def test_csrf_protection(client):
    import app.main as main

    bare = TestClient(main.app)  # no custom header
    assert bare.post("/api/search", json=PARAMS).status_code == 403
    assert bare.post("/api/settings", json={}).status_code == 403
    assert client.post("/api/settings", json={}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/settings", json={}, headers={"Origin": "http://testserver"}).status_code == 200


def test_body_size_limit(client):
    big = {"cv_text": "x" * (config.MAX_JSON_BYTES + 10), "jobs": []}
    assert client.post("/api/ai", json=big).status_code == 413


def test_auth_flow(client, monkeypatch):
    monkeypatch.setattr(config, "AUTH_REQUIRED", True)
    monkeypatch.setattr(config, "APP_PASSWORD", "una-clave-muy-segura")
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert client.get("/login").status_code == 200
    assert client.get("/api/meta").status_code == 401
    assert client.post("/api/search", json=PARAMS).status_code == 401
    assert client.post("/api/login", json={"password": "mala"}).status_code == 401
    r = client.post("/api/login", json={"password": "una-clave-muy-segura"})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert client.get("/api/meta").status_code == 200
    assert client.post("/api/logout").status_code == 200
    assert client.get("/api/meta").status_code == 401


def test_login_bruteforce_limited(client, monkeypatch):
    monkeypatch.setattr(config, "AUTH_REQUIRED", True)
    monkeypatch.setattr(config, "APP_PASSWORD", "una-clave-muy-segura")
    codes = [client.post("/api/login", json={"password": f"x{i}"}).status_code for i in range(7)]
    assert codes[:5] == [401] * 5 and codes[5:] == [429, 429]
    assert client.post("/api/login", json={"password": "una-clave-muy-segura"}).status_code == 429


def test_production_requires_secrets(monkeypatch):
    import importlib

    monkeypatch.setenv("ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "short")
    monkeypatch.setenv("APP_PASSWORD", "una-clave-muy-segura")
    with pytest.raises(RuntimeError):
        importlib.reload(config)
    monkeypatch.setenv("SECRET_KEY", "s" * 40)
    monkeypatch.setenv("APP_PASSWORD", "corta")
    with pytest.raises(RuntimeError):
        importlib.reload(config)
    monkeypatch.setenv("APP_PASSWORD", "una-clave-muy-segura")
    importlib.reload(config)
    assert config.PRODUCTION and config.AUTH_REQUIRED
    monkeypatch.setenv("ENV", "development")
    monkeypatch.delenv("SECRET_KEY")
    monkeypatch.delenv("APP_PASSWORD")
    importlib.reload(config)


# ---------- AI key cookie ----------
def test_api_key_cookie_is_encrypted(client):
    s = client.get("/api/settings").json()
    assert s["ai_provider"] == "gemini" and not s["has_api_key"]
    secret = "AIzaSyTEST1234567890abcdefgh"
    r = client.post("/api/settings", json={"api_key": secret, "ai_model": "gemini-3.5-flash-lite", "ai_top_n": 10})
    assert r.status_code == 200
    body = r.json()
    assert body["has_api_key"] and secret not in r.text and body["ai_model"] == "gemini-3.5-flash-lite"
    set_cookie = r.headers["set-cookie"]
    assert security.AI_COOKIE in set_cookie and "httponly" in set_cookie.lower() and "samesite=strict" in set_cookie.lower()
    assert secret not in set_cookie and "1234567890" not in set_cookie
    assert client.get("/api/settings").json()["has_api_key"]
    assert client.post("/api/settings", json={"api_key": "sk-ant-xyz"}).status_code == 400
    assert client.post("/api/settings", json={"ai_model": "claude-opus-5-5"}).status_code == 400
    s = client.post("/api/settings", json={"ai_provider": "claude"}).json()
    assert s["ai_provider"] == "claude" and not s["has_api_key"] and s["ai_model"] == "claude-opus-5-5"
    assert client.post("/api/settings", json={"ai_provider": "gemini"}).json()["has_api_key"]
    assert not client.post("/api/settings", json={"clear_api_key": True}).json()["has_api_key"]


def test_tampered_cookie_is_ignored(client):
    client.cookies.set(security.AI_COOKIE, "gAAAAABfake-token")
    assert client.get("/api/settings").json()["has_api_key"] is False


# ---------- CV (not stored) ----------
CV = ("Analista de datos con 3 años de experiencia en SQL, Power BI y Python. " * 5).encode()


def test_cv_upload_returns_text_only(client):
    assert client.post("/api/cv", files={"file": ("cv.txt", b"corto", "text/plain")}).status_code == 400
    assert client.post("/api/cv", files={"file": ("cv.exe", b"x" * 200, "application/octet-stream")}).status_code == 400
    r = client.post("/api/cv", files={"file": ("cv.txt", CV, "text/plain")}).json()
    assert r["cv_filename"] == "cv.txt" and "Power BI" in r["cv_text"] and not r["truncated"]


# ---------- AI streaming ----------
def _fake_result(text):
    return ai_match.AIMatch(score=88 if "Analista de Datos" in text else 40, strengths=["SQL"], gaps=[], pitch="Destaca tu SQL")


class FakeGeminiModels:
    def __init__(self):
        self.calls = []

    def generate_content(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(parsed=_fake_result(kw["contents"]), text=None)


AI_JOBS = [{"id": "a1", "title": "Analista de Datos", "company": "ACME", "description": "SQL"},
           {"id": "a2", "title": "Asistente", "company": "B", "description": "Archivo"}]


def test_ai_requires_key(client):
    r = client.post("/api/ai", json={"cv_text": CV.decode(), "jobs": AI_JOBS})
    assert r.status_code == 400 and "API key" in r.json()["detail"]


def test_ai_stream_gemini(client, monkeypatch):
    models = FakeGeminiModels()
    monkeypatch.setattr(ai_match, "make_client", lambda provider, key: SimpleNamespace(models=models))
    client.post("/api/settings", json={"api_key": "AIzaSyTEST1234567890abcdefgh"})
    r = client.post("/api/ai", json={"cv_text": CV.decode(), "jobs": AI_JOBS})
    assert r.status_code == 200, r.text
    ev = ndjson(r)
    assert ev[0] == {"type": "start", "total": 2, "model": "gemini-3.8-flash"}
    assert {e["id"]: e["ai"]["score"] for e in ev if e["type"] == "result"} == {"a1": 88, "a2": 40}
    cfg = models.calls[0]["config"]
    assert cfg.response_schema is ai_match.AIMatch and "CV DEL CANDIDATO" in cfg.system_instruction
    assert client.post("/api/ai", json={"cv_text": "corto", "jobs": AI_JOBS}).status_code == 422
    assert client.post("/api/ai", json={"cv_text": CV.decode(), "jobs": AI_JOBS * 30}).status_code == 422


def test_ai_stream_reports_errors(client, monkeypatch):
    def boom(**kw):
        raise RuntimeError("400 INVALID_ARGUMENT. API key not valid. Please pass a valid API key.")

    monkeypatch.setattr(ai_match, "make_client", lambda p, k: SimpleNamespace(models=SimpleNamespace(generate_content=boom)))
    client.post("/api/settings", json={"api_key": "AIzaSyTEST1234567890abcdefgh"})
    ev = ndjson(client.post("/api/ai", json={"cv_text": CV.decode(), "jobs": AI_JOBS}))
    errs = [e for e in ev if e["type"] == "error"]
    assert len(errs) == 2 and "API key inválida" in errs[0]["message"]


def test_gemini_rate_limit_retry():
    calls = {"n": 0}

    def flaky(**kw):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("429 RESOURCE_EXHAUSTED. Quota exceeded")
        return SimpleNamespace(parsed=_fake_result("Analista de Datos"), text=None)

    fake = SimpleNamespace(models=SimpleNamespace(generate_content=flaky))
    m = ai_match.match_one(fake, "gemini", "gemini-3.8-flash", "cv", {"title": "x"}, retry_waits=(0, 0, 0))
    assert m.score == 88 and calls["n"] == 3


def test_gemini_json_text_fallback():
    fake = SimpleNamespace(models=SimpleNamespace(generate_content=lambda **kw: SimpleNamespace(
        parsed=None, text='{"score": 120, "strengths": ["a"], "gaps": [], "pitch": "p"}')))
    assert ai_match.match_one(fake, "gemini", "gemini-3.8-flash", "cv", {"title": "x"}).score == 100


def test_claude_provider_call_shape():
    calls = []

    def parse(**kw):
        calls.append(kw)
        return SimpleNamespace(stop_reason="end_turn", parsed_output=_fake_result(kw["messages"][0]["content"]))

    msgs = SimpleNamespace(parse=parse)
    fake = SimpleNamespace(beta=SimpleNamespace(messages=msgs), messages=msgs)
    m = ai_match.match_one(fake, "claude", "claude-opus-5-5", "cv", {"title": "Analista de Datos"})
    assert m.score == 88 and calls[0]["fallbacks"] == "default" and calls[0]["output_format"] is ai_match.AIMatch


# ---------- export ----------
def test_export_neutralizes_formulas_and_bad_links(client):
    rows = [{"score": 90, "title": "=HYPERLINK(\"http://evil\",\"x\")", "company": "+cmd", "url": "javascript:alert(1)",
             "reasons": ["a", "b"], "salary_min": 2200, "salary_currency": "PEN", "salary_period": "monthly"},
            {"score": 80, "title": "Analista", "company": "ACME", "url": "https://ok.example/1"}]
    x = client.post("/api/export", json={"rows": rows, "fmt": "xlsx"})
    assert x.status_code == 200 and x.content[:2] == b"PK"
    from openpyxl import load_workbook

    ws = load_workbook(io.BytesIO(x.content)).active
    assert ws.max_row == 3
    assert ws.cell(2, 1).value.startswith("'=") and ws.cell(2, 3).value == "'+cmd"
    assert ws.cell(2, 14).value in (None, "") and ws.cell(3, 14).value == "https://ok.example/1"
    assert ws.cell(2, 7).value == "PEN 2,200/mes"
    assert "Puntaje" not in [c.value for c in ws[1]]
    c = client.post("/api/export", json={"rows": rows, "fmt": "csv"})
    assert "Puesto" in c.content.decode("utf-8-sig").splitlines()[0]


def test_meta_and_health(client):
    m = client.get("/api/meta").json()
    assert "gemini" in m["ai_providers"] and m["limits"]["max_keywords"] == 5
    assert "env" not in m["ai_providers"]["gemini"]
    assert client.get("/healthz").json() == {"ok": True}


def test_disabled_sources_are_reported_not_searched(monkeypatch):
    from app import config
    from app.engine.params import SearchParams
    from app.engine.search import run_source
    from app.sources.registry import SOURCES

    monkeypatch.setattr(config, "DISABLED_SOURCES", {"bumeran"})
    events = []
    assert run_source("bumeran", SearchParams(keywords=["x"]), events.append) == []
    assert events[-1]["status"] == "no_disponible" and "run.bat" in events[-1]["message"]
    assert SOURCES["bumeran"].unavailable("peru") == config.DISABLED_SOURCE_MSG
    assert SOURCES["indeed"].unavailable("peru") is None
