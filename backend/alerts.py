import logging

import httpx
from sqlalchemy.orm import Session

from .config import config
from .models import AlertSent, OutageEvent

log = logging.getLogger(__name__)

_COLORS = {"severe": 0xFF3333, "significant": 0xFF8C00, "minor": 0xFFD700}
_EMOJI  = {"severe": "D", "significant": "O", "minor": "Y"}


async def _send_discord(event: OutageEvent):
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
    except Exception as exc:
        log.error(f"[alerts] Discord failed: {exc}")


async def check_and_send_alerts(db: Session, new_event_ids: list):
    if not config.ALERT_WEBHOOK_URL:
        return
    for eid in new_event_ids:
        ev = db.query(OutageEvent).filter(OutageEvent.id == eid).first()
        if not ev or ev.severity not in ("significant", "severe"):
            continue
        if db.query(AlertSent).filter(AlertSent.event_id == eid).first():
            continue
        await _send_discord(ev)
        db.add(AlertSent(event_id=eid, channel="discord", message=ev.title))
    db.commit()
