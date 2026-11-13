"""Enrichment, post-filters, relevance scoring and cross-portal dedupe."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from rapidfuzz import fuzz

from app.engine.params import SearchParams
from app.engine.text import (
    contains_term,
    detect_job_types,
    detect_language,
    detect_level,
    detect_modality,
    extract_required_years,
    norm,
    to_monthly,
)
from app.sources.base import JobPosting

USD_TO_PEN = 3.75  # approximate; used only to compare salaries across currencies


@dataclass
class Enriched:
    job: JobPosting
    modality: Optional[str] = None
    level: Optional[str] = None
    job_types: set = field(default_factory=set)
    required_years: Optional[int] = None
    language: Optional[str] = None
    salary_monthly: Optional[float] = None  # in params currency
    also_on: list = field(default_factory=list)  # other sources with the same offer
    score: int = 0
    reasons: list = field(default_factory=list)
    rejected: Optional[str] = None

    @property
    def text_norm(self) -> str:
        return norm(self.job.title) + " \n " + norm(self.job.description)


def convert(amount: Optional[float], src: Optional[str], dst: str) -> Optional[float]:
    if amount is None:
        return None
    src = (src or dst).upper()
    dst = dst.upper()
    if src == dst:
        return amount
    if src == "USD" and dst == "PEN":
        return amount * USD_TO_PEN
    if src == "PEN" and dst == "USD":
        return amount / USD_TO_PEN
    return None  # unknown currency pair: treat as unknown


def enrich(job: JobPosting, currency: str) -> Enriched:
    desc = job.description or ""
    e = Enriched(job=job)
    e.modality = detect_modality(job.modality_text, job.title, desc, job.is_remote)
    e.level = detect_level(job.title)
    e.job_types = detect_job_types(job.job_type, job.title, desc)
    e.required_years = extract_required_years(desc) if desc else None
    e.language = detect_language(desc) if desc else None
    best = job.salary_max or job.salary_min
    e.salary_monthly = convert(to_monthly(best, job.salary_period), job.salary_currency, currency)
    return e


def apply_filters(e: Enriched, p: SearchParams, today: Optional[date] = None) -> Optional[str]:
    """Return the rejection reason, or None when the offer passes."""
    j = e.job
    today = today or date.today()
    title_n = norm(j.title)
    text_n = e.text_norm
    has_desc = bool(j.description and len(j.description) > 80)

    if p.hours_old and j.date_posted:
        if (today - j.date_posted).days > max(1, p.hours_old // 24):
            return "Fuera del rango de fecha"
    if p.modalities and e.modality and e.modality not in p.modalities:
        return f"Modalidad {e.modality}"
    if p.job_types and e.job_types and not (e.job_types & set(p.job_types)):
        return "Tipo de jornada"
    if p.levels and e.level and e.level not in p.levels:
        return f"Nivel {e.level}"
    if p.years_experience is not None and e.required_years is not None and e.required_years > p.years_experience:
        return f"Pide {e.required_years} años de experiencia"
    if p.salary_min:
        if e.salary_monthly is None:
            if not p.include_no_salary:
                return "Sin salario publicado"
        elif e.salary_monthly < p.salary_min:
            return "Salario menor al mínimo"
    for w in p.exclude_title:
        if contains_term(title_n, w):
            return f"Título contiene '{w}'"
    for w in p.exclude:
        if contains_term(text_n, w):
            return f"Contiene '{w}'"
    comp_n = norm(j.company)
    for c in p.blocked_companies:
        cn = norm(c)
        if cn and comp_n and (cn in comp_n or fuzz.ratio(cn, comp_n) >= 90):
            return f"Empresa bloqueada ({j.company})"
    if p.must_have:
        haystack = text_n if has_desc else title_n
        missing = [w for w in p.must_have if not contains_term(haystack, w)]
        if missing and has_desc:
            return "Falta: " + ", ".join(missing)
        # without description we cannot verify: keep it, flagged in the reasons
    if p.languages and e.language and e.language not in p.languages:
        return f"Idioma {e.language}"
    return None


def score(e: Enriched, p: SearchParams, today: Optional[date] = None) -> tuple[int, list[str]]:
    """Relevance 0-100 with human-readable reasons; components without criteria are skipped."""
    j = e.job
    today = today or date.today()
    title_n = norm(j.title)
    text_n = e.text_norm
    has_desc = bool(j.description and len(j.description) > 80)
    got = total = 0.0
    reasons: list[str] = []

    # Title vs keywords (35)
    if p.keywords:
        best_kw, best = "", 0.0
        for kw in p.keywords:
            kn = norm(kw)
            if contains_term(title_n, kw):
                r = 100.0
            else:
                r = max(fuzz.token_set_ratio(kn, title_n), fuzz.partial_ratio(kn, title_n) * 0.9)
            if r > best:
                best, best_kw = r, kw
        pts = 35.0 if best >= 95 else (35.0 * best / 100 * 0.8 if best >= 50 else 0.0)
        got += pts
        total += 35
        if pts >= 30:
            reasons.append(f"Título coincide con «{best_kw}»")
        elif pts > 0:
            reasons.append(f"Título similar a «{best_kw}» ({int(best)}%)")
        else:
            reasons.append("Título poco relacionado")

    # Nice-to-have skills (30)
    if p.nice_to_have:
        hits = [w for w in p.nice_to_have if contains_term(text_n, w)]
        got += 30.0 * len(hits) / len(p.nice_to_have)
        total += 30
        if hits:
            reasons.append("Deseables: " + ", ".join(hits))
        elif not has_desc:
            reasons.append("Sin descripción para evaluar deseables")

    # Must-have confirmation (bonus visibility only)
    if p.must_have and not has_desc:
        reasons.append("⚠ Obligatorias sin verificar (sin descripción)")

    # Modality / location (15)
    if p.modalities:
        total += 15
        if e.modality in p.modalities:
            got += 15
            reasons.append(f"Modalidad {e.modality}")
        elif e.modality is None:
            got += 6
            reasons.append("Modalidad no indicada")

    # Salary (10)
    if p.salary_min:
        total += 10
        if e.salary_monthly is not None and e.salary_monthly >= p.salary_min:
            got += 10
            reasons.append(f"Salario ≈ {p.currency} {e.salary_monthly:,.0f}/mes")
        elif e.salary_monthly is None:
            got += 4

    # Recency (10)
    total += 10
    if j.date_posted:
        days = (today - j.date_posted).days
        rec = 10 if days <= 1 else 8 if days <= 3 else 6 if days <= 7 else 4 if days <= 14 else 2 if days <= 30 else 0
        got += rec
        if days <= 1:
            reasons.append("Publicada hoy/ayer")
    else:
        got += 3

    if e.required_years is not None:
        reasons.append(f"Pide {e.required_years}+ años")
    if e.also_on:
        reasons.append("También en: " + ", ".join(e.also_on))

    value = int(round(100 * got / total)) if total else 0
    return value, reasons


def _dedupe_key(e: Enriched) -> tuple[str, str]:
    return norm(e.job.company), norm(e.job.title)


def dedupe(items: list[Enriched]) -> list[Enriched]:
    """Merge the same offer published on several portals (keeps the richest copy)."""
    kept: list[Enriched] = []
    for e in sorted(items, key=lambda x: len(x.job.description or ""), reverse=True):
        comp, title = _dedupe_key(e)
        dup = None
        for k in kept:
            kc, kt = _dedupe_key(k)
            if k.job.source == e.job.source and k.job.external_id == e.job.external_id:
                dup = k
                break
            same_company = bool(comp and kc) and (comp == kc or fuzz.ratio(comp, kc) >= 88)
            if same_company and fuzz.token_sort_ratio(title, kt) >= 92:
                dup = k
                break
        if dup is None:
            kept.append(e)
        elif e.job.source != dup.job.source and e.job.source not in dup.also_on:
            dup.also_on.append(e.job.source)
    return kept
