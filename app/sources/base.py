"""Common contract for every job source."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Optional

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
BROWSER_HEADERS = {"User-Agent": USER_AGENT, "Accept-Language": "es-PE,es;q=0.9,en;q=0.6"}


@dataclass
class JobPosting:
    source: str
    external_id: str
    title: str
    url: str
    company: Optional[str] = None
    location: Optional[str] = None
    description: Optional[str] = None
    date_posted: Optional[date] = None
    is_remote: Optional[bool] = None
    modality_text: Optional[str] = None  # raw modality hint from the portal
    job_type: Optional[str] = None  # raw job type text
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    salary_currency: Optional[str] = None
    salary_period: Optional[str] = None  # monthly / yearly / hourly / daily / weekly
    extra: dict = field(default_factory=dict)

    @property
    def uid(self) -> str:
        raw = f"{self.source}:{self.external_id or self.url}"
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


ProgressCb = Callable[[int, str], None]  # (found_so_far, message)


@dataclass
class SourceQuery:
    term: str
    location: str
    country: str
    hours_old: Optional[int]
    limit: int
    remote_only: bool = False
    job_type: Optional[str] = None  # single normalized type when exactly one chosen
    fetch_descriptions: bool = True


class SourceError(Exception):
    """Raised when a source cannot run (unsupported country, blocked, etc.)."""


class Source:
    key: str = ""
    label: str = ""
    experimental: bool = False

    def availability(self, country: str) -> Optional[str]:
        """Return a human message if the source cannot run for this country."""
        return None

    def unavailable(self, country: str) -> Optional[str]:
        """availability() plus portals disabled for this deployment (DISABLED_SOURCES)."""
        from app import config

        if self.key in config.DISABLED_SOURCES:
            return config.DISABLED_SOURCE_MSG
        return self.availability(country)

    def search(self, q: SourceQuery, progress: ProgressCb) -> list[JobPosting]:
        raise NotImplementedError
