"""Search parameters entered by the user (sections A–E of the design)."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator

from app import config
from app.engine.text import split_list

MODALITIES = ["remoto", "hibrido", "presencial"]
LEVELS = ["practicante", "junior", "semi-senior", "senior", "jefatura"]
JOB_TYPES = ["tiempo_completo", "medio_tiempo", "practicas", "temporal", "freelance"]


class SearchParams(BaseModel):
    model_config = {"extra": "ignore"}

    # A. Qué buscar
    keywords: list[str] = Field(default_factory=list)
    similar_titles: bool = False  # False: the title must contain the role (or a synonym)
    location: str = "Lima"
    country: str = "peru"
    modalities: list[str] = Field(default_factory=list)
    hours_old: Optional[int] = Field(168, ge=0, le=24 * 365)
    job_types: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=lambda: ["indeed", "linkedin", "computrabajo", "bumeran", "google"])
    max_per_source: int = 50
    fetch_descriptions: bool = True
    # B. Perfil y condiciones
    levels: list[str] = Field(default_factory=list)
    salary_min: Optional[float] = Field(None, ge=0, le=10_000_000)
    currency: str = "PEN"
    include_no_salary: bool = True
    years_experience: Optional[int] = Field(None, ge=0, le=60)
    # C. Filtros finos
    must_have: list[str] = Field(default_factory=list)
    nice_to_have: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)
    exclude_title: list[str] = Field(default_factory=list)
    blocked_companies: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)  # "es", "en"; empty = any
    # D/E
    min_score: int = Field(0, ge=0, le=100)

    @field_validator("keywords", "must_have", "nice_to_have", "exclude", "exclude_title", "blocked_companies", mode="before")
    @classmethod
    def _split(cls, v):
        items = [i[:80] for i in split_list(v)]  # cap each term
        return items[:30]

    @field_validator("keywords")
    @classmethod
    def _kw_limit(cls, v):
        if len(v) > config.MAX_KEYWORDS:
            raise ValueError(f"Máximo {config.MAX_KEYWORDS} cargos por búsqueda.")
        return v

    @field_validator("location", "country", "currency")
    @classmethod
    def _short(cls, v):
        return (v or "").strip()[:60]

    @field_validator("currency")
    @classmethod
    def _currency(cls, v):
        return v if v in ("PEN", "USD") else "PEN"

    @field_validator("languages")
    @classmethod
    def _langs(cls, v):
        return [x for x in v if x in ("es", "en")]

    @field_validator("sources")
    @classmethod
    def _sources(cls, v):
        from app.sources.registry import SOURCES

        return list(dict.fromkeys(x for x in v if x in SOURCES))

    @field_validator("modalities")
    @classmethod
    def _mods(cls, v):
        return [m for m in v if m in MODALITIES]

    @field_validator("levels")
    @classmethod
    def _levels(cls, v):
        return [m for m in v if m in LEVELS]

    @field_validator("job_types")
    @classmethod
    def _types(cls, v):
        return [m for m in v if m in JOB_TYPES]

    @field_validator("max_per_source")
    @classmethod
    def _limit(cls, v):
        return max(5, min(int(v), config.MAX_PER_SOURCE))

    @field_validator("hours_old", "salary_min", mode="before")
    @classmethod
    def _zero_none(cls, v):
        return None if v in ("", 0, "0", None) else v

    @field_validator("years_experience", mode="before")
    @classmethod
    def _empty_none(cls, v):
        return None if v in ("", None) else v  # 0 years is a valid answer
