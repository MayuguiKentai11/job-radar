"""Indeed, LinkedIn, Glassdoor and Google Jobs through python-jobspy."""
from __future__ import annotations

import logging
import math
from datetime import date, datetime

from jobspy import scrape_jobs
from jobspy.model import Country

from app.engine.text import html_to_text
from app.sources.base import JobPosting, Source, SourceError, SourceQuery

log = logging.getLogger(__name__)

_JOBSPY_TYPES = {"tiempo_completo": "fulltime", "medio_tiempo": "parttime", "practicas": "internship", "freelance": "contract"}
_PERIOD = {"yearly": "yearly", "monthly": "monthly", "hourly": "hourly", "daily": "daily", "weekly": "weekly"}


def _clean(v):
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    try:
        import pandas as pd

        if v is pd.NaT:
            return None
    except Exception:  # pragma: no cover
        pass
    return v


def _country_display(country: str) -> str:
    return {"peru": "Perú"}.get(country.lower(), country.title())


class JobSpySource(Source):
    def __init__(self, site: str, label: str, experimental: bool = False):
        self.key = site
        self.label = label
        self.experimental = experimental

    def availability(self, country: str):
        if self.key == "google":
            return "Google bloquea las búsquedas automáticas de empleos, así que este portal está desactivado."
        if self.key == "glassdoor":
            from jobspy.glassdoor.constant import domains

            try:
                c = Country(country.lower())
            except ValueError:
                return f"País '{country}' no reconocido por Glassdoor."
            if c not in domains:
                return f"Glassdoor no tiene sitio para {_country_display(country)} (no soportado por el motor)."
        return None

    def _kwargs(self, q: SourceQuery) -> dict:
        kw: dict = dict(
            site_name=self.key,
            search_term=q.term,
            results_wanted=q.limit,
            country_indeed=q.country.lower(),
            verbose=0,
            description_format="markdown",
        )
        loc = q.location.strip()
        country_name = _country_display(q.country)
        if self.key == "indeed":
            kw["location"] = loc or None
            # Indeed: hours_old cannot be combined with job_type / is_remote
            if q.hours_old:
                kw["hours_old"] = q.hours_old
            elif q.remote_only:
                kw["is_remote"] = True
            elif q.job_type in _JOBSPY_TYPES:
                kw["job_type"] = _JOBSPY_TYPES[q.job_type]
        elif self.key == "linkedin":
            kw["location"] = f"{loc}, {country_name}" if loc else country_name
            kw["fetch_description"] = q.fetch_descriptions
            if q.hours_old:
                kw["hours_old"] = q.hours_old
            if q.remote_only:
                kw["is_remote"] = True
            if q.job_type in _JOBSPY_TYPES:
                kw["job_type"] = _JOBSPY_TYPES[q.job_type]
        elif self.key == "google":
            when = ""
            if q.hours_old:
                when = " since yesterday" if q.hours_old <= 24 else (" in the last 3 days" if q.hours_old <= 72 else (" in the last week" if q.hours_old <= 168 else " in the last month"))
            place = f"{loc}, {country_name}" if loc else country_name
            kw["google_search_term"] = f"{q.term} jobs near {place}{when}"
        else:  # glassdoor
            kw["location"] = loc or None
            if q.hours_old:
                kw["hours_old"] = q.hours_old
            if q.remote_only:
                kw["is_remote"] = True
        return kw

    def search(self, q: SourceQuery, progress) -> list[JobPosting]:
        msg = self.availability(q.country)
        if msg:
            raise SourceError(msg)
        progress(0, f"Consultando {self.label}…")
        df = scrape_jobs(**self._kwargs(q))
        errors = (df.attrs or {}).get("errors") or {}
        if errors and df.empty:
            err = "; ".join(str(e) for e in errors.values())
            if "no job data" in err.lower():
                err = "El portal no devolvió resultados (puede estar bloqueando consultas automáticas)."
            raise SourceError(err)
        jobs: list[JobPosting] = []
        for row in df.to_dict(orient="records"):
            row = {k: _clean(v) for k, v in row.items()}
            if not row.get("title") or not row.get("job_url"):
                continue
            dp = row.get("date_posted")
            if isinstance(dp, datetime):
                dp = dp.date()
            elif isinstance(dp, str):
                try:
                    dp = date.fromisoformat(dp[:10])
                except ValueError:
                    dp = None
            jobs.append(
                JobPosting(
                    source=self.key,
                    external_id=str(row.get("id") or row["job_url"]),
                    title=str(row["title"]).strip(),
                    url=row.get("job_url_direct") if self.key == "google" and row.get("job_url_direct") else row["job_url"],
                    company=row.get("company"),
                    location=row.get("location"),
                    description=html_to_text(row.get("description")),
                    date_posted=dp if isinstance(dp, date) else None,
                    is_remote=bool(row["is_remote"]) if row.get("is_remote") is not None else None,
                    modality_text=row.get("work_from_home_type"),
                    job_type=row.get("job_type"),
                    salary_min=row.get("min_amount"),
                    salary_max=row.get("max_amount"),
                    salary_currency=row.get("currency"),
                    salary_period=_PERIOD.get(str(row.get("interval") or "").lower()),
                    extra={"job_level": row.get("job_level"), "company_url": row.get("company_url")},
                )
            )
        progress(len(jobs), f"{len(jobs)} ofertas")
        return jobs
