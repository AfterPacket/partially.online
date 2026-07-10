"""
Zero-trust security for the Internet Outage Monitor.

Principles applied
------------------
1. Verify every request  – admin actions require an explicit API key
2. Least privilege       – all public endpoints are read-only
3. Assume breach         – security headers on every response, inputs validated
4. Audit trail           – all admin attempts logged with source IP
"""

import time
import logging
from collections import defaultdict

from fastapi import Depends, HTTPException, Request
from fastapi.security.api_key import APIKeyHeader
from starlette.middleware.base import BaseHTTPMiddleware

from .config import config

log = logging.getLogger(__name__)

# ── Admin key auth ────────────────────────────────────────────────────────────

_KEY_HDR = APIKeyHeader(name="X-Admin-Key", auto_error=False)


async def require_admin(
    request: Request,
    key: str = Depends(_KEY_HDR),
) -> str:
    """Dependency: raises 403 unless a valid X-Admin-Key header is present."""
    if not config.ADMIN_API_KEY:
        raise HTTPException(503, "Admin access not configured — set ADMIN_API_KEY in .env")
    ip = _client_ip(request)
    if not key or key != config.ADMIN_API_KEY:
        log.warning(f"[security] Unauthorized admin attempt from {ip}")
        raise HTTPException(403, "Invalid or missing X-Admin-Key header")
    log.info(f"[security] Admin action authorized from {ip}")
    return key


# ── Rate limiter (no extra dependencies) ─────────────────────────────────────

class _RateLimiter:
    def __init__(self, max_req: int, window_s: int = 60):
        self._max     = max_req
        self._window  = window_s
        self._log: dict = defaultdict(list)

    def is_allowed(self, key: str) -> bool:
        now    = time.monotonic()
        cutoff = now - self._window
        self._log[key] = [t for t in self._log[key] if t > cutoff]
        if len(self._log[key]) >= self._max:
            return False
        self._log[key].append(now)
        return True


_limiter = _RateLimiter(max_req=120, window_s=60)  # 120 req / min per IP


async def rate_limit(request: Request) -> None:
    """Dependency: raises 429 if the caller exceeds 120 requests/minute."""
    if not _limiter.is_allowed(_client_ip(request)):
        raise HTTPException(429, "Rate limit exceeded — try again in a moment")


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("X-Forwarded-For")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# ── Security headers middleware ───────────────────────────────────────────────

_CSP = (
    "default-src 'self'; "
    "script-src 'self' cdn.jsdelivr.net; "
    "style-src 'self' cdn.jsdelivr.net 'unsafe-inline'; "  # Leaflet needs inline styles
    "img-src 'self' data: https://*.basemaps.cartocdn.com; "
    "connect-src 'self' https://cdn.jsdelivr.net; "         # world-atlas fetch
    "font-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds hardening headers to every HTTP response."""

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        h = response.headers
        h["X-Frame-Options"]         = "DENY"
        h["X-Content-Type-Options"]  = "nosniff"
        h["Referrer-Policy"]         = "strict-origin-when-cross-origin"
        h["Permissions-Policy"]      = "geolocation=(), microphone=(), camera=()"
        h["Content-Security-Policy"] = _CSP
        # Uncomment when deployed behind HTTPS:
        # h["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
        return response
