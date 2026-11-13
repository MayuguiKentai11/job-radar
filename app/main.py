"""JobRadar — stateless FastAPI app. No database: secrets live in an encrypted HttpOnly cookie,
access in a signed session cookie, and results/statuses/profiles in the user's own browser."""
from __future__ import annotations

import csv
import io
import json
import logging
import threading
import time
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from app import config, security
from app.engine import ai_match
from app.engine.params import JOB_TYPES, LEVELS, MODALITIES, SearchParams
from app.engine.search import stream_search
from app.sources.registry import SOURCES

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
for noisy in ("httpx", "httpcore", "google_genai", "anthropic", "jobspy"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
log = logging.getLogger("jobradar")

BASE = Path(__file__).resolve().parent
app = FastAPI(title="JobRadar", docs_url=None, redoc_url=None, openapi_url=None)
# Order matters: SessionMiddleware must wrap the app so request.session is available inside security checks.
app.add_middleware(security.SecurityMiddleware)
app.add_middleware(
    SessionMiddleware, secret_key=config.SECRET_KEY, session_cookie=security.SESSION_COOKIE,
    max_age=config.SESSION_MAX_AGE, same_site="strict", https_only=config.PRODUCTION,
)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")

_search_slots = threading.BoundedSemaphore(config.MAX_CONCURRENT_SEARCHES)
_active_by_ip: dict[str, int] = {}
_active_lock = threading.Lock()


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    msgs = []
    for err in exc.errors():
        m = str(err.get("msg", "Dato inválido")).removeprefix("Value error, ")
        field = ".".join(str(x) for x in err.get("loc", [])[1:])
        msgs.append(f"{field}: {m}" if field else m)
    return JSONResponse({"detail": "; ".join(msgs)[:500]}, status_code=422)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("unhandled error on %s", request.url.path)
    return JSONResponse({"detail": "Error interno."}, status_code=500)


# ---------- pages & auth ----------
@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    if not security.is_authenticated(request):
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "index.html", {"auth_required": config.AUTH_REQUIRED})


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if security.is_authenticated(request):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {})


class LoginIn(BaseModel):
    password: str = Field(max_length=200)


@app.post("/api/login")
def login(body: LoginIn, request: Request):
    security.rate_limit(request, "login", config.LOGIN_ATTEMPTS_PER_15MIN, 15 * 60)
    if not config.AUTH_REQUIRED or security.check_password(body.password):
        request.session.clear()
        request.session.update({"auth": True, "exp": time.time() + config.SESSION_MAX_AGE})
        return {"ok": True}
    time.sleep(0.5)  # slow down guessing
    raise HTTPException(401, "Contraseña incorrecta.")


@app.post("/api/logout")
def logout(request: Request):
    request.session.clear()
    resp = JSONResponse({"ok": True})
    security.clear_ai_cookie(resp)
    return resp


auth = Depends(security.require_auth)


# ---------- meta / AI settings (key kept in an encrypted HttpOnly cookie) ----------
@app.get("/api/meta", dependencies=[auth])
def meta(country: str = "peru"):
    return {
        "sources": [
            {"key": s.key, "label": s.label, "experimental": s.experimental, "unavailable": s.availability(country)}
            for s in SOURCES.values()
        ],
        "modalities": MODALITIES, "levels": LEVELS, "job_types": JOB_TYPES,
        "statuses": ["nueva", "interesante", "postulada", "entrevista", "descartada"],
        "defaults": SearchParams().model_dump(),
        "ai_providers": {k: {kk: v[kk] for kk in ("label", "key_prefix", "key_url", "models")} for k, v in ai_match.PROVIDERS.items()},
        "limits": {"max_keywords": config.MAX_KEYWORDS, "max_per_source": config.MAX_PER_SOURCE, "max_ai_jobs": config.MAX_AI_JOBS},
        "auth_required": config.AUTH_REQUIRED,
    }


def _ai_settings(request: Request) -> dict:
    data = security.decode_ai_cookie(request)
    provider = data.get("provider") if data.get("provider") in ai_match.PROVIDERS else ai_match.DEFAULT_PROVIDER
    keys = data.get("keys") or {}
    models = data.get("models") or {}
    model = models.get(provider) if models.get(provider) in ai_match.PROVIDERS[provider]["models"] else ai_match.default_model(provider)
    return {"provider": provider, "keys": keys, "models": models, "model": model, "top_n": int(data.get("top_n") or 30)}


def _public_settings(s: dict) -> dict:
    key = s["keys"].get(s["provider"])
    return {
        "ai_provider": s["provider"], "ai_model": s["model"], "ai_top_n": s["top_n"],
        "has_api_key": bool(key), "api_key_hint": (key[:4] + "…" + key[-4:]) if key else None,
    }


@app.get("/api/settings", dependencies=[auth])
def get_settings(request: Request):
    return _public_settings(_ai_settings(request))


class SettingsIn(BaseModel):
    ai_provider: Optional[str] = None
    api_key: Optional[str] = Field(None, max_length=300)
    ai_model: Optional[str] = None
    ai_top_n: Optional[int] = Field(None, ge=5, le=config.MAX_AI_JOBS)
    clear_api_key: bool = False


@app.post("/api/settings", dependencies=[auth])
def save_settings(body: SettingsIn, request: Request):
    s = _ai_settings(request)
    if body.ai_provider:
        if body.ai_provider not in ai_match.PROVIDERS:
            raise HTTPException(400, "Proveedor no válido")
        s["provider"] = body.ai_provider
    prov = s["provider"]
    if body.clear_api_key:
        s["keys"].pop(prov, None)
    elif body.api_key and body.api_key.strip():
        key = body.api_key.strip()
        if not key.startswith(ai_match.PROVIDERS[prov]["key_prefix"]) or any(c.isspace() for c in key):
            raise HTTPException(400, f"La key no tiene el formato de {ai_match.PROVIDERS[prov]['label']}.")
        s["keys"][prov] = key
    if body.ai_model:
        if body.ai_model not in ai_match.PROVIDERS[prov]["models"]:
            raise HTTPException(400, "Modelo no válido para este proveedor")
        s["models"][prov] = body.ai_model
    if not s["models"].get(prov):
        s["models"][prov] = ai_match.default_model(prov)
    s["model"] = s["models"][prov]
    if body.ai_top_n:
        s["top_n"] = body.ai_top_n
    resp = JSONResponse(_public_settings(s))
    security.set_ai_cookie(resp, {"provider": prov, "keys": s["keys"], "models": s["models"], "top_n": s["top_n"]})
    return resp


@app.post("/api/cv", dependencies=[auth])
async def upload_cv(request: Request, file: UploadFile = File(...)):
    """Extract text from the CV and return it. Nothing is stored on the server."""
    security.rate_limit(request, "cv", 20, 3600)
    data = await file.read(config.MAX_UPLOAD_BYTES + 1)
    if len(data) > config.MAX_UPLOAD_BYTES:
        raise HTTPException(413, "El archivo supera 5 MB")
    try:
        text = ai_match.extract_cv_text(file.filename or "", data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception:
        raise HTTPException(400, "No se pudo leer el archivo.")
    if len(text) < 100:
        raise HTTPException(400, "No se pudo extraer texto del CV (¿es un PDF escaneado como imagen?).")
    return {"cv_text": text[: config.MAX_CV_CHARS], "cv_filename": (file.filename or "cv")[:120], "truncated": len(text) > config.MAX_CV_CHARS}


# ---------- search (streamed NDJSON) ----------
def _ndjson(gen):
    for ev in gen:
        yield json.dumps(ev, ensure_ascii=False, default=str) + "\n"


@app.post("/api/search", dependencies=[auth])
def search(params: SearchParams, request: Request):
    if not params.keywords:
        raise HTTPException(400, "Ingresa al menos un cargo o palabra clave.")
    if not params.sources:
        raise HTTPException(400, "Selecciona al menos un portal.")
    ip = security.client_ip(request)
    with _active_lock:
        if _active_by_ip.get(ip, 0) >= 1:
            raise HTTPException(429, "Ya tienes una búsqueda en curso.")
        security.rate_limit(request, "search", config.SEARCHES_PER_HOUR, 3600)
        if not _search_slots.acquire(blocking=False):
            raise HTTPException(503, "El servidor está ocupado con otras búsquedas. Intenta en un minuto.")
        _active_by_ip[ip] = _active_by_ip.get(ip, 0) + 1

    def release():
        with _active_lock:
            _active_by_ip[ip] = max(0, _active_by_ip.get(ip, 1) - 1)
            if not _active_by_ip[ip]:
                del _active_by_ip[ip]
        _search_slots.release()

    return StreamingResponse(_ndjson(stream_search(params, on_finish=release)), media_type="application/x-ndjson",
                             headers={"X-Accel-Buffering": "no"})


# ---------- AI matching (streamed NDJSON; CV and offers come from the browser) ----------
class AIJob(BaseModel):
    id: str = Field(max_length=64)
    title: str = Field(max_length=300)
    company: Optional[str] = Field(None, max_length=200)
    location: Optional[str] = Field(None, max_length=200)
    modality: Optional[str] = Field(None, max_length=30)
    description: Optional[str] = Field(None, max_length=20_000)


class AIRequest(BaseModel):
    cv_text: str = Field(min_length=100, max_length=config.MAX_CV_CHARS)
    jobs: list[AIJob] = Field(min_length=1, max_length=config.MAX_AI_JOBS)


@app.post("/api/ai", dependencies=[auth])
def ai(body: AIRequest, request: Request):
    s = _ai_settings(request)
    key = s["keys"].get(s["provider"]) or ai_match.env_key(s["provider"])
    if not key:
        raise HTTPException(400, "Configura tu API key en Configuración.")
    security.rate_limit(request, "ai", config.AI_REQUESTS_PER_HOUR, 3600)
    jobs = [j.model_dump() for j in body.jobs]
    events: list = []
    cond = threading.Condition()

    def progress_cb(jid, res):
        with cond:
            events.append(jid_res_event(jid, res))
            cond.notify()

    def jid_res_event(jid, res):
        if isinstance(res, str):
            return {"type": "error", "id": jid, "message": res}
        return {"type": "result", "id": jid, "ai": res.model_dump()}

    def run():
        try:
            client = ai_match.make_client(s["provider"], key)
            ai_match.match_many(body.cv_text, jobs, s["provider"], key, s["model"], client=client, on_result=progress_cb)
        except Exception as e:
            with cond:
                events.append({"type": "fatal", "message": ai_match.friendly_error(e)})
        finally:
            with cond:
                events.append(None)
                cond.notify()

    threading.Thread(target=run, daemon=True).start()

    def gen():
        yield {"type": "start", "total": len(jobs), "model": s["model"]}
        i = 0
        while True:
            with cond:
                while i >= len(events):
                    if not cond.wait(timeout=15):
                        break
                batch = events[i:]
                i = len(events)
            if not batch:
                yield {"type": "ping"}
                continue
            for ev in batch:
                if ev is None:
                    return
                yield ev

    return StreamingResponse(_ndjson(gen()), media_type="application/x-ndjson", headers={"X-Accel-Buffering": "no"})


# ---------- export (stateless: the browser posts the rows it has) ----------
class ExportRow(BaseModel):
    model_config = {"extra": "ignore"}
    score: Optional[int] = None
    ai_score: Optional[int] = None
    title: str = Field("", max_length=300)
    company: Optional[str] = Field(None, max_length=200)
    location: Optional[str] = Field(None, max_length=200)
    modality: Optional[str] = None
    level: Optional[str] = None
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    salary_currency: Optional[str] = None
    salary_period: Optional[str] = None
    date_posted: Optional[str] = None
    source: Optional[str] = None
    status: Optional[str] = None
    required_years: Optional[int] = None
    reasons: list[str] = Field(default_factory=list)
    notes: Optional[str] = Field(None, max_length=5000)
    url: Optional[str] = None


class ExportIn(BaseModel):
    rows: list[ExportRow] = Field(max_length=2000)
    fmt: str = "xlsx"


EXPORT_COLS = [
    ("score", "Puntaje"), ("ai_score", "Afinidad IA"), ("title", "Puesto"), ("company", "Empresa"), ("location", "Ubicación"),
    ("modality", "Modalidad"), ("level", "Nivel"), ("salary", "Salario"), ("date_posted", "Publicada"), ("source", "Portal"),
    ("status", "Estado"), ("required_years", "Años pedidos"), ("reasons", "Motivos"), ("notes", "Notas"), ("url", "Enlace"),
]


def _cell(v):
    """Neutralize spreadsheet formula injection from scraped text (=, +, -, @ …)."""
    if isinstance(v, str) and v[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + v
    return v


def _export_values(r: ExportRow) -> list:
    d = r.model_dump()
    lo, hi = r.salary_min, r.salary_max
    sal = ""
    if lo or hi:
        amounts = "–".join(f"{v:,.0f}" for v in (lo, hi) if v) if lo != hi else f"{lo:,.0f}"
        per = {"monthly": "/mes", "yearly": "/año", "hourly": "/hora", "daily": "/día", "weekly": "/semana"}.get(r.salary_period or "", "")
        sal = f"{r.salary_currency or 'PEN'} {amounts}{per}"
    d["salary"] = sal
    d["reasons"] = "; ".join(r.reasons)
    d["url"] = security.safe_url(r.url) or ""
    return [_cell(d.get(k)) if d.get(k) is not None else "" for k, _ in EXPORT_COLS]


@app.post("/api/export", dependencies=[auth])
def export(body: ExportIn):
    rows = [_export_values(r) for r in body.rows]
    headers = [h for _, h in EXPORT_COLS]
    if body.fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(headers)
        w.writerows(rows)
        data = io.BytesIO(("﻿" + buf.getvalue()).encode("utf-8"))
        return StreamingResponse(data, media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="jobradar.csv"'})
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "Ofertas"
    ws.append(headers)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F4E79")
    for row in rows:
        ws.append(row)
        link = ws.cell(row=ws.max_row, column=len(headers))
        if link.value:
            link.hyperlink = link.value
            link.style = "Hyperlink"
    for i, wdt in enumerate([9, 11, 45, 28, 22, 12, 13, 22, 12, 13, 12, 12, 50, 30, 50], start=1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = wdt
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(buf, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                             headers={"Content-Disposition": 'attachment; filename="jobradar.xlsx"'})
