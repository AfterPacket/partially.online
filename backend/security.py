"""
Zero-trust security for the Internet Outage Monitor.

Principles applied
------------------
1. Verify every request  – admin actions require an explicit API key
2. Least privilege       – all public endpoints are read-only
3. Assume breach         – security headers on every response, inputs validated
4. Audit trail           – all admin attempts logged with source IP
"""

import hmac
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
        log.error("[security] Admin endpoint hit but ADMIN_API_KEY not configured")
        raise HTTPException(403, "Invalid or missing X-Admin-Key header")
    ip = _client_ip(request)
    if not key or not hmac.compare_digest(key, config.ADMIN_API_KEY):
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
    # When behind a reverse proxy (nginx), X-Forwarded-For is set by the
    # proxy. The rightmost entry is the one added by the proxy we control.
    # The leftmost entries can be spoofed by the client, so we use the
    # rightmost entry (closest to our proxy) when we have more than one hop.
    # When running without a proxy, request.client.host is authoritative.
    fwd = request.headers.get("X-Forwarded-For")
    if fwd:
        # Take the last entry — set by the trusted proxy directly in front of us
        return fwd.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


# ── Security headers middleware ───────────────────────────────────────────────

def _build_csp() -> str:
    """Build the Content-Security-Policy with ad script domains from config."""
    # Always-allowed script domains (app + Leaflet + Chart.js)
    script_domains = ["'self'", "cdn.jsdelivr.net", "pagead2.googlesyndication.com"]
    # Always-allowed frame domains (AdSense iframes)
    frame_domains = ["https://googleads.g.doubleclick.net"]
    # Extract ad script domains from AD_SCRIPTS config and add them to CSP.
    # This is the only way third-party ad scripts can load — CSP blocks
    # everything else. The domain extraction itself is validated (https://
    # or // prefix, no injection chars) before it reaches this point.
    from urllib.parse import urlparse
    if config.AD_SCRIPTS:
        import json, re
        try:
            data = json.loads(config.AD_SCRIPTS)
            for _placement, url in data.items():
                if not isinstance(url, str):
                    continue
                # Normalise protocol-relative to https for parsing
                full = url if url.startswith("https://") else "https:" + url if url.startswith("//") else ""
                if not full:
                    continue
                domain = urlparse(full).hostname
                if domain and re.match(r'^[a-z0-9.*-]+$', domain, re.IGNORECASE):
                    script_domains.append(domain)
                    frame_domains.append("https://" + domain)
        except (json.JSONDecodeError, TypeError):
            pass
    return (
        "default-src 'self'; "
        f"script-src {' '.join(script_domains)}; "
        "style-src 'self' cdn.jsdelivr.net 'unsafe-inline'; "
        "img-src 'self' data: https://*.basemaps.cartocdn.com https://pagead2.googlesyndication.com; "
        "connect-src 'self' https://cdn.jsdelivr.net; "
        f"frame-src {' '.join(frame_domains)}; "
        "font-src 'self'; "
        "object-src 'none'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'"
    )


_CSP = _build_csp()


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
        # Without an explicit Cache-Control, browsers heuristically cache the
        # static frontend and keep running stale JS after a deploy until a
        # hard refresh. no-cache forces revalidation on every load; unchanged
        # files still return as cheap 304s via StaticFiles' ETag support.
        h.setdefault("Cache-Control", "no-cache")
        # Uncomment when deployed behind HTTPS:
        # h["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
        return response
