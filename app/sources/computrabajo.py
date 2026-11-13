"""Computrabajo scraper: listing pages (HTML) + detail pages (JSON-LD JobPosting)."""
from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from app.engine.text import html_to_text, norm, parse_relative_date, parse_salary_text
from app.sources.base import BROWSER_HEADERS, JobPosting, Source, SourceError, SourceQuery

log = logging.getLogger(__name__)

_COUNTRY_SUBDOMAIN = {"peru": "pe", "colombia": "co", "mexico": "mx", "chile": "cl", "argentina": "ar", "ecuador": "ec"}
PAGE_SIZE = 20


def slugify(text: str) -> str:
    return re.sub(r"\s+", "-", re.sub(r"[^a-z0-9\s]", " ", norm(text))).strip("-")


def pubdate_param(hours_old: Optional[int]) -> Optional[int]:
    if not hours_old:
        return None
    days = hours_old / 24
    for d in (1, 3, 7, 15, 30):
        if days <= d:
            return d
    return None


def build_search_url(base: str, term: str, location: str, page: int, hours_old: Optional[int]) -> str:
    path = f"/trabajo-de-{slugify(term)}"
    if location.strip():
        path += f"-en-{slugify(location)}"
    params = []
    pd = pubdate_param(hours_old)
    if pd:
        params.append(f"pubdate={pd}")
    if page > 1:
        params.append(f"p={page}")
    return base + path + ("?" + "&".join(params) if params else "")


def parse_listing(html: str, base: str, today: Optional[date] = None) -> list[JobPosting]:
    soup = BeautifulSoup(html, "lxml")
    out: list[JobPosting] = []
    for art in soup.select("article.box_offer"):
        a = art.select_one("h2 a.js-o-link") or art.select_one("h2 a")
        if not a or not a.get("href"):
            continue
        oid = art.get("data-id") or art.get("id") or a["href"]
        href = a["href"].split("#")[0]
        url = href if href.startswith("http") else base + href
        comp = art.select_one("a[offer-grid-article-company-url]")
        company = comp.get_text(strip=True) if comp else None
        if not company:
            p = art.select_one("p.dFlex")
            company = p.get_text(" ", strip=True) if p else None
        loc_el = art.select_one("p.fs16.fc_base.mt5:not(.dFlex) span.mr10")  # .dFlex holds the company rating
        location = loc_el.get_text(" ", strip=True) if loc_el else None
        modality = salary_txt = None
        for span in art.select("div.fs13 span.dIB"):
            txt = span.get_text(" ", strip=True)
            if span.select_one(".i_home_office"):
                modality = txt
            elif span.select_one(".i_salary") or "S/" in txt:
                salary_txt = txt
        date_el = art.select_one("p.fs13.fc_aux")
        posted = parse_relative_date(date_el.get_text(" ", strip=True) if date_el else None, today)
        lo, hi, cur, per = parse_salary_text(salary_txt)
        out.append(
            JobPosting(
                source="computrabajo",
                external_id=str(oid),
                title=a.get_text(" ", strip=True),
                url=url,
                company=company,
                location=location,
                date_posted=posted,
                modality_text=modality,
                salary_min=lo,
                salary_max=hi,
                salary_currency=cur,
                salary_period=per,
                extra={"salary_text": salary_txt} if salary_txt else {},
            )
        )
    return out


def parse_detail(html: str) -> dict:
    """Extract description, job type, date and salary from the detail page JSON-LD."""
    soup = BeautifulSoup(html, "lxml")
    info: dict = {}
    for script in soup.select("script[type='application/ld+json']"):
        try:
            data = json.loads(script.string or "{}")
        except json.JSONDecodeError:
            continue
        nodes = data.get("@graph", [data]) if isinstance(data, dict) else data
        for node in nodes:
            if isinstance(node, dict) and node.get("@type") == "JobPosting":
                info["description"] = html_to_text(node.get("description"))
                et = node.get("employmentType")
                info["job_type"] = ", ".join(et) if isinstance(et, list) else et
                dp = node.get("datePosted")
                if dp:
                    try:
                        info["date_posted"] = date.fromisoformat(str(dp)[:10])
                    except ValueError:
                        pass
                sal = node.get("baseSalary") or {}
                val = sal.get("value") if isinstance(sal, dict) else None
                if isinstance(val, dict):
                    lo = val.get("minValue") or val.get("value")
                    hi = val.get("maxValue")
                    try:
                        info["salary_min"] = float(lo) if lo else None
                        info["salary_max"] = float(hi) if hi else None
                    except (TypeError, ValueError):
                        pass
                    info["salary_currency"] = sal.get("currency")
                    unit = str(val.get("unitText") or "").upper()
                    info["salary_period"] = {"MONTH": "monthly", "YEAR": "yearly", "HOUR": "hourly", "DAY": "daily", "WEEK": "weekly"}.get(unit)
    if "description" not in info:
        box = soup.select_one("div[div-link='oferta'] p.mbB") or soup.select_one("div[div-link='oferta']")
        if box:
            info["description"] = box.get_text("\n", strip=True)
    # tags under "Descripción de la oferta" (e.g. "Tiempo completo", "Presencial y remoto")
    tags = [s.get_text(" ", strip=True) for s in soup.select("div[div-link='oferta'] span.tag")]
    if tags:
        info["tags"] = tags
    return info


class ComputrabajoSource(Source):
    key = "computrabajo"
    label = "Computrabajo"

    def __init__(self, delay: float = 0.6, detail_workers: int = 4):
        self.delay = delay
        self.detail_workers = detail_workers

    def availability(self, country: str):
        if country.lower() not in _COUNTRY_SUBDOMAIN:
            return f"Computrabajo no configurado para {country}."
        return None

    def search(self, q: SourceQuery, progress) -> list[JobPosting]:
        msg = self.availability(q.country)
        if msg:
            raise SourceError(msg)
        base = f"https://{_COUNTRY_SUBDOMAIN[q.country.lower()]}.computrabajo.com"
        jobs: list[JobPosting] = []
        seen: set[str] = set()
        with httpx.Client(headers=BROWSER_HEADERS, timeout=25, follow_redirects=True) as client:
            page = 1
            max_pages = max(1, -(-q.limit // PAGE_SIZE)) + 1
            while len(jobs) < q.limit and page <= max_pages:
                url = build_search_url(base, q.term, q.location, page, q.hours_old)
                r = client.get(url)
                if r.status_code == 404:
                    break
                if r.status_code in (403, 429):
                    if not jobs:
                        raise SourceError(f"Computrabajo bloqueó la consulta (HTTP {r.status_code}).")
                    break
                r.raise_for_status()
                batch = [j for j in parse_listing(r.text, base) if j.external_id not in seen]
                if not batch:
                    break
                for j in batch:
                    seen.add(j.external_id)
                jobs.extend(batch)
                progress(len(jobs), f"Página {page}: {len(jobs)} ofertas")
                page += 1
                time.sleep(self.delay)
            jobs = jobs[: q.limit]

            if q.fetch_descriptions and jobs:
                done = 0

                def fetch(j: JobPosting):
                    try:
                        resp = client.get(j.url)
                        if resp.status_code == 200:
                            return j, parse_detail(resp.text)
                    except httpx.HTTPError as e:
                        log.debug("detail failed %s: %s", j.url, e)
                    return j, {}

                with ThreadPoolExecutor(self.detail_workers) as ex:
                    for j, info in ex.map(fetch, jobs):
                        done += 1
                        j.description = info.get("description") or j.description
                        j.job_type = info.get("job_type") or j.job_type
                        if info.get("date_posted"):
                            j.date_posted = info["date_posted"]
                        if info.get("salary_min") and not j.salary_min:
                            j.salary_min, j.salary_max = info["salary_min"], info.get("salary_max")
                            j.salary_currency = info.get("salary_currency") or "PEN"
                            j.salary_period = info.get("salary_period") or "monthly"
                        if info.get("tags"):
                            j.extra["tags"] = info["tags"]
                            if not j.job_type:
                                j.job_type = ", ".join(info["tags"])
                        if done % 10 == 0 or done == len(jobs):
                            progress(len(jobs), f"Descripciones {done}/{len(jobs)}")
        return jobs
