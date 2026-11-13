"""Optional CV ↔ offer matching with an LLM (Gemini by default, Claude as alternative)."""
from __future__ import annotations

import hashlib
import io
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional

from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

DEFAULT_PROVIDER = "gemini"
PROVIDERS = {
    "gemini": {
        "label": "Google Gemini",
        "env": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
        "key_prefix": "AIza",
        "key_url": "https://aistudio.google.com/apikey",
        "models": {
            "gemini-3.8-flash": "Gemini 3.8 Flash (recomendado)",
            "gemini-3.5-flash-lite": "Gemini 3.5 Flash-Lite (más barato)",
            "gemini-pro-latest": "Gemini Pro (mejor calidad, más caro)",
        },
    },
    "claude": {
        "label": "Anthropic Claude",
        "env": ["ANTHROPIC_API_KEY"],
        "key_prefix": "sk-ant-",
        "key_url": "https://console.anthropic.com/settings/keys",
        "models": {
            "claude-opus-5-5": "Claude Opus 5.5 (mejor calidad)",
            "claude-sonnet-5-5": "Claude Sonnet 5.5 (más económico)",
            "claude-haiku-5-5": "Claude Haiku 5.5 (el más barato)",
        },
    },
}
MAX_DESC_CHARS = 6000


def default_model(provider: str) -> str:
    return next(iter(PROVIDERS[provider]["models"]))


def env_key(provider: str) -> Optional[str]:
    for name in PROVIDERS[provider]["env"]:
        if os.environ.get(name):
            return os.environ[name]
    return None


class AIMatch(BaseModel):
    score: int = Field(description="Afinidad 0-100 entre el CV y la oferta")
    strengths: list[str] = Field(description="2-4 puntos fuertes concretos del candidato para esta oferta")
    gaps: list[str] = Field(description="0-3 requisitos que el CV no evidencia")
    pitch: str = Field(description="Una frase de cómo enfocar la postulación")


SYSTEM = """Eres un reclutador senior en Perú que evalúa la afinidad entre un candidato y una oferta laboral.
Evalúa con honestidad: requisitos obligatorios no cubiertos bajan mucho el puntaje; experiencia y habilidades
demostradas en el CV lo suben. Escala: 85-100 encaje excelente, 70-84 bueno, 50-69 parcial, <50 débil.
Responde en español, frases cortas y concretas basadas solo en el CV y la oferta.
Devuelve: score (0-100), strengths (2-4), gaps (0-3) y pitch (una frase).

CV DEL CANDIDATO:
<cv>
{cv}
</cv>"""


def cv_hash(cv_text: str, model: str) -> str:
    return hashlib.sha1((model + "\n" + cv_text).encode("utf-8")).hexdigest()[:16]


def extract_cv_text(filename: str, data: bytes) -> str:
    name = filename.lower()
    if name.endswith(".pdf"):
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        return "\n".join((p.extract_text() or "") for p in reader.pages).strip()
    if name.endswith(".docx"):
        import docx

        d = docx.Document(io.BytesIO(data))
        parts = [p.text for p in d.paragraphs]
        for t in d.tables:
            for row in t.rows:
                parts.append(" | ".join(c.text for c in row.cells))
        return "\n".join(parts).strip()
    if name.endswith((".txt", ".md")):
        return data.decode("utf-8", errors="ignore").strip()
    raise ValueError("Formato no soportado: sube un PDF, DOCX o TXT.")


def make_client(provider: str, api_key: Optional[str]):
    key = api_key or env_key(provider)
    if provider == "gemini":
        from google import genai

        if not key:
            raise RuntimeError("Falta la API key de Gemini.")
        return genai.Client(api_key=key)
    import anthropic

    return anthropic.Anthropic(api_key=key, max_retries=3) if key else anthropic.Anthropic(max_retries=3)


def _offer_text(job: dict) -> str:
    return (
        f"Puesto: {job.get('title')}\nEmpresa: {job.get('company') or '-'}\n"
        f"Ubicación: {job.get('location') or '-'}\nModalidad: {job.get('modality') or '-'}\n\n"
        f"Descripción:\n{(job.get('description') or 'Sin descripción (evalúa solo por el título).')[:MAX_DESC_CHARS]}"
    )


def _match_gemini(client, model: str, cv_text: str, job: dict) -> AIMatch:
    from google.genai import types

    resp = client.models.generate_content(
        model=model,
        contents=f"Evalúa esta oferta para el candidato:\n\n{_offer_text(job)}",
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM.format(cv=cv_text[:20000]),
            response_mime_type="application/json",
            response_schema=AIMatch,
        ),
    )
    parsed = getattr(resp, "parsed", None)
    if isinstance(parsed, AIMatch):
        return parsed
    if isinstance(parsed, dict):
        return AIMatch.model_validate(parsed)
    if not getattr(resp, "text", None):
        raise RuntimeError("Gemini no devolvió una evaluación (posible bloqueo de seguridad).")
    return AIMatch.model_validate_json(resp.text)


def _match_claude(client, model: str, cv_text: str, job: dict) -> AIMatch:
    import anthropic

    user = f"Evalúa esta oferta para el candidato:\n\n{_offer_text(job)}"
    try:
        resp = client.beta.messages.parse(
            model=model,
            max_tokens=4000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "low"},
            system=[{"type": "text", "text": SYSTEM.format(cv=cv_text[:20000]), "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": user}],
            output_format=AIMatch,
        )
    except anthropic.BadRequestError as e:
        if "fallback" not in str(e).lower():  # Haiku has no server-side fallback
            raise
        resp = client.messages.parse(
            model=model, max_tokens=4000, output_config={"effort": "low"},
            system=SYSTEM.format(cv=cv_text[:20000]), messages=[{"role": "user", "content": user}], output_format=AIMatch,
        )
    if resp.stop_reason == "refusal" or resp.parsed_output is None:
        raise RuntimeError("El modelo no devolvió una evaluación para esta oferta.")
    return resp.parsed_output


def _is_rate_limit(e: Exception) -> bool:
    low = str(e).lower()
    return "429" in low or "resource_exhausted" in low


def match_one(client, provider: str, model: str, cv_text: str, job: dict, retry_waits=(15, 30, 60)) -> AIMatch:
    fn = _match_gemini if provider == "gemini" else _match_claude
    for wait in (*retry_waits, None):
        try:
            m = fn(client, model, cv_text, job)
            break
        except Exception as e:
            # Gemini free tier has per-minute limits: wait and retry (Claude's SDK already retries 429)
            if provider != "gemini" or wait is None or not _is_rate_limit(e):
                raise
            log.info("Gemini rate limit; retrying in %ss", wait)
            time.sleep(wait)
    m.score = max(0, min(100, int(m.score)))
    return m


def friendly_error(e: Exception) -> str:
    msg = str(e)
    low = msg.lower()
    if "api key not valid" in low or "api_key_invalid" in low or "401" in low or "authentication" in low:
        return "API key inválida. Revísala en Configuración."
    if "429" in low or "resource_exhausted" in low or "quota" in low or "rate" in low:
        return "Límite de uso alcanzado (cuota o velocidad). Espera un minuto o reduce las ofertas a analizar."
    if "not found" in low or "404" in low:
        return "Modelo no disponible para tu key. Elige otro modelo en Configuración."
    return f"{type(e).__name__}: {msg[:200]}"


def match_many(
    cv_text: str,
    jobs: list[dict],
    provider: str,
    api_key: Optional[str],
    model: str,
    progress: Optional[Callable[[int, int], None]] = None,
    client=None,
    workers: Optional[int] = None,
    on_result: Optional[Callable[[str, "AIMatch | str"], None]] = None,
) -> dict[str, AIMatch | str]:
    """Return {job_id: AIMatch | error message}."""
    workers = workers or (2 if provider == "gemini" else 4)
    client = client or make_client(provider, api_key)
    results: dict[str, AIMatch | str] = {}
    done = 0

    def run(job):
        try:
            return job["id"], match_one(client, provider, model, cv_text, job)
        except Exception as e:  # keep going for the other offers
            log.warning("AI match failed for %s: %s", job.get("id"), e)
            return job["id"], friendly_error(e)

    with ThreadPoolExecutor(workers) as ex:
        for fut in as_completed([ex.submit(run, j) for j in jobs]):  # stream as each finishes
            jid, res = fut.result()
            results[jid] = res
            done += 1
            if on_result:
                on_result(jid, res)
            if progress:
                progress(done, len(jobs))
    return results
