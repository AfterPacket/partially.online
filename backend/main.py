import asyncio
import datetime
import json
import logging
import os
import re
from contextlib import asynccontextmanager
from html import escape as _esc

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import desc
from sqlalchemy.orm import Session

from .coalescer import backfill, country_status, duration_label, span_seconds
from .config import config
from .database import SessionLocal, get_db, init_db
from .models import Banner, CoalescedEvent
from .scheduler import run_api_collection, run_probe_collection, start_scheduler, stop_scheduler
from .security import SecurityHeadersMiddleware, rate_limit, require_admin

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)-24s %(levelname)s  %(message)s",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # First run after enabling the coalesced layer: derive it from all raw
    # history so the 30-day feed/counts are correct immediately, and mark
    # pre-existing incidents as already-announced so we don't retro-blast
    # social posts. Idempotent — a no-op once the table is populated.
    db = SessionLocal()
    try:
        if db.query(CoalescedEvent).count() == 0:
            backfill(db)
    except Exception as exc:
        logging.getLogger(__name__).error(f"[startup] backfill failed: {exc}")
    finally:
        db.close()
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

def _iso(dt):
    return (dt.isoformat() + "Z") if dt else None


def _e(ev: CoalescedEvent) -> dict:
    """
    Serialize a coalesced event. start_time/end_time keep their old names for
    frontend compatibility but now carry the honest OBSERVED window; the
    explicit observed_* / ongoing / duration_label fields are the ones new code
    should read. end_time is only set once the event has truly closed.
    """
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
        "sources":         ev.sources,
        "source_url":      ev.source_url,
        "actual_value":    ev.actual_value,
        "baseline_value":  ev.baseline_value,
        "drop_pct":        ev.drop_pct,
        "sample_count":    ev.sample_count,
        # Honest observed window (never raw sample spacing).
        "start_time":      _iso(ev.observed_start),
        "end_time":        _iso(ev.observed_end) if ev.resolved else None,
        "observed_start":  _iso(ev.observed_start),
        "observed_end":    _iso(ev.observed_end) if ev.resolved else None,
        "observed_span_seconds": span_seconds(ev.observed_start, ev.observed_end) if ev.resolved else None,
        "duration_label":  duration_label(ev),
        "ongoing":         bool(ev.is_active),
        "is_active":       bool(ev.is_active),
        "probe_confirmed": bool(ev.probe_confirmed),
        # Two-tier confidence (see coalescer._confirmation):
        # source | probe | multi-source | magnitude | unconfirmed.
        "confirmation":    ev.confirmation or "unconfirmed",
        "confirmed":       (ev.confirmation or "unconfirmed") != "unconfirmed",
        "resolved":        bool(ev.resolved),
        "resolved_at":     _iso(ev.resolved_at),
    }


def _cut(hours: int = 24):
    return datetime.datetime.utcnow() - datetime.timedelta(hours=hours)


# ── Public read-only endpoints ────────────────────────────────────────────────

@app.get("/api/status", dependencies=[Depends(rate_limit)])
def api_status(db: Session = Depends(get_db)):
    cut   = _cut(24)
    total = db.query(CoalescedEvent).filter(
        CoalescedEvent.is_active.is_(True), CoalescedEvent.observed_start >= cut,
        CoalescedEvent.severity != "normal").count()
    # Use country_status for the severe count so it matches the map:
    # a country with 3+ significant events is escalated to severe.
    cs = country_status(db)
    severe = sum(d["active_events"] for d in cs.values() if d["status"] == "severe")
    # "Confirmed" = any independent corroboration tier (source cross-check,
    # probe, multi-source, or self-evident magnitude) — not just probes.
    confirmed = db.query(CoalescedEvent).filter(
        CoalescedEvent.is_active.is_(True),
        CoalescedEvent.confirmation.isnot(None),
        CoalescedEvent.confirmation != "unconfirmed",
        CoalescedEvent.observed_start >= cut).count()
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
    q = db.query(CoalescedEvent)
    if active_only:
        q = q.filter(CoalescedEvent.is_active.is_(True), CoalescedEvent.observed_start >= _cut(24))
    if severity:
        q = q.filter(CoalescedEvent.severity == severity)
    else:
        # "normal" == normal-variance (a sub-MINOR_PCT dip); not a real event.
        q = q.filter(CoalescedEvent.severity != "normal")
    if country:
        q = q.filter(CoalescedEvent.country_code == country.upper())
    total = q.count()
    rows  = q.order_by(desc(CoalescedEvent.severity_score), desc(CoalescedEvent.observed_start))              .offset(offset).limit(limit).all()
    return {"events": [_e(r) for r in rows], "total": total}


@app.get("/api/countries", dependencies=[Depends(rate_limit)])
def api_countries(db: Session = Depends(get_db)):
    return {"countries": list(country_status(db).values())}


@app.get("/api/countries/{code}", dependencies=[Depends(rate_limit)])
def api_country(code: str, db: Session = Depends(get_db)):
    code = code.upper()
    active = (
        db.query(CoalescedEvent)
        .filter(CoalescedEvent.country_code == code,
                CoalescedEvent.is_active.is_(True),
                CoalescedEvent.observed_start >= _cut(24),
                CoalescedEvent.severity != "normal")
        .order_by(desc(CoalescedEvent.severity_score)).all()
    )
    history = (
        db.query(CoalescedEvent)
        .filter(CoalescedEvent.country_code == code,
                CoalescedEvent.observed_start >= _cut(24 * 30))
        .order_by(CoalescedEvent.observed_start).all()
    )
    name   = active[0].country_name if active else (history[0].country_name if history else code)
    # Use country_status for the overall status so the detail panel matches
    # the map: a country with 3+ significant events is escalated to severe.
    cs = country_status(db)
    entry = cs.get(code)
    if entry:
        status = entry["status"]
    elif active:
        status = active[0].severity
    else:
        status = "normal"
    daily: dict = {}
    for ev in history:
        day = ev.observed_start.strftime("%Y-%m-%d")
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
    q = db.query(CoalescedEvent).filter(
        CoalescedEvent.country_code == code,
        CoalescedEvent.observed_start >= _cut(24 * days),
    )
    if category == "outages":
        q = q.filter(CoalescedEvent.event_type.in_(["shutdown", "disruption"]))
    elif category == "censorship":
        q = q.filter(CoalescedEvent.event_type == "censorship")
    rows = q.order_by(desc(CoalescedEvent.observed_start)).limit(500).all()
    return {"code": code, "days": days, "events": [_e(r) for r in rows], "total": len(rows)}


# ── Resolved events ───────────────────────────────────────────────────────────

@app.get("/api/events/resolved", dependencies=[Depends(rate_limit)])
def api_events_resolved(
    days: int = Query(7, le=30),
    db: Session = Depends(get_db),
):
    """Events that were resolved within the last N days (default 7)."""
    since = datetime.datetime.utcnow() - datetime.timedelta(days=days)
    q = (
        db.query(CoalescedEvent)
        .filter(
            CoalescedEvent.resolved.is_(True),
            CoalescedEvent.resolved_at >= since,
            CoalescedEvent.severity != "normal",
        )
    )
    # True window count — the list itself is capped at 100 for payload size,
    # so len(rows) would peg at exactly 100 on a busy week.
    total = q.count()
    rows = q.order_by(CoalescedEvent.resolved_at.desc()).limit(100).all()
    return {"events": [_e(r) for r in rows], "total": total}


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


@app.post("/api/admin/backfill", dependencies=[Depends(require_admin)])
def api_admin_backfill(db: Session = Depends(get_db)):
    """
    Re-coalesce ALL historical raw observations into the derived CoalescedEvent
    layer, retroactively correcting inflated 30-day counts and dishonest
    durations. Idempotent; pre-existing incidents are marked already-announced
    so this never triggers a burst of social posts. Runs synchronously and
    returns a summary.
    """
    return {"status": "ok", **backfill(db)}


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
_INDEX_PATH = os.path.join(_fe, "index.html")

_SITE_TITLE_DEFAULT = "Internet Outage Monitor"
_SITE_DESC_DEFAULT = (
    "Live map of internet outages, shutdowns and censorship worldwide — "
    "coalesced from OONI, IODA and Cloudflare Radar measurements."
)


def _render_meta() -> str:
    """Build the <head> SEO/social tags from env (see Config.SITE_*).

    Crawlers and social link-unfurlers read the served markup and do NOT run
    our JS, so these must be injected server-side rather than set from app.js.
    Every interpolated value is HTML-escaped.
    """
    title = config.SITE_TITLE or _SITE_TITLE_DEFAULT
    desc  = config.SITE_DESCRIPTION or _SITE_DESC_DEFAULT
    url   = config.PUBLIC_SITE_URL or ""
    image = config.SITE_OG_IMAGE or ""
    # Social platforms want an absolute og:image; join a site-relative path
    # with the known base URL when we can.
    if image and url and image.startswith("/"):
        image = url.rstrip("/") + image

    t, d = _esc(title), _esc(desc)
    card = "summary_large_image" if image else "summary"
    tags = [f"<title>{t}</title>",
            f'<meta name="description" content="{d}"/>']
    if url:
        tags.append(f'<link rel="canonical" href="{_esc(url)}"/>')
    tags += ['<meta property="og:type" content="website"/>',
             f'<meta property="og:site_name" content="{t}"/>',
             f'<meta property="og:title" content="{t}"/>',
             f'<meta property="og:description" content="{d}"/>']
    if url:
        tags.append(f'<meta property="og:url" content="{_esc(url)}"/>')
    tags += [f'<meta name="twitter:card" content="{card}"/>',
             f'<meta name="twitter:title" content="{t}"/>',
             f'<meta name="twitter:description" content="{d}"/>']
    if image:
        im = _esc(image)
        tags += [f'<meta property="og:image" content="{im}"/>',
                 f'<meta name="twitter:image" content="{im}"/>']
    return "\n  ".join(tags)


# Confirmation-tier labels, mirroring app.js _confirmTagHTML — the server render
# needs the same text so crawlers/no-JS visitors see the honest confidence tier.
_CONF_LABEL = {
    "source":       "&#10003; source verified",
    "probe":        "&#10003; probe confirmed",
    "multi-source": "&#10003; multi-source",
    "magnitude":    "&#10003; signal collapse",
}


def _safe_class(s) -> str:
    """Strip to a CSS-class-safe token (mirrors app.js safeClass)."""
    return re.sub(r"[^a-zA-Z0-9-]", "", str(s or ""))


def _events_html(db: Session) -> str:
    """Server-render the current active events into #event-list.

    Crawlers and no-JS visitors get real, indexable content (country names,
    event titles, types, honest observed windows); app.js overwrites this on
    load via innerHTML. Mirrors /api/events' default query, sort and card
    markup so there's no visible reshuffle when the live JS hydrates.
    """
    rows = (
        db.query(CoalescedEvent)
        .filter(CoalescedEvent.is_active.is_(True),
                CoalescedEvent.observed_start >= _cut(24),
                CoalescedEvent.severity != "normal")
        .order_by(desc(CoalescedEvent.severity_score), desc(CoalescedEvent.observed_start))
        .limit(200).all()
    )
    if not rows:
        return '<div class="empty">No active internet outages right now.</div>'

    cards = []
    for row in rows:
        ev = _e(row)
        conf = ev["confirmation"]
        conf_html = (f'<span class="probe-tag">{_CONF_LABEL[conf]}</span>'
                     if conf in _CONF_LABEL
                     else '<span class="probe-tag probe-unconfirmed">unconfirmed</span>')
        region = (f'<span class="region-tag">{_esc(ev["region_name"])}</span>'
                  if ev["region_name"] else "")
        sources = _esc((ev["sources"] or ev["source"] or "").upper())
        cards.append(
            f'<div class="event-card" data-country="{_esc(ev["country_code"])}">'
            f'<div class="ec-top">'
            f'<span class="sev-pip sev-{_safe_class(ev["severity"])}"></span>'
            f'<span class="ec-title">{_esc(ev["title"])}</span>'
            f'</div>'
            f'<div class="ec-meta">'
            f'<span class="type-tag">{_esc(ev["event_type"])}</span>'
            f'{region}'
            f'<span>{_esc(ev["country_name"])}</span>'
            f'<span class="source-tag">{sources}</span>'
            f'{conf_html}'
            f'<span class="ec-when" style="margin-left:auto">{_esc(ev["duration_label"] or "")}</span>'
            f'</div>'
            f'</div>'
        )
    return "".join(cards)


def _index_html(events_html: str) -> str:
    """index.html with the <!--META--> and <!--EVENTS--> placeholders filled."""
    try:
        with open(_INDEX_PATH, encoding="utf-8") as fh:
            doc = fh.read()
    except OSError:
        return f"<!doctype html><title>{_esc(_SITE_TITLE_DEFAULT)}</title>"
    return (doc.replace("<!--META-->", _render_meta())
               .replace("<!--EVENTS-->", events_html))


@app.get("/robots.txt", include_in_schema=False)
def robots_txt() -> PlainTextResponse:
    base = (config.PUBLIC_SITE_URL or "").rstrip("/")
    body = "User-agent: *\nAllow: /\n"
    if base:
        body += f"Sitemap: {base}/sitemap.xml\n"
    return PlainTextResponse(body)


@app.get("/sitemap.xml", include_in_schema=False)
def sitemap_xml() -> Response:
    # Homepage only for now; per-country pages (/country/{code}) get added here
    # when that feature lands — see the internal roadmap. Needs PUBLIC_SITE_URL
    # for absolute <loc>s; without it we emit a valid but empty urlset.
    base = (config.PUBLIC_SITE_URL or "").rstrip("/")
    today = datetime.date.today().isoformat()
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    if base:
        lines.append(f"  <url><loc>{_esc(base)}/</loc><lastmod>{today}</lastmod>"
                     f"<changefreq>hourly</changefreq><priority>1.0</priority></url>")
    lines.append("</urlset>")
    return Response("\n".join(lines) + "\n", media_type="application/xml")


if os.path.isdir(_fe):
    # Serve "/" and /index.html through the injection layer so the meta tags and
    # server-rendered events are present in the markup; StaticFiles still handles
    # css/js/other assets.
    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    @app.get("/index.html", response_class=HTMLResponse, include_in_schema=False)
    def serve_index() -> HTMLResponse:
        # A dead DB must never take the homepage down: fall back to the JS
        # loading state (app.js fetches /api/events regardless) and still ship
        # the meta tags.
        events_html = '<div class="loading">Loading events&hellip;</div>'
        try:
            db = SessionLocal()
            try:
                events_html = _events_html(db)
            finally:
                db.close()
        except Exception:
            logging.getLogger(__name__).exception("[serve_index] event render failed")
        return HTMLResponse(_index_html(events_html))

    app.mount("/", StaticFiles(directory=_fe, html=True), name="static")
