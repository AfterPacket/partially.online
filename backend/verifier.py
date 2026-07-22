"""
Source cross-check (verification) for raw IODA-alert-derived events.

IODA exposes two feeds with very different reliability semantics:

  * /outages/alerts  — RAW, point-in-time threshold crossings. Fast (this is
    what the ioda collector consumes for early warning) but noisy: IODA
    reprocesses its data and RETRACTS raw alerts after the fact, so an alert
    we ingested can silently vanish from the API — leaving our stored event
    unverifiable against the source (confirmed live: the Gaza Strip
    July 2026 alerts disappeared from the raw feed within hours).
  * /outages/events  — CURATED, post-processed outages. Slower to appear but
    persistent, and it is what the IODA dashboard's outage table shows — so
    an event corroborated here can actually be checked by a visitor
    following our "View source" link.

This module closes the loop: it periodically asks the curated feed whether
any published outage overlaps our raw IODA observations, and marks matching
raw rows source_confirmed. The coalescer then surfaces that as
confirmation="source" on the derived event (see coalescer._confirmation).

Matching is deliberately conservative:
  * country events match on exact country code;
  * region events are queried per-country via relatedTo=country/{cc} so two
    same-named regions in different countries (e.g. Punjab IN vs PK) can
    never cross-confirm each other, then matched on exact region name;
  * time windows must overlap, with a small slack for boundary jitter
    between the two feeds.
"""
import datetime
import logging

from sqlalchemy.orm import Session

from .collectors.base import BaseCollector
from .models import OutageEvent

log = logging.getLogger(__name__)

IODA_BASE = "https://api.ioda.inetintel.cc.gatech.edu/v2"

# Curated-event boundaries are quantized to IODA's processing buckets and can
# disagree with raw-alert timestamps by a bucket or two; allow this much slack
# on each side when testing overlap.
OVERLAP_SLACK = datetime.timedelta(hours=1)

# How far back we try to verify raw rows. Matches the raw layer's own 48h
# active-event horizon (see analyzer.expire_old_events).
LOOKBACK = datetime.timedelta(hours=48)


class _CuratedFeed(BaseCollector):
    """Thin async client for the curated /outages/events feed."""
    name = "ioda-verify"

    async def events(self, entity_type: str, since, until, related_to=None):
        params = {
            "from":       int(since.timestamp()),
            "until":      int(until.timestamp()),
            "entityType": entity_type,
            "limit":      500,
        }
        if related_to:
            params["relatedTo"] = related_to
        data = await self._get(f"{IODA_BASE}/outages/events", params=params)
        return data.get("data") or []


def parse_curated(entries: list, country_code: str | None = None) -> list:
    """
    Normalize curated CODF entries into (cc, region_name, start, end) tuples.

    ``location`` is "country/XX" or "region/<entity id>"; region entries carry
    the human name in location_name but NOT their country, so region queries
    must be issued per-country (relatedTo) and the cc passed in here.
    """
    out = []
    for e in entries:
        loc = e.get("location") or ""
        start = e.get("start")
        if start is None:
            continue
        try:
            begin = datetime.datetime.utcfromtimestamp(float(start))
            end = begin + datetime.timedelta(seconds=float(e.get("duration") or 0))
        except (TypeError, ValueError, OverflowError):
            continue
        if loc.startswith("country/"):
            out.append((loc.split("/", 1)[1].upper(), None, begin, end))
        elif loc.startswith("region/") and country_code:
            name = (e.get("location_name") or "").strip()
            if name:
                out.append((country_code.upper(), name, begin, end))
    return out


def match_confirmations(rows: list, curated: list, slack=OVERLAP_SLACK) -> list:
    """
    Pure matching: return the subset of raw OutageEvent rows corroborated by a
    curated entry — same (country, region) key and overlapping time window
    (with ``slack`` widening the curated window on both sides).
    """
    matched = []
    for r in rows:
        r_start = r.start_time or r.created_at
        if r_start is None:
            continue
        r_end = r.end_time or r.resolved_at or r.updated_at or r_start
        if r_end < r_start:
            r_end = r_start
        for (cc, region, c_start, c_end) in curated:
            if cc != (r.country_code or "").upper():
                continue
            if (region or None) != (r.region_name or None):
                continue
            if r_start <= c_end + slack and r_end >= c_start - slack:
                matched.append(r)
                break
    return matched


async def crosscheck_ioda(db: Session, now=None) -> int:
    """
    Mark recent raw IODA rows as source_confirmed when the curated feed
    corroborates them. Returns the number of rows newly confirmed. Never
    raises — a failed cross-check must not block the collection cycle; rows
    simply stay unconfirmed until a later pass succeeds.
    """
    now   = now or datetime.datetime.utcnow()
    since = now - LOOKBACK

    rows = (
        db.query(OutageEvent)
        .filter(
            OutageEvent.source == "ioda",
            OutageEvent.source_confirmed.is_(False),
            OutageEvent.start_time >= since,
        )
        .all()
    )
    if not rows:
        return 0

    feed = _CuratedFeed()
    try:
        curated: list = []
        # One country-level query covers all country-wide rows.
        if any(r.region_name is None for r in rows):
            entries = await feed.events("country", since, now)
            curated.extend(parse_curated(entries))
        # Region rows: one precise per-country query each (relatedTo scoping
        # prevents same-named regions in different countries cross-matching).
        for cc in sorted({r.country_code for r in rows if r.region_name}):
            entries = await feed.events("region", since, now,
                                        related_to=f"country/{cc}")
            curated.extend(parse_curated(entries, country_code=cc))

        confirmed = match_confirmations(rows, curated)
        for r in confirmed:
            r.source_confirmed = True
        if confirmed:
            db.commit()
            log.info(f"[verifier] {len(confirmed)} raw IODA row(s) corroborated "
                     f"by the curated outage feed")
        return len(confirmed)
    except Exception as exc:
        log.warning(f"[verifier] cross-check failed (will retry next cycle): {exc}")
        return 0
    finally:
        try:
            await feed.close()
        except Exception:
            pass
