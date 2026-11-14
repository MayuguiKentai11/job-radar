"""Runtime configuration from environment variables (12-factor; no secrets in code)."""
from __future__ import annotations

import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

ENV = os.environ.get("ENV", "development").lower()
PRODUCTION = ENV == "production"

# Signs the session cookie and derives the key that encrypts the API-key cookie.
SECRET_KEY = os.environ.get("SECRET_KEY") or ""
# Access password for the whole app. Required in production.
APP_PASSWORD = os.environ.get("APP_PASSWORD") or ""

if PRODUCTION:
    if len(SECRET_KEY) < 32:
        raise RuntimeError("SECRET_KEY must be set (>= 32 chars) in production.")
    if len(APP_PASSWORD) < 12:
        raise RuntimeError("APP_PASSWORD must be set (>= 12 chars) in production.")
elif not SECRET_KEY:
    SECRET_KEY = secrets.token_urlsafe(48)  # dev only: sessions reset on restart

AUTH_REQUIRED = bool(APP_PASSWORD)

# Portals that refuse requests coming from cloud servers (datacenter IPs). Listed here they are shown as
# "no disponible" instead of failing on every search. Example on Render: DISABLED_SOURCES=linkedin,bumeran,google
DISABLED_SOURCES = {s.strip().lower() for s in os.environ.get("DISABLED_SOURCES", "").split(",") if s.strip()}
# Google Jobs via SerpApi: pages of 10 offers per role (each page uses 1 search of the user's SerpApi plan)
SERPAPI_MAX_PAGES = max(1, min(int(os.environ.get("SERPAPI_MAX_PAGES", 2)), 5))
SERPAPI_ENV_KEY = os.environ.get("SERPAPI_API_KEY", "").strip()
DISABLED_SOURCE_MSG = ("Este portal bloquea las consultas directas desde servidores en la nube. Sus ofertas llegan a través de "
                       "Google Jobs (activa tu key de SerpApi en Configuración), o directo si usas JobRadar en tu PC (run.bat).")
SESSION_MAX_AGE = int(os.environ.get("SESSION_MAX_AGE", 7 * 24 * 3600))
AI_COOKIE_MAX_AGE = 30 * 24 * 3600

# Abuse / resource limits (Render free tier: 512 MB RAM, shared CPU)
MAX_CONCURRENT_SEARCHES = int(os.environ.get("MAX_CONCURRENT_SEARCHES", 2))
SEARCHES_PER_HOUR = int(os.environ.get("SEARCHES_PER_HOUR", 30))
AI_REQUESTS_PER_HOUR = int(os.environ.get("AI_REQUESTS_PER_HOUR", 20))
LOGIN_ATTEMPTS_PER_15MIN = 5
MAX_KEYWORDS = 5
MAX_PER_SOURCE = int(os.environ.get("MAX_PER_SOURCE", 100))
MAX_AI_JOBS = 50
MAX_CV_CHARS = 30_000
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_JSON_BYTES = 2 * 1024 * 1024
