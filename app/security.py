"""Security helpers: headers, CSRF, auth, rate limiting, encrypted API-key cookie."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import time
from collections import defaultdict, deque
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from app import config

CSRF_HEADER = "x-jobradar"  # custom header: browsers can't send it cross-site without CORS (which we don't enable)
AI_COOKIE = ("__Host-" if config.PRODUCTION else "") + "jr_ai"
SESSION_COOKIE = ("__Host-" if config.PRODUCTION else "") + "jr_session"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

CSP = (
    "default-src 'self'; "
    # Alpine.js evaluates x-* expressions with Function(): needs 'unsafe-eval'. No inline scripts allowed.
    "script-src 'self' 'unsafe-eval'; "
    "style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; "
    "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)


def client_ip(request: Request) -> str:
    """Real client IP. On Render the edge proxy appends the client address to X-Forwarded-For,
    so the right-most entry is the one we can trust (left entries are client-controlled)."""
    if config.PRODUCTION:
        xff = request.headers.get("x-forwarded-for", "")
        parts = [p.strip() for p in xff.split(",") if p.strip()]
        if parts:
            return parts[-1]
    return request.client.host if request.client else "unknown"


class SecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # 1) request size cap (before reading the body)
        limit = config.MAX_UPLOAD_BYTES + 64_000 if request.url.path == "/api/cv" else config.MAX_JSON_BYTES
        try:
            length = int(request.headers.get("content-length") or 0)
        except ValueError:
            length = 0
        if length > limit:
            return JSONResponse({"detail": "Solicitud demasiado grande."}, status_code=413)
        # 2) CSRF: every state-changing request must carry the custom header and a same-origin Origin
        if request.method not in SAFE_METHODS:
            if request.headers.get(CSRF_HEADER) != "1":
                return JSONResponse({"detail": "Solicitud rechazada (CSRF)."}, status_code=403)
            origin = request.headers.get("origin")
            if origin and not _same_origin(origin, request):
                return JSONResponse({"detail": "Origen no permitido."}, status_code=403)
        response: Response = await call_next(request)
        # 3) security headers
        h = response.headers
        h["Content-Security-Policy"] = CSP
        h["X-Content-Type-Options"] = "nosniff"
        h["X-Frame-Options"] = "DENY"
        h["Referrer-Policy"] = "no-referrer"
        h["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        h["Cross-Origin-Opener-Policy"] = "same-origin"
        h["Cross-Origin-Resource-Policy"] = "same-origin"
        if config.PRODUCTION:
            h["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        if request.url.path.startswith("/api/") or request.url.path in ("/", "/login"):
            h["Cache-Control"] = "no-store"
        return response


def _same_origin(origin: str, request: Request) -> bool:
    # browsers set both Origin and Host themselves; a cross-site page cannot forge either
    return origin.rstrip("/").split("://", 1)[-1] == request.headers.get("host", "")


# ---------- auth ----------
def is_authenticated(request: Request) -> bool:
    if not config.AUTH_REQUIRED:
        return True
    sess = request.session
    return bool(sess.get("auth")) and sess.get("exp", 0) > time.time()


def require_auth(request: Request) -> None:
    if not is_authenticated(request):
        raise HTTPException(401, "Sesión expirada. Vuelve a ingresar.")


def check_password(candidate: str) -> bool:
    a = hashlib.sha256(candidate.encode("utf-8")).digest()
    b = hashlib.sha256(config.APP_PASSWORD.encode("utf-8")).digest()
    return hmac.compare_digest(a, b)


# ---------- rate limiting (in-memory, single instance) ----------
class RateLimiter:
    def __init__(self):
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window: float) -> bool:
        """Record a hit; return False when the limit is exceeded."""
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= now - window:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            if len(self._hits) > 10_000:  # bound memory
                for k in [k for k, v in self._hits.items() if not v][:5000]:
                    del self._hits[k]
            return True

    def reset(self):
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()


def rate_limit(request: Request, bucket: str, limit: int, window: float) -> None:
    if not limiter.hit(f"{bucket}:{client_ip(request)}", limit, window):
        raise HTTPException(429, "Demasiadas solicitudes. Intenta de nuevo más tarde.")


# ---------- encrypted API-key cookie ----------
def _fernet() -> Fernet:
    digest = hashlib.sha256(("jobradar-ai-cookie:" + config.SECRET_KEY).encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encode_ai_cookie(data: dict) -> str:
    return _fernet().encrypt(json.dumps(data).encode("utf-8")).decode("ascii")


def decode_ai_cookie(request: Request) -> dict:
    token = request.cookies.get(AI_COOKIE)
    if not token:
        return {}
    try:
        return json.loads(_fernet().decrypt(token.encode("ascii"), ttl=config.AI_COOKIE_MAX_AGE))
    except (InvalidToken, ValueError):
        return {}


def set_ai_cookie(response: Response, data: dict) -> None:
    response.set_cookie(
        AI_COOKIE, encode_ai_cookie(data), max_age=config.AI_COOKIE_MAX_AGE, path="/",
        httponly=True, secure=config.PRODUCTION, samesite="strict",
    )


def clear_ai_cookie(response: Response) -> None:
    response.delete_cookie(AI_COOKIE, path="/", secure=config.PRODUCTION, httponly=True, samesite="strict")


def safe_url(url: Optional[str]) -> Optional[str]:
    """Only allow http(s) links coming from scraped data (blocks javascript:, data:, …)."""
    if not url:
        return None
    u = url.strip()
    return u if u.lower().startswith(("https://", "http://")) else None
