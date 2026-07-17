import asyncio
import logging

import httpx
from sqlalchemy.orm import Session

from .config import config
from .models import AlertSent, OutageEvent

log = logging.getLogger(__name__)

# ── Rate limiting ────────────────────────────────────────────────────────────
# Mastodon API: 300 posts / 30 min per-token.  Even a handful of posts per
# cycle is fine, but a burst of simultaneous resolutions shouldn't hammer
# the instance.  Stagger each post by this many seconds.
_POST_INTERVAL_SEC = 2

_COLORS = {"severe": 0xFF3333, "significant": 0xFF8C00, "minor": 0xFFD700}
_EMOJI  = {"severe": "D", "significant": "O", "minor": "Y"}


def _display_title(event: OutageEvent) -> str:
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


def _duration_text(event: OutageEvent) -> str:
    end = event.end_time or event.resolved_at
    if not (event.start_time and end):
        return ""
    mins = int((end - event.start_time).total_seconds()) // 60
    if mins < 1:
        return "under a minute"
    days, rem      = divmod(mins, 1440)
    hours, minutes = divmod(rem, 60)
    parts = []
    if days:                  parts.append(f"{days}d")
    if hours:                 parts.append(f"{hours}h")
    if minutes and not days:  parts.append(f"{minutes}m")
    return " ".join(parts)


async def _send_discord(event: OutageEvent, resolved: bool = False) -> bool:
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
        {"name": "Source",   "value": event.source.upper(),      "inline": True},
        {"name": "Detected", "value": event.start_time.strftime("%Y-%m-%d %H:%M UTC"), "inline": True},
    ]
    if resolved and _duration_text(event):
        fields.append({"name": "Duration", "value": _duration_text(event), "inline": True})
    payload = {
        "embeds": [{
            "title":       title,
            "description": event.description,
            "color":       color,
            "fields":      fields,
            "url":       event.source_url or "",
            # For new alerts: when the outage started.
            # For resolved: when it actually ended (per end_time),
            # not the wall-clock time the system noticed it.
            "timestamp": (event.end_time if resolved else event.start_time).isoformat() if (resolved and event.end_time) or (not resolved and event.start_time) else None,
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


def _hashtags(event: OutageEvent) -> str:
    tags = []
    if config.SITE_HASHTAG:
        site = config.SITE_HASHTAG.strip()
        tags.append(site if site.startswith("#") else f"#{site}")
    for label in (event.country_name, event.region_name, event.event_type):
        if label:
            tags.append(_hashtag(label))
    deduped = list(dict.fromkeys(t for t in tags if t))
    return " ".join(deduped)


def _status_text(event: OutageEvent, resolved: bool = False) -> str:
    """Same format the frontend Share button produces (see app.js _shareText)."""
    parts = []
    if resolved:
        parts.append("✅ Resolved:")
    parts.append(_display_title(event))
    if resolved:
        dur = _duration_text(event)
        if dur:
            parts.append(f"— outage lasted {dur}")
    else:
        qualifiers = " ".join(
            p.title() for p in (event.severity, event.event_type) if p)
        if qualifiers:
            parts.append(f"— {qualifiers}")
    if event.source:
        parts.append(f"(source: {event.source.upper()})")
    # Use real outage timestamps, not the system clock.
    # start_time = when the source first flagged it
    # end_time = last time the source confirmed it active (best estimate
    #   of actual end; falls back to start_time if unknown)
    if resolved and event.start_time:
        ts = event.start_time.strftime("%Y-%m-%d %H:%M UTC")
        end = event.end_time or event.resolved_at
        if end:
            ts += f" – {end.strftime('%Y-%m-%d %H:%M UTC')}"
        parts.append(f"({ts})")
    elif event.start_time:
        parts.append(f"(started {event.start_time.strftime('%Y-%m-%d %H:%M UTC')})")
    text = " ".join(parts) + "."
    if config.PUBLIC_SITE_URL:
        text += f" {config.PUBLIC_SITE_URL.rstrip('/')}/?country={event.country_code}"
    tags = _hashtags(event)
    if tags:
        text += f" {tags}"
    return text


async def _send_mastodon(event: OutageEvent, resolved: bool = False) -> bool:
    instance = config.MASTODON_INSTANCE_URL.rstrip("/")
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(
                f"{instance}/api/v1/statuses",
                headers={
                    "Authorization": f"Bearer {config.MASTODON_ACCESS_TOKEN}",
                    # Instance-side dedupe if the same status is retried.
                    "Idempotency-Key": f"outage-event-{event.id}"
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


async def check_and_send_alerts(db: Session, new_event_ids: list):
    channels = _channels()
    if not channels:
        return
    for eid in new_event_ids:
        ev = db.query(OutageEvent).filter(OutageEvent.id == eid).first()
        if not ev or ev.severity not in ("significant", "severe"):
            continue
        for channel, send in channels:
            already = db.query(AlertSent).filter(
                AlertSent.event_id == eid,
                AlertSent.channel == channel).first()
            if already:
                continue
            if await _rate_limited(send, ev):
                db.add(AlertSent(event_id=eid, channel=channel, message=ev.title))
    db.commit()


async def check_and_send_resolved_alerts(db: Session, resolved_event_ids: list):
    """
    Follow-up "resolved" notice for events whose outage we previously
    announced. Gated on the original alert having been sent on that same
    channel — an outage nobody heard about doesn't get a resolution post.
    """
    channels = _channels()
    if not channels or not resolved_event_ids:
        return
    for eid in resolved_event_ids:
        ev = db.query(OutageEvent).filter(OutageEvent.id == eid).first()
        if not ev or ev.severity not in ("significant", "severe"):
            continue
        for channel, send in channels:
            announced = db.query(AlertSent).filter(
                AlertSent.event_id == eid,
                AlertSent.channel == channel).first()
            if not announced:
                continue
            resolved_channel = f"{channel}-resolved"
            already = db.query(AlertSent).filter(
                AlertSent.event_id == eid,
                AlertSent.channel == resolved_channel).first()
            if already:
                continue
            if await _rate_limited(send, ev, resolved=True):
                db.add(AlertSent(event_id=eid, channel=resolved_channel,
                                 message=f"Resolved: {ev.title}"))
    db.commit()
