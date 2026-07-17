import logging

import httpx
from sqlalchemy.orm import Session

from .config import config
from .models import AlertSent, OutageEvent

log = logging.getLogger(__name__)

_COLORS = {"severe": 0xFF3333, "significant": 0xFF8C00, "minor": 0xFFD700}
_EMOJI  = {"severe": "D", "significant": "O", "minor": "Y"}


async def _send_discord(event: OutageEvent) -> bool:
    emoji = _EMOJI.get(event.severity, "W")
    payload = {
        "embeds": [{
            "title":       f"[{emoji}] {event.title}",
            "description": event.description,
            "color":       _COLORS.get(event.severity, 0x808080),
            "fields": [
                {"name": "Country",  "value": f"{event.country_name} ({event.country_code})", "inline": True},
                {"name": "Type",     "value": event.event_type.title(),  "inline": True},
                {"name": "Severity", "value": event.severity.title(),    "inline": True},
                {"name": "Source",   "value": event.source.upper(),      "inline": True},
                {"name": "Detected", "value": event.start_time.strftime("%Y-%m-%d %H:%M UTC"), "inline": True},
            ],
            "url":       event.source_url or "",
            "timestamp": event.start_time.isoformat(),
        }]
    }
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(config.ALERT_WEBHOOK_URL, json=payload)
            r.raise_for_status()
        log.info(f"[alerts] Discord alert sent for {event.country_code} event {event.id}")
        return True
    except Exception as exc:
        log.error(f"[alerts] Discord failed: {exc}")
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


def _status_text(event: OutageEvent) -> str:
    """Same format the frontend Share button produces (see app.js _shareText)."""
    parts = [event.title]
    qualifiers = " ".join(
        p.title() for p in (event.severity, event.event_type) if p)
    if qualifiers:
        parts.append(f"— {qualifiers}")
    if event.source:
        parts.append(f"(source: {event.source.upper()})")
    text = " ".join(parts) + "."
    if config.PUBLIC_SITE_URL:
        text += f" {config.PUBLIC_SITE_URL.rstrip('/')}/?country={event.country_code}"
    tags = _hashtags(event)
    if tags:
        text += f" {tags}"
    return text


async def _send_mastodon(event: OutageEvent) -> bool:
    instance = config.MASTODON_INSTANCE_URL.rstrip("/")
    try:
        async with httpx.AsyncClient() as c:
            r = await c.post(
                f"{instance}/api/v1/statuses",
                headers={
                    "Authorization": f"Bearer {config.MASTODON_ACCESS_TOKEN}",
                    # Instance-side dedupe if the same status is retried.
                    "Idempotency-Key": f"outage-event-{event.id}",
                },
                data={
                    "status":     _status_text(event),
                    "visibility": config.MASTODON_VISIBILITY,
                },
            )
            r.raise_for_status()
        log.info(f"[alerts] Mastodon post sent for {event.country_code} event {event.id}")
        return True
    except Exception as exc:
        log.error(f"[alerts] Mastodon failed: {exc}")
        return False


async def check_and_send_alerts(db: Session, new_event_ids: list):
    channels = []
    if config.ALERT_WEBHOOK_URL:
        channels.append(("discord", _send_discord))
    if config.MASTODON_INSTANCE_URL and config.MASTODON_ACCESS_TOKEN:
        channels.append(("mastodon", _send_mastodon))
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
            if await send(ev):
                db.add(AlertSent(event_id=eid, channel=channel, message=ev.title))
    db.commit()
