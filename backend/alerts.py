import asyncio
import logging

import httpx
from sqlalchemy.orm import Session

from .coalescer import duration_label, span_label
from .config import config
from .models import CoalescedAlert, CoalescedEvent

log = logging.getLogger(__name__)

# ── Rate limiting ────────────────────────────────────────────────────────────
# Mastodon API: 300 posts / 30 min per-token.  Even a handful of posts per
# cycle is fine, but a burst of simultaneous resolutions shouldn't hammer
# the instance.  Stagger each post by this many seconds.
_POST_INTERVAL_SEC = 2

_COLORS = {"severe": 0xFF3333, "significant": 0xFF8C00, "minor": 0xFFD700}
_EMOJI  = {"severe": "D", "significant": "O", "minor": "Y"}


def _display_title(event: CoalescedEvent) -> str:
    """
    Event title, guaranteed to name the region for region-scoped events.
    IODA region titles already embed the region; this is a safety net so a
    region event from any source can never post an ambiguous country-only
    title — two regions of the same country would read as duplicate posts.
    """
    title = event.title or ""
    region = (event.region_name or "").strip()
    if region and region.lower() not in title.lower():
        title = f"{title} ({region})"
    return title


def _duration_text(event: CoalescedEvent) -> str:
    """Span of the OBSERVED window (first->last anomalous sample), never the
    raw sample spacing. Empty while the event is still ongoing."""
    if event.is_active or not event.observed_end:
        return ""
    return span_label(event.observed_start, event.observed_end)


async def _send_discord(event: CoalescedEvent, resolved: bool = False) -> bool:
    if resolved:
        title = f"[R] Resolved: {_display_title(event)}"
        color = 0x2ECC71
    else:
        emoji = _EMOJI.get(event.severity, "W")
        title = f"[{emoji}] {_display_title(event)}"
        color = _COLORS.get(event.severity, 0x808080)
    fields = [
        {"name": "Country",  "value": f"{event.country_name} ({event.country_code})", "inline": True},
        {"name": "Type",     "value": event.event_type.title(),  "inline": True},
        {"name": "Severity", "value": event.severity.title(),    "inline": True},
        {"name": "Source",   "value": (event.sources or event.source or "").upper(), "inline": True},
        {"name": "Detected", "value": event.observed_start.strftime("%Y-%m-%d %H:%M UTC"), "inline": True},
    ]
    if resolved and _duration_text(event):
        # Duration = OBSERVED window span, never raw sample spacing.
        fields.append({"name": "Observed window", "value": _duration_text(event), "inline": True})
    payload = {
        "embeds": [{
            "title":       title,
            "description": event.description,
            "color":       color,
            "fields":      fields,
            "url":       event.source_url or "",
            # New alert: when the outage was first observed (observed_start).
            # Resolved: last time it was observed active (observed_end), not the
            # wall-clock time the system noticed the recovery.
            "timestamp": (event.observed_end if resolved else event.observed_start).isoformat()
                          if (event.observed_end if resolved else event.observed_start) else None,
        }]
    }
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(config.ALERT_WEBHOOK_URL, json=payload)
            r.raise_for_status()
        log.info(f"[alerts] Discord {'resolution' if resolved else 'alert'} sent "
                 f"for {event.country_code} event {event.id}")
        return True
    except Exception as exc:
        log.error(f"[alerts] Discord failed: {exc}")
        return False


async def _rate_limited(send_fn, *args, **kwargs) -> bool:
    """Call an async send function, then pause to avoid API spam."""
    try:
        result = await send_fn(*args, **kwargs)
        await asyncio.sleep(_POST_INTERVAL_SEC)
        return result
    except Exception as exc:
        log.error(f"[alerts] Send failed: {exc}")
        return False


def _hashtag(label: str) -> str:
    """CamelCase a label into a hashtag: 'Jammu & Kashmir' -> '#JammuKashmir'."""
    words = "".join(ch if ch.isalnum() else " " for ch in label).split()
    tag = "".join(w[:1].upper() + w[1:] for w in words)
    return f"#{tag}" if tag else ""


def _hashtags(event: CoalescedEvent) -> str:
    tags = []
    if config.SITE_HASHTAG:
        site = config.SITE_HASHTAG.strip()
        tags.append(site if site.startswith("#") else f"#{site}")
    for label in (event.country_name, event.region_name, event.event_type):
        if label:
            tags.append(_hashtag(label))
    deduped = list(dict.fromkeys(t for t in tags if t))
    return " ".join(deduped)


def _status_text(event: CoalescedEvent, resolved: bool = False) -> str:
    """
    Honest social text. An open event reads "ongoing since <UTC>"; a resolved
    event reads "observed HH:MM–HH:MM UTC (span)". We never claim a fixed
    "outage lasted Nm" for what is really the OBSERVED window between spaced
    measurements.
    """
    parts = []
    if resolved:
        parts.append("✅ Resolved:")
    parts.append(_display_title(event))
    if not resolved:
        qualifiers = " ".join(p.title() for p in (event.severity, event.event_type) if p)
        if qualifiers:
            parts.append(f"— {qualifiers}")
    # Honest window: "ongoing since ..." (open) or "observed ...–... UTC (span)".
    parts.append(f"— {duration_label(event)}")
    src = event.sources or event.source
    if src:
        parts.append(f"(source: {src.upper()})")
    text = " ".join(parts) + "."
    if config.PUBLIC_SITE_URL:
        text += f" {config.PUBLIC_SITE_URL.rstrip('/')}/?country={event.country_code}"
    tags = _hashtags(event)
    if tags:
        text += f" {tags}"
    return text


async def _send_mastodon(event: CoalescedEvent, resolved: bool = False) -> bool:
    instance = config.MASTODON_INSTANCE_URL.rstrip("/")
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(
                f"{instance}/api/v1/statuses",
                headers={
                    "Authorization": f"Bearer {config.MASTODON_ACCESS_TOKEN}",
                    # Instance-side dedupe if the same status is retried.
                    "Idempotency-Key": f"coalesced-event-{event.id}"
                                       + ("-resolved" if resolved else ""),
                },
                data={
                    "status":     _status_text(event, resolved=resolved),
                    "visibility": config.MASTODON_VISIBILITY,
                },
            )
            r.raise_for_status()
        log.info(f"[alerts] Mastodon {'resolution' if resolved else 'post'} sent "
                 f"for {event.country_code} event {event.id}")
        return True
    except Exception as exc:
        log.error(f"[alerts] Mastodon failed: {exc}")
        return False


def _channels():
    channels = []
    if config.ALERT_WEBHOOK_URL:
        channels.append(("discord", _send_discord))
    if config.MASTODON_INSTANCE_URL and config.MASTODON_ACCESS_TOKEN:
        channels.append(("mastodon", _send_mastodon))
    return channels


async def check_and_send_alerts(db: Session, event_ids: list):
    """
    Post an "opened" notice, at most once per coalesced event per channel. Safe
    to call every cycle with all currently-active alert-worthy ids: the
    CoalescedAlert dedup skips anything already announced.
    """
    channels = _channels()
    if not channels or not event_ids:
        return
    for eid in event_ids:
        ev = db.query(CoalescedEvent).filter(CoalescedEvent.id == eid).first()
        if not ev or ev.severity not in ("significant", "severe"):
            continue
        # Never publicly announce an event nothing corroborates: raw source
        # alerts get retracted after reprocessing (see backend/verifier.py),
        # and a post about a retracted alert can't be walked back. The ids
        # are re-offered every cycle, so the open post simply fires on the
        # first cycle where corroboration (source/probe/multi-source/
        # magnitude) exists — a real collapse is magnitude-confirmed at once.
        if (ev.confirmation or "unconfirmed") == "unconfirmed":
            continue
        for channel, send in channels:
            already = db.query(CoalescedAlert).filter(
                CoalescedAlert.event_id == eid,
                CoalescedAlert.channel == channel).first()
            if already:
                continue
            if await _rate_limited(send, ev):
                db.add(CoalescedAlert(event_id=eid, channel=channel, message=ev.title))
    db.commit()


async def check_and_send_resolved_alerts(db: Session, resolved_event_ids: list):
    """
    Follow-up "resolved" notice for a coalesced event whose outage we previously
    announced. Gated on the original open post having been sent on that same
    channel — an outage nobody heard about doesn't get a resolution post — and
    itself deduped so it fires at most once.
    """
    channels = _channels()
    if not channels or not resolved_event_ids:
        return
    for eid in resolved_event_ids:
        ev = db.query(CoalescedEvent).filter(CoalescedEvent.id == eid).first()
        if not ev or ev.severity not in ("significant", "severe"):
            continue
        for channel, send in channels:
            announced = db.query(CoalescedAlert).filter(
                CoalescedAlert.event_id == eid,
                CoalescedAlert.channel == channel).first()
            if not announced:
                continue
            resolved_channel = f"{channel}-resolved"
            already = db.query(CoalescedAlert).filter(
                CoalescedAlert.event_id == eid,
                CoalescedAlert.channel == resolved_channel).first()
            if already:
                continue
            if await _rate_limited(send, ev, resolved=True):
                db.add(CoalescedAlert(event_id=eid, channel=resolved_channel,
                                      message=f"Resolved: {ev.title}"))
    db.commit()
