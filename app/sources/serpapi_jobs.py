"""Google Jobs through SerpApi (official paid API with a free tier; the user brings their own key).

Google Jobs aggregates offers published on LinkedIn, Bumeran, Computrabajo, Indeed and company sites,
so this source also brings back offers from portals that block cloud servers.
Docs: https://serpapi.com/google-jobs-api
"""
from __future__ import annotations

import re
from typing import Optional

import httpx

from app import config
from app.engine.text import parse_relative_date, parse_salary_text
from app.sources.base import JobPosting, Source, SourceError, SourceQuery

ENDPOINT = "https://serpapi.com/search.json"
LOCATIONS = "https://serpapi.com/locations.json"  # free, no key: maps "Lima" to Google's canonical name
_LOC_CACHE: dict[str, str] = {}
KEY_RE = re.compile(r"^[A-Za-z0-9]{20,128}$")


def valid_key(key: str) -> bool:
    return bool(KEY_RE.match(key or ""))


def _clean_via(via: Optional[str]) -> Optional[str]:
    if not via:
        return None
    return re.sub(r"^(v[ií]a|via)\s+", "", via.strip(), flags=re.I)[:60] or None


def parse_item(it: dict) -> Optional[JobPosting]:
    title = (it.get("title") or "").strip()
    apply = [a for a in (it.get("apply_options") or []) if isinstance(a, dict) and a.get("link")]
    url = apply[0]["link"] if apply else it.get("share_link")
    if not title or not url:
        return None
    ext = it.get("detected_extensions") or {}
    lo, hi, cur, per = parse_salary_text(ext.get("salary"))
    wfh = ext.get("work_from_home")
    return JobPosting(
        source="google",
        external_id=str(it.get("job_id") or url)[:300],
        title=title,
        url=url,
        company=it.get("company_name"),
        location=it.get("location"),
        description=it.get("description"),
        date_posted=parse_relative_date(ext.get("posted_at")),
        is_remote=True if wfh else None,
        modality_text="remoto" if wfh else None,
        job_type=ext.get("schedule_type"),
        salary_min=lo,
        salary_max=hi,
        salary_currency=cur,
        salary_period=per,
        extra={"via": _clean_via(it.get("via"))},
    )


def _error_message(status: int, data: dict) -> Optional[str]:
    err = (data or {}).get("error")
    if not err and status < 400:
        return None
    e = (err or "").lower()
    if "hasn't returned any results" in e or "no results" in e:
        return None  # an empty search is not an error
    if status == 401 or "invalid api key" in e:
        return "SerpApi: la API key no es válida. Revísala en Configuración."
    if status == 429 or "run out of searches" in e or ("plan" in e and "limit" in e):
        return "SerpApi: se acabaron las búsquedas de tu plan este mes."
    return "SerpApi no pudo completar la búsqueda."


class SerpApiJobsSource(Source):
    key = "google"
    label = "Google Jobs"
    needs_key = True

    def __init__(self, client_factory=None):
        self._client_factory = client_factory or (lambda: httpx.Client(timeout=30))

    def availability(self, country: str):
        if country.lower() != "peru":
            return "Google Jobs está configurado solo para Perú."
        return None

    def _location(self, client, place: str) -> str:
        """'Lima' -> 'Lima,Lima,Lima Province,Peru' (first match in Peru); 'Peru' when unknown."""
        key = place.lower()
        if key not in _LOC_CACHE:
            canonical = "Peru"
            try:
                r = client.get(LOCATIONS, params={"q": place, "limit": 10})
                for loc in r.json() if r.status_code == 200 else []:
                    if isinstance(loc, dict) and loc.get("country_code") == "PE" and loc.get("canonical_name"):
                        canonical = loc["canonical_name"]
                        break
            except (httpx.HTTPError, ValueError):
                return "Peru"  # don't cache transient failures
            if len(_LOC_CACHE) > 200:
                _LOC_CACHE.clear()
            _LOC_CACHE[key] = canonical
        return _LOC_CACHE[key]

    def _call(self, client, params: dict) -> dict:
        r = client.get(ENDPOINT, params=params)
        try:
            data = r.json()
        except ValueError:
            data = {}
        msg = _error_message(r.status_code, data)
        if msg:
            raise SourceError(msg)
        return data

    def search(self, q: SourceQuery, progress) -> list[JobPosting]:
        if not q.api_key:
            raise SourceError("Agrega tu key de SerpApi en Configuración para usar Google Jobs.")
        base = {"engine": "google_jobs", "q": q.term, "google_domain": "google.com.pe", "gl": "pe", "hl": "es",
                "api_key": q.api_key}
        if q.remote_only:
            base["ltype"] = "1"  # Google Jobs "work from home" filter
        place = (q.location or "").strip()
        jobs: list[JobPosting] = []
        pages = max(1, min(config.SERPAPI_MAX_PAGES, -(-q.limit // 10)))
        with self._client_factory() as client:
            location = self._location(client, place) if place else "Peru"
            if location == "Peru" and place and place.lower() not in ("peru", "perú"):
                params = {**base, "q": f"{q.term} {place}", "location": "Peru"}  # city unknown to Google
            else:
                params = {**base, "location": location}
            try:
                data = self._call(client, params)
            except SourceError as e:
                if "no pudo" not in str(e) or not place:
                    raise
                # unknown location name: search the whole country with the city in the query instead
                params = {**base, "q": f"{q.term} {place}", "location": "Peru"}
                data = self._call(client, params)
            for page in range(pages):
                for it in data.get("jobs_results") or []:
                    j = parse_item(it)
                    if j:
                        jobs.append(j)
                progress(len(jobs), f"Página {page + 1}: {len(jobs)} ofertas")
                token = (data.get("serpapi_pagination") or {}).get("next_page_token")
                if page + 1 >= pages or not token or len(jobs) >= q.limit:
                    break
                data = self._call(client, {**params, "next_page_token": token})
        return jobs[: q.limit]
