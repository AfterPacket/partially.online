import asyncio
import datetime
import json
import logging
import os
import re
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import desc
from sqlalchemy.orm import Session

from .analyzer import get_country_status
from .config import config
from .database import get_db, init_db
from .models import Banner, OutageEvent
from .scheduler import run_api_collection, run_probe_collection, start_scheduler, stop_scheduler
from .security import SecurityHeadersMiddleware, rate_limit, require_admin

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)-24s %(levelname)s  %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_scheduler()
    asyncio.create_task(run_api_collection())
    asyncio.create_task(run_probe_collection())
    yield
    stop_scheduler()


app = FastAPI(
    title="Internet Outage Monitor",
    version="1.0.0",
    lifespan=lifespan,
    # Hide docs on public deployment (set DOCS_URL=None via env if desired)
    docs_url=None,
    redoc_url=None,
)

# ── Middleware (order matters: outermost first) ───────────────────────────────
app.add_middleware(SecurityHeadersMiddleware)

_origins = [o.strip() for o in config.ALLOWED_ORIGINS.split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["*"],
    max_age=600,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _e(ev: OutageEvent) -> dict:
    return {
        "id":              ev.id,
        "country_code":    ev.country_code,
        "country_name":    ev.country_name,
        "region_name":     ev.region_name,
        "title":           ev.title,
        "description":     ev.description,
        "event_type":      ev.event_type,
        "severity":        ev.severity,
        "severity_score":  ev.severity_score,
        "source":          ev.source,
        "source_url":      ev.source_url,
        "actual_value":    ev.actual_value,
        "baseline_value":  ev.baseline_value,
        "start_time":      (ev.start_time.isoformat() + "Z") if ev.start_time else None,
        "end_time":        (ev.end_time.isoformat()   + "Z") if ev.end_time   else None,
        "is_active":       ev.is_active,
        "probe_confirmed": bool(ev.probe_confirmed),
        "resolved":        bool(ev.resolved),
        "resolved_at":     (ev.resolved_at.isoformat() + "Z") if ev.resolved_at else None,
    }


def _cut(hours: int = 24):
    return datetime.datetime.utcnow() - datetime.timedelta(hours=hours)


# ── Public read-only endpoints ────────────────────────────────────────────────

@app.get("/api/status", dependencies=[Depends(rate_limit)])
def api_status(db: Session = Depends(get_db)):
    cut   = _cut(24)
    total = db.query(OutageEvent).filter(
        OutageEvent.is_active.is_(True), OutageEvent.start_time >= cut).count()
    severe = db.query(OutageEvent).filter(
        OutageEvent.is_active.is_(True), OutageEvent.severity == "severe",
        OutageEvent.start_time >= cut).count()
    confirmed = db.query(OutageEvent).filter(
        OutageEvent.is_active.is_(True), OutageEvent.probe_confirmed.is_(True),
        OutageEvent.start_time >= cut).count()
    return {
        "status":        "ok",
        "active_events": total,
        "severe_events": severe,
        "confirmed":     confirmed,
        "last_updated":  datetime.datetime.utcnow().isoformat() + "Z",
        # Consumed by the frontend Share button so posts carry the same
        # site tag as backend Mastodon posts, without hardcoding it in JS.
        "site_hashtag":  config.SITE_HASHTAG,
    }


@app.get("/api/events", dependencies=[Depends(rate_limit)])
def api_events(
    limit:       int  = Query(50, le=200),
    offset:      int  = Query(0, ge=0),
    severity:    str  = None,
    country:     str  = None,
    active_only: bool = True,
    db: Session = Depends(get_db),
):
    q = db.query(OutageEvent)
    if active_only:
        q = q.filter(OutageEvent.is_active.is_(True), OutageEvent.start_time >= _cut(24))
    if severity:
        q = q.filter(OutageEvent.severity == severity)
    if country:
        q = q.filter(OutageEvent.country_code == country.upper())
    total = q.count()
    rows  = q.order_by(desc(OutageEvent.severity_score), desc(OutageEvent.start_time))              .offset(offset).limit(limit).all()
    return {"events": [_e(r) for r in rows], "total": total}


@app.get("/api/countries", dependencies=[Depends(rate_limit)])
def api_countries(db: Session = Depends(get_db)):
    return {"countries": list(get_country_status(db).values())}


@app.get("/api/countries/{code}", dependencies=[Depends(rate_limit)])
def api_country(code: str, db: Session = Depends(get_db)):
    code = code.upper()
    active = (
        db.query(OutageEvent)
        .filter(OutageEvent.country_code == code,
                OutageEvent.is_active.is_(True),
                OutageEvent.start_time >= _cut(24))
        .order_by(desc(OutageEvent.severity_score)).all()
    )
    history = (
        db.query(OutageEvent)
        .filter(OutageEvent.country_code == code,
                OutageEvent.start_time >= _cut(24 * 30))
        .order_by(OutageEvent.start_time).all()
    )
    name   = active[0].country_name if active else (history[0].country_name if history else code)
    status = active[0].severity     if active else "normal"
    daily: dict = {}
    for ev in history:
        day = ev.start_time.strftime("%Y-%m-%d")
        if day not in daily or ev.severity_score > daily[day]["score"]:
            daily[day] = {"date": day, "score": ev.severity_score, "status": ev.severity}
    return {
        "code":          code,
        "name":          name,
        "status":        status,
        "active_events": [_e(e) for e in active],
        "history":       list(daily.values()),
    }


@app.get("/api/countries/{code}/history", dependencies=[Depends(rate_limit)])
def api_country_history(
    code:     str,
    days:     int = Query(30, le=180),
    category: str = Query(None, description="outages | censorship | None (all)"),
    db: Session = Depends(get_db),
):
    """
    Event-level history log for one country — every individual alert
    (active or resolved) in the window, not just the daily-aggregated
    severity trend from /api/countries/{code}. Lets the UI browse actual
    past outages/censorship alerts, e.g. to check whether a specific
    incident (a protest, a reported shutdown) shows up in the data.
    """
    code = code.upper()
    q = db.query(OutageEvent).filter(
        OutageEvent.country_code == code,
        OutageEvent.start_time >= _cut(24 * days),
    )
    if category == "outages":
        q = q.filter(OutageEvent.event_type.in_(["shutdown", "disruption"]))
    elif category == "censorship":
        q = q.filter(OutageEvent.event_type == "censorship")
    rows = q.order_by(desc(OutageEvent.start_time)).limit(500).all()
    return {"code": code, "days": days, "events": [_e(r) for r in rows], "total": len(rows)}


# ── Resolved events ───────────────────────────────────────────────────────────

@app.get("/api/events/resolved", dependencies=[Depends(rate_limit)])
def api_events_resolved(
    days: int = Query(7, le=30),
    db: Session = Depends(get_db),
):
    """Events that were resolved within the last N days (default 7)."""
    since = datetime.datetime.utcnow() - datetime.timedelta(days=days)
    rows = (
        db.query(OutageEvent)
        .filter(
            OutageEvent.resolved.is_(True),
            OutageEvent.resolved_at >= since,
        )
        .order_by(OutageEvent.resolved_at.desc())
        .limit(100)
        .all()
    )
    return {"events": [_e(r) for r in rows], "total": len(rows)}


# ── Public banners ─────────────────────────────────────────────────────────────

@app.get("/api/banners", dependencies=[Depends(rate_limit)])
def api_banners(db: Session = Depends(get_db)):
    """Active banner notices shown to all visitors."""
    rows = (
        db.query(Banner)
        .filter(Banner.active.is_(True))
        .order_by(Banner.created_at.desc())
        .all()
    )
    return {"banners": [_b(r) for r in rows]}


def _b(banner: Banner) -> dict:
    return {
        "id":        banner.id,
        "message":   banner.message,
        "level":     banner.level,
        "active":    bool(banner.active),
        "created_at": (banner.created_at.isoformat() + "Z") if banner.created_at else None,
    }


# ── Advertising ─────────────────────────────────────────────────────────────────
#
# Serves *only* validated placement parameters (IDs, URLs) — never raw HTML
# or script tags. The frontend constructs elements from these values using
# safe DOM APIs (createElement, setAttribute), so there is zero XSS surface
# even if an env var is tampered with. Any value that fails validation is
# silently stripped rather than served.

_GOOGLE_ID_RE = re.compile(r'^ca-pub-\d{16,}$')
_GOOGLE_SLOT_RE = re.compile(r'^\d+$')
# Sponsor script URLs: must be https:// or protocol-relative //, no injection chars.
_SCRIPT_URL_RE = re.compile(r'^(https://|//)[^<>"\'\s]+$')
_PLACEMENTS = {'header', 'sidebar', 'footer'}


def _parse_slots(raw: str, pattern: re.Pattern) -> dict | None:
    """Parse a JSON map of {placement: id}, validating each value."""
    if not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    result = {}
    for key, value in data.items():
        if key not in _PLACEMENTS:
            continue
        if not isinstance(value, str) or not pattern.match(value):
            continue
        result[key] = value
    return result or None


@app.get("/api/placements", dependencies=[Depends(rate_limit)])
def api_placements():
    """Validated placement configuration for the frontend (no HTML, no scripts)."""
    result = {}
    if config.SPONSOR_GOOGLE_ID and _GOOGLE_ID_RE.match(config.SPONSOR_GOOGLE_ID):
        result["google_id"] = config.SPONSOR_GOOGLE_ID
        slots = _parse_slots(config.SPONSOR_GOOGLE_SLOTS, _GOOGLE_SLOT_RE)
        if slots:
            result["google_slots"] = slots
    scripts = _parse_slots(config.SPONSOR_SCRIPTS, _SCRIPT_URL_RE)
    if scripts:
        result["scripts"] = scripts
    return result


# ── Admin endpoints (require X-Admin-Key header, see security.require_admin) ──

@app.post("/api/admin/refresh", dependencies=[Depends(require_admin)])
async def api_admin_refresh():
    """
    Trigger an IODA/OONI/Cloudflare collection cycle immediately, instead of
    waiting for the next scheduled run (every COLLECTION_INTERVAL_MINUTES).
    Runs in the background — this returns as soon as the cycle is queued,
    not once it's finished.
    """
    asyncio.create_task(run_api_collection())
    return {"status": "ok", "message": "API collection cycle triggered"}


@app.post("/api/admin/probe", dependencies=[Depends(require_admin)])
async def api_admin_probe():
    """
    Trigger a Trinocular probe confirmation cycle immediately, instead of
    waiting for the next scheduled run (every PROBE_INTERVAL_MINUTES).
    """
    asyncio.create_task(run_probe_collection())
    return {"status": "ok", "message": "Probe cycle triggered"}


# ── Admin banner management (require X-Admin-Key header) ─────────────────────

from pydantic import BaseModel, Field, field_validator
import re


class BannerCreate(BaseModel):
    message: str = Field(..., min_length=1, max_length=1000)
    level: str   = Field("info", pattern=r"^(info|warning|success)$")
    active: bool  = True

    @field_validator('message')
    @classmethod
    def sanitize_message(cls, v: str) -> str:
        # Strip HTML tags — only plain text and [text](url) links are allowed.
        # The frontend renders [text](url) as safe <a> tags.
        return re.sub(r'<[^>]+>', '', v)


class BannerUpdate(BaseModel):
    message: str | None = Field(None, min_length=1, max_length=1000)
    level: str   | None = Field(None, pattern=r"^(info|warning|success)$")
    active: bool | None = None

    @field_validator('message')
    @classmethod
    def sanitize_message(cls, v: str | None) -> str | None:
        if v is None:
            return v
        return re.sub(r'<[^>]+>', '', v)


@app.get("/api/admin/banners", dependencies=[Depends(require_admin)])
def api_admin_banners(db: Session = Depends(get_db)):
    """List all banners (active and inactive)."""
    rows = db.query(Banner).order_by(Banner.created_at.desc()).all()
    return {"banners": [_b(r) for r in rows]}


@app.post("/api/admin/banners", dependencies=[Depends(require_admin)])
def api_admin_banner_create(body: BannerCreate, db: Session = Depends(get_db)):
    """Create a new banner notice."""
    banner = Banner(message=body.message, level=body.level, active=body.active)
    db.add(banner)
    db.commit()
    db.refresh(banner)
    return _b(banner)


@app.patch("/api/admin/banners/{banner_id}", dependencies=[Depends(require_admin)])
def api_admin_banner_update(banner_id: int, body: BannerUpdate, db: Session = Depends(get_db)):
    """Update a banner (change message, level, or active status)."""
    banner = db.query(Banner).filter(Banner.id == banner_id).first()
    if not banner:
        raise HTTPException(404, "Banner not found")
    if body.message is not None:
        banner.message = body.message
    if body.level is not None:
        banner.level = body.level
    if body.active is not None:
        banner.active = body.active
    db.commit()
    db.refresh(banner)
    return _b(banner)


@app.delete("/api/admin/banners/{banner_id}", dependencies=[Depends(require_admin)])
def api_admin_banner_delete(banner_id: int, db: Session = Depends(get_db)):
    """Delete a banner."""
    banner = db.query(Banner).filter(Banner.id == banner_id).first()
    if not banner:
        raise HTTPException(404, "Banner not found")
    db.delete(banner)
    db.commit()
    return {"status": "ok"}


# ── Static frontend ───────────────────────────────────────────────────────────
_fe = os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend")
if os.path.isdir(_fe):
    app.mount("/", StaticFiles(directory=_fe, html=True), name="static")
