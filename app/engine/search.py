"""Stateless search: runs portals in parallel and streams progress + final results (no storage)."""
from __future__ import annotations

import logging
import queue
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Iterator, Optional

from app.engine.filters import Enriched, apply_filters, dedupe, enrich, score
from app.engine.params import SearchParams
from app.security import safe_url
from app.sources.base import JobPosting, SourceError, SourceQuery
from app.sources.registry import SOURCES

log = logging.getLogger(__name__)
_DONE = object()


def _single_job_type(p: SearchParams) -> Optional[str]:
    return p.job_types[0] if len(p.job_types) == 1 else None


def run_source(key: str, p: SearchParams, emit, sources=None, credentials: Optional[dict] = None) -> list[JobPosting]:
    src = (sources or SOURCES).get(key)

    def status(st, count, message):
        emit({"type": "source", "source": key, "status": st, "count": count, "message": message})

    if src is None:
        status("error", 0, "Portal desconocido")
        return []
    msg = src.unavailable(p.country)
    if msg:
        status("no_disponible", 0, msg)
        return []
    api_key = (credentials or {}).get(key)
    if getattr(src, "needs_key", False) and not api_key:
        status("no_disponible", 0, "Agrega tu key de SerpApi en Configuración para usar este portal.")
        return []
    terms = p.keywords or [""]
    found: list[JobPosting] = []
    errors: list[str] = []
    for i, term in enumerate(terms):
        q = SourceQuery(
            term=term, location=p.location, country=p.country, hours_old=p.hours_old,
            limit=p.max_per_source, remote_only=p.modalities == ["remoto"],
            job_type=_single_job_type(p), fetch_descriptions=p.fetch_descriptions, api_key=api_key,
        )
        prefix = f"«{term}» ({i + 1}/{len(terms)}) " if len(terms) > 1 else ""
        base = len(found)
        last = [0.0]

        def progress(n, message, _base=base, _prefix=prefix):
            now = time.monotonic()
            if now - last[0] > 0.4:  # throttle progress events
                last[0] = now
                status("buscando", _base + n, _prefix + message)

        progress(0, "iniciando…")
        try:
            found.extend(src.search(q, progress))
        except SourceError as e:
            errors.append(str(e))
        except Exception as e:  # network errors, HTML changes, blocks…
            log.warning("source %s failed: %s", key, type(e).__name__)
            errors.append("Error de conexión o el portal cambió su formato." if not isinstance(e, TimeoutError) else "Tiempo de espera agotado.")
        if i < len(terms) - 1:
            time.sleep(1.0)
    if found:
        status("ok" if not errors else "parcial", len(found), f"{len(found)} ofertas" + (f" — aviso: {errors[0]}" if errors else ""))
    else:
        status("error" if errors else "ok", 0, errors[0] if errors else "0 ofertas")
    return found


def process(postings: list[JobPosting], p: SearchParams):
    """Pure pipeline: unique → enrich → dedupe → filter → score. Returns (kept, rejected Counter, dupes)."""
    by_uid: dict[str, JobPosting] = {}
    for j in postings:
        by_uid.setdefault(j.uid, j)
    enriched = [enrich(j, p.currency) for j in by_uid.values()]
    unique = dedupe(enriched)
    kept, rejected = [], Counter()
    for e in unique:
        reason = apply_filters(e, p)
        if reason:
            rejected[reason.split(" (")[0].split(":")[0]] += 1
            continue
        e.score, e.reasons = score(e, p)
        if e.score < p.min_score:
            rejected["Puntaje menor al mínimo"] += 1
            continue
        kept.append(e)
    kept.sort(key=lambda x: (x.score, x.job.date_posted.toordinal() if x.job.date_posted else 0), reverse=True)
    return kept, rejected, len(enriched) - len(unique)


def to_result(e: Enriched) -> Optional[dict]:
    j = e.job
    url = safe_url(j.url)
    if not url:
        return None
    return {
        "id": j.uid, "source": j.source, "title": j.title[:300], "company": (j.company or "")[:200] or None,
        "location": (j.location or "")[:200] or None, "url": url,
        "description": (j.description or "")[:20_000] or None,
        "date_posted": j.date_posted.isoformat() if j.date_posted else None,
        "modality": e.modality, "level": e.level, "job_types": sorted(e.job_types), "required_years": e.required_years,
        "salary_min": j.salary_min, "salary_max": j.salary_max, "salary_currency": j.salary_currency,
        "salary_period": j.salary_period, "via": (j.extra or {}).get("via"), "score": e.score, "reasons": e.reasons, "also_on": e.also_on,
    }


def stream_search(p: SearchParams, sources=None, on_finish=None, credentials: Optional[dict] = None) -> Iterator[dict]:
    """Yield progress events, then {"type": "done", "results": [...], "summary": {...}}.
    `on_finish` is always called once the background work ends (used to release concurrency slots)."""
    events: "queue.Queue" = queue.Queue()
    started = time.time()

    def worker():
        try:
            with ThreadPoolExecutor(max(1, len(p.sources))) as ex:
                batches = list(ex.map(lambda k: run_source(k, p, events.put, sources, credentials), p.sources))
            postings = [j for b in batches for j in b]
            events.put({"type": "phase", "phase": "filtrando"})
            kept, rejected, dupes = process(postings, p)
            results = [r for r in (to_result(e) for e in kept) if r]
            events.put({
                "type": "done", "results": results,
                "summary": {"fetched": len(postings), "duplicates": dupes, "kept": len(results),
                            "rejected": dict(rejected.most_common()), "seconds": round(time.time() - started, 1)},
            })
        except Exception:
            log.exception("search failed")
            events.put({"type": "error", "message": "La búsqueda falló. Intenta de nuevo."})
        finally:
            events.put(_DONE)
            if on_finish:
                on_finish()

    threading.Thread(target=worker, daemon=True).start()
    yield {"type": "phase", "phase": "buscando", "sources": p.sources}
    while True:
        try:
            ev = events.get(timeout=15)
        except queue.Empty:
            yield {"type": "ping"}  # keeps proxies from closing an idle stream
            continue
        if ev is _DONE:
            return
        yield ev
