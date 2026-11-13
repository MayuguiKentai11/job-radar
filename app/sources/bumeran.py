"""Bumeran Perú via the JSON API used by its own web app."""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from typing import Optional

import httpx

from app.engine.text import html_to_text, norm, parse_salary_text
from app.sources.base import BROWSER_HEADERS, JobPosting, Source, SourceError, SourceQuery
from app.sources.computrabajo import slugify

_SITES = {"peru": ("BMPE", "https://www.bumeran.com.pe")}
PAGE_SIZE = 20


def _parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    for fmt in ("%d-%m-%Y %H:%M:%S", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_item(item: dict, base: str) -> JobPosting:
    title = (item.get("titulo") or "").strip()
    company = None if item.get("confidencial") else item.get("empresa")
    jid = str(item.get("id"))
    slug = slugify(f"{title} {company or ''}")[:120]
    lo = hi = cur = per = None
    sal = item.get("salario") or item.get("salarioDesde")
    if isinstance(sal, str):
        lo, hi, cur, per = parse_salary_text(sal)
    return JobPosting(
        source="bumeran",
        external_id=jid,
        title=title,
        url=f"{base}/empleos/{slug}-{jid}.html",
        company=company or "Confidencial",
        location=item.get("localizacion"),
        description=html_to_text(item.get("detalle")),
        date_posted=_parse_date(item.get("fechaHoraPublicacion") or item.get("fechaPublicacion")),
        modality_text=item.get("modalidadTrabajo"),
        job_type=item.get("tipoTrabajo"),
        salary_min=lo,
        salary_max=hi,
        salary_currency=cur,
        salary_period=per,
        extra={"vacantes": item.get("cantidadVacantes")},
    )


class BumeranSource(Source):
    key = "bumeran"
    label = "Bumeran"

    def __init__(self, delay: float = 0.5):
        self.delay = delay

    def availability(self, country: str):
        if country.lower() not in _SITES:
            return f"Bumeran solo está configurado para Perú."
        return None

    def search(self, q: SourceQuery, progress) -> list[JobPosting]:
        msg = self.availability(q.country)
        if msg:
            raise SourceError(msg)
        site_id, base = _SITES[q.country.lower()]
        headers = {**BROWSER_HEADERS, "x-site-id": site_id, "Content-Type": "application/json",
                   "Accept": "application/json", "Origin": base, "Referer": base + "/"}
        cutoff = (datetime.now() - timedelta(hours=q.hours_old)).date() if q.hours_old else None
        loc_norm = norm(q.location)
        jobs: list[JobPosting] = []
        body = {"filtros": [], "busquedaExtendida": False, "tipoDetalle": "full",
                "withHome": False, "internacional": False, "query": q.term}
        page = 0
        scanned = 0
        with httpx.Client(headers=headers, timeout=25) as client:
            while len(jobs) < q.limit and page < 25:
                r = client.post(f"{base}/api/avisos/searchV2?pageSize={PAGE_SIZE}&page={page}&sort=RECIENTES", json=body)
                if r.status_code in (403, 429):
                    if not jobs:
                        raise SourceError(f"Bumeran bloqueó la consulta (HTTP {r.status_code}).")
                    break
                r.raise_for_status()
                data = r.json()
                items = data.get("content") or []
                if not items:
                    break
                stop = False
                for it in items:
                    scanned += 1
                    j = parse_item(it, base)
                    if cutoff and j.date_posted and j.date_posted < cutoff:
                        stop = True  # sorted by recency: everything after is older
                        continue
                    if loc_norm and j.location and loc_norm not in norm(j.location) and norm(j.modality_text) != "remoto":
                        continue
                    jobs.append(j)
                progress(len(jobs), f"Página {page + 1}: {len(jobs)} ofertas")
                if stop or scanned >= (data.get("total") or 0):
                    break
                page += 1
                time.sleep(self.delay)
        return jobs[: q.limit]
