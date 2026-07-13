import datetime
import logging

from sqlalchemy.dialects.sqlite import insert as sqlite_upsert
from sqlalchemy.orm import Session

from .models import CountryBelief, OutageEvent

log = logging.getLogger(__name__)


def upsert_events(db: Session, new_events: list) -> int:
    added = 0
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(hours=48)
    for ev in new_events:
        if not ev.get("country_code"):
            continue
        region = ev.get("region_name")
        q = db.query(OutageEvent).filter(
            OutageEvent.country_code == ev["country_code"],
            OutageEvent.source == ev["source"],
            OutageEvent.event_type == ev["event_type"],
            OutageEvent.is_active.is_(True),
            OutageEvent.start_time >= cutoff,
        )
        # A region-scoped alert (e.g. Jammu & Kashmir) must never be matched
        # against — and silently overwritten by — a country-wide alert for
        # the same country/source/type, or vice versa. Match NULL-to-NULL
        # and same-region-to-same-region only.
        q = q.filter(OutageEvent.region_name == region) if region \
            else q.filter(OutageEvent.region_name.is_(None))
        existing = q.first()
        if existing:
            existing.severity       = ev["severity"]
            existing.severity_score = ev["severity_score"]
            existing.description    = ev["description"]
            existing.actual_value   = ev.get("actual_value")
            existing.baseline_value = ev.get("baseline_value")
            existing.updated_at     = datetime.datetime.utcnow()
        else:
            db.add(OutageEvent(**ev))
            added += 1
            _reset_belief_for_new_event(db, ev["country_code"])
    db.commit()
    return added


def _reset_belief_for_new_event(db: Session, country_code: str):
    """
    A brand-new event means a source just flagged this country as disrupted
    right now. If we left its Trinocular belief at whatever it was before —
    the optimistic 0.99 startup default, or a stale belief left over from an
    unrelated earlier resolution — a single negative probe next round isn't
    enough to overturn that strong prior (correct Bayesian behavior in
    isolation, see collectors/trinocular.py), so the round can read as
    "confirmed up" off one bad-for-it probe, on the very cycle the event was
    created. Left unfixed, that produces exactly the flapping duplicate-row
    pattern seen in production: the event resolves on a stale prior, the
    source reports the same ongoing outage again next cycle, finds no active
    row to update, creates a new one, and the cycle repeats. Resetting to a
    neutral 0.5 forces fresh evidence to be gathered before either
    conclusion is reached.

    Uses an atomic INSERT ... ON CONFLICT DO UPDATE rather than a
    check-then-insert (query for a row, add() if missing): a country with
    both a country-wide and a region-scoped alert in the same batch (or
    several region alerts at once) calls this more than once for the same
    country_code within one upsert_events() loop, in one uncommitted
    session. With autoflush=False (see database.py), a plain SELECT run
    twice for the same key doesn't see the first call's still-pending add(),
    so both conclude "no row exists" and both try to insert one — a
    sqlite UNIQUE constraint violation that rolls back the entire batch.
    The atomic upsert has no such window: the second call correctly sees
    the first call's row (even uncommitted, within the same transaction)
    and updates it instead of colliding with it. availability is
    deliberately left out of the update clause so a reset never clobbers a
    previously-learned response rate — it's only ever seeded on first insert.
    """
    stmt = sqlite_upsert(CountryBelief).values(
        country_code=country_code, belief_up=0.5, availability=0.9, state="uncertain",
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=[CountryBelief.country_code],
        set_={"belief_up": 0.5, "state": "uncertain"},
    )
    db.execute(stmt)


def get_country_status(db: Session) -> dict:
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(hours=24)
    events = (
        db.query(OutageEvent)
        .filter(OutageEvent.is_active.is_(True), OutageEvent.start_time >= cutoff)
        .all()
    )
    status: dict = {}
    for ev in events:
        cc = ev.country_code
        if cc not in status or ev.severity_score > status[cc]["score"]:
            ts = ev.updated_at or ev.created_at
            status[cc] = {
                "code": cc,
                "name": ev.country_name,
                "status": ev.severity,
                "score": ev.severity_score,
                "active_events": 0,
                "last_updated": ts.isoformat(),
            }
        status[cc]["active_events"] += 1
    return status


def expire_old_events(db: Session):
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(hours=48)
    # Expired events: set end_time to updated_at (last time the source
    # confirmed the outage was still active) rather than the cutoff time,
    # since the outage actually ended before the 48h cutoff was reached.
    events = (
        db.query(OutageEvent)
        .filter(
            OutageEvent.is_active.is_(True),
            OutageEvent.start_time < cutoff,
        )
        .all()
    )
    for ev in events:
        ev.is_active = False
        ev.resolved = True
        ev.resolved_at = datetime.datetime.utcnow()
        ev.end_time = ev.updated_at or ev.start_time
    db.commit()


# Only ever sharpens the generic "disruption" bucket into something more
# specific — never overrides IODA's "shutdown" (BGP-withdrawal evidence) or
# OONI's "censorship" (its whole methodology is built around detecting that).
_INTERFERENCE_TO_EVENT_TYPE = {
    "ssl_tamper":  "censorship",   # TLS interception is a classic censorship technique
    "blocked":     "censorship",   # reachable but filtered at the app layer
    "unreachable": "shutdown",     # matches the semantics already used for BGP withdrawal
}


def confirm_events_with_probe(db: Session, probe_results: dict):
    """
    Annotate active events with probe confirmation data, resolve any whose
    country the probe now confirms is back online, and sharpen a generic
    "disruption" classification into "censorship" or "shutdown" when probe
    evidence clearly points that way (see collectors/probe.py's
    _interference_signal — the Trinocular belief itself only judges up vs.
    down; this recategorization is layered on top of it).

    probe_results: {cc: {disrupted, confirmed_up, confidence, note,
    interference_signal}} from ProbeCollector. This function ONLY updates
    existing events — it never creates new ones.

    Region-scoped events (region_name is set) are skipped entirely here.
    Our probe targets are national (e.g. Kremlin.ru, VK, Rostelecom DNS for
    Russia) — a country being reachable overall says nothing about whether
    one specific region within it (Stavropol', say) has a localized
    shutdown. Applying the country-wide verdict to a region event was
    wrongly resolving real, ongoing regional outages the moment the
    national probe read "up", which produced the same flapping-duplicate
    pattern as the earlier stale-belief bug, just via a different path.
    Region events instead rely solely on check_resolutions() — the source
    (IODA) no longer reporting that specific region.
    """
    if not probe_results:
        return
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(hours=24)
    events = (
        db.query(OutageEvent)
        .filter(OutageEvent.is_active.is_(True), OutageEvent.start_time >= cutoff)
        .all()
    )
    now = datetime.datetime.utcnow()
    updated       = 0
    resolved      = 0
    recategorized = 0
    for ev in events:
        if ev.region_name:
            continue
        result = probe_results.get(ev.country_code)
        if result is None:
            continue
        if result.get("confirmed_up"):
            ev.is_active       = False
            ev.resolved        = True
            ev.resolved_at     = now
            # Use updated_at (last time the source confirmed the outage was
            # still active) as a better estimate of when the outage actually
            # ended, rather than the arbitrary time this probe cycle ran.
            ev.end_time        = ev.updated_at or ev.start_time
            ev.probe_confirmed = False
            ev.probe_note      = result.get("note", "")
            resolved += 1
            continue

        confidence = result.get("confidence", 0)
        ev.probe_confirmed = bool(result.get("disrupted") and confidence >= 50)
        ev.probe_note      = result.get("note", "")

        if ev.event_type == "disruption" and confidence >= 50:
            upgrade = _INTERFERENCE_TO_EVENT_TYPE.get(result.get("interference_signal"))
            if upgrade:
                ev.event_type = upgrade
                recategorized += 1

        updated += 1
    db.commit()
    log.info(
        f"[analyzer] Probe confirmation applied to {updated} events, "
        f"{resolved} resolved (confirmed back online), "
        f"{recategorized} recategorized from probe evidence"
    )


def check_resolutions(db: Session, seen_keys: set):
    """
    Auto-resolve active events whose (country, region) is no longer being
    reported.

    Logic:
      - If a (country, region) pair appeared in the current data batch ->
        it stays active. `region` is None for country-wide events.
      - If an event has NOT been refreshed for more than 30 minutes AND its
        exact (country, region) pair is absent from the current batch -> the
        outage likely ended. Mark it resolved with an end_time.

    Keying on the (country, region) pair rather than country alone matters:
    otherwise a single ongoing alert anywhere in a country (e.g. a
    country-wide censorship event) would keep seen_keys non-empty for that
    country and mask a *different*, genuinely-stale region event from ever
    expiring via this path.

    The 30-minute grace prevents a single missed API cycle from falsely
    resolving an ongoing event.
    """
    now     = datetime.datetime.utcnow()
    grace   = now - datetime.timedelta(minutes=30)
    cutoff  = now - datetime.timedelta(hours=24)

    stale_active = (
        db.query(OutageEvent)
        .filter(
            OutageEvent.is_active.is_(True),
            OutageEvent.resolved.is_(False),
            OutageEvent.start_time >= cutoff,
            OutageEvent.updated_at <= grace,       # not refreshed in last 30 min
        )
        .all()
    )

    resolved_count = 0
    for ev in stale_active:
        if (ev.country_code, ev.region_name) not in seen_keys:
            ev.is_active   = False
            ev.resolved    = True
            ev.resolved_at = now
            # Use updated_at (last time the source confirmed the outage was
            # still active) as a better estimate of when the outage actually
            # ended, rather than the arbitrary time this interval cycle ran.
            ev.end_time    = ev.updated_at or ev.start_time
            resolved_count += 1

    if resolved_count:
        db.commit()
        log.info(f"[analyzer] Auto-resolved {resolved_count} events (no longer in source data)")
