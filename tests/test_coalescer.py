"""
Acceptance tests for the coalescing / severity fix.

Covers the four acceptance criteria:
  1. A continuous 3-hour condition sampled every ~30 min yields ONE event
     (ongoing while recent, then one ~3h resolved span) — not ~6 events.
  2. No event shows a duration equal to the sample interval.
  3. A 394-vs-398 (~1%) reading produces no severe/shutdown event.
  4. Mastodon emits at most one open post and one close post per coalesced event.
"""
import asyncio
import datetime as dt

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.coalescer import (CLEAR_HYSTERESIS, GAP_MERGE, classify_severity,
                               duration_label, effective_event_type,
                               plan_events, recompute)
from backend.models import Base, CoalescedEvent, OutageEvent

DAY   = dt.datetime(2026, 7, 22)
START = DAY.replace(hour=14)          # incident begins 14:00 UTC
STEP  = dt.timedelta(minutes=30)      # OONI/IODA-style sample spacing


def _session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _point_samples(n, start=START, step=STEP):
    """n anomalous point-in-time observations, `step` apart."""
    return [(start + step * i, start + step * i) for i in range(n)]


# ── Criterion 1: one sustained event, ongoing then one ~3h span ──────────────

def test_three_hour_condition_is_one_event():
    samples = _point_samples(7)                      # 14:00..17:00 inclusive (3h)
    # 20 min after the last sample -> still within CLEAR_HYSTERESIS -> ongoing.
    events = plan_events(samples, now=START + dt.timedelta(hours=3, minutes=20))
    assert len(events) == 1
    ev = events[0]
    assert ev["is_active"] is True
    assert ev["sample_count"] == 7
    assert ev["observed_start"] == START
    assert ev["observed_end"] == START + dt.timedelta(hours=3)


def test_three_hour_condition_resolves_as_single_span():
    samples = _point_samples(7)
    # 65 min of silence after the last sample -> past CLEAR_HYSTERESIS -> closed.
    events = plan_events(samples, now=START + dt.timedelta(hours=4, minutes=5))
    assert len(events) == 1
    ev = events[0]
    assert ev["is_active"] is False
    span = (ev["observed_end"] - ev["observed_start"]).total_seconds()
    assert abs(span - 3 * 3600) < 1                  # exactly the observed 3h


def test_single_clean_sample_does_not_split():
    # A lone clean cycle (30-min gap) mid-incident must NOT create a boundary.
    samples = _point_samples(3) + _point_samples(3, start=START + dt.timedelta(hours=2))
    events = plan_events(samples, now=START + dt.timedelta(hours=10))
    assert len(events) == 1                          # bridged, not fragmented


# ── Criterion 2: duration never equals the sample interval ───────────────────

def test_duration_never_equals_sample_interval():
    events = plan_events(_point_samples(7), now=START + dt.timedelta(hours=6))
    span = (events[0]["observed_end"] - events[0]["observed_start"]).total_seconds()
    assert span != STEP.total_seconds()              # not 30 min
    assert span == 3 * 3600


def test_isolated_sample_has_zero_span_not_interval():
    events = plan_events(_point_samples(1), now=START + dt.timedelta(hours=6))
    assert len(events) == 1
    span = (events[0]["observed_end"] - events[0]["observed_start"]).total_seconds()
    assert span == 0                                 # a point observation, not 30 min


# ── Criterion 3: ~1% drop is never severe/shutdown ───────────────────────────

def test_one_percent_drop_is_not_severe():
    sev, score, drop = classify_severity(394, 398, source_severity="severe", source_score=90)
    assert sev != "severe"
    assert sev == "normal"                           # below MINOR_PCT
    assert drop < 5
    # Numbers preserved so it can read as a minor/normal dip, not severe.
    assert drop is not None


def test_real_shutdown_still_severe():
    sev, score, drop = classify_severity(20, 400, source_severity="minor", source_score=25)
    assert sev == "severe"
    assert drop >= 50


def test_no_baseline_keeps_source_severity():
    # OONI has no baseline; its own (percentage-based) severity is preserved.
    sev, score, drop = classify_severity(None, None, source_severity="severe", source_score=88)
    assert sev == "severe"
    assert drop is None


# ── Event type is downgraded by drop magnitude, like severity ────────────────

def test_small_dip_is_not_a_shutdown():
    # The Gaza Strip case: IODA labels any bgp alert "shutdown", but a
    # 142->134 (5.6%) dip is not a shutdown.
    assert effective_event_type("shutdown", 5.6) == "disruption"


def test_real_collapse_keeps_shutdown_type():
    assert effective_event_type("shutdown", 95.0) == "shutdown"


def test_no_drop_data_keeps_source_type():
    # No baseline to judge by (probe/Cloudflare evidence) -> type preserved.
    assert effective_event_type("shutdown", None) == "shutdown"


def test_non_shutdown_types_pass_through():
    assert effective_event_type("censorship", 1.0) == "censorship"
    assert effective_event_type("disruption", 1.0) == "disruption"


# ── DB-level: recompute is idempotent and collapses fragments ────────────────

def _seed_fragments(session, n, actual=100, baseline=400, source="ioda",
                    etype="disruption", start=START, step=STEP,
                    probe_confirmed=False, source_confirmed=False):
    """Seed n raw OutageEvent fragments (as if resolved+recreated each cycle)."""
    for i in range(n):
        t = start + step * i
        session.add(OutageEvent(
            country_code="XX", country_name="Xland", region_name=None,
            title="Internet disruption detected in Xland", description="raw",
            event_type=etype, severity="severe", severity_score=90,
            source=source, source_url="https://example/x",
            actual_value=actual, baseline_value=baseline,
            start_time=t, end_time=t, updated_at=t,
            is_active=(i == n - 1), resolved=(i < n - 1),
            probe_confirmed=probe_confirmed, source_confirmed=source_confirmed,
        ))
    session.commit()


def test_recompute_collapses_fragments_and_is_idempotent():
    session = _session()
    _seed_fragments(session, 7)                      # 6 gaps of 30 min -> one event
    now = START + dt.timedelta(hours=3, minutes=20)
    recompute(session, now=now)
    rows = session.query(CoalescedEvent).all()
    assert len(rows) == 1
    ce = rows[0]
    assert ce.is_active is True
    assert ce.severity == "severe"                   # 75% drop
    assert ce.sample_count == 7
    first_id = ce.id

    # Idempotent: re-run at same time -> still exactly one, same id.
    recompute(session, now=now)
    rows = session.query(CoalescedEvent).all()
    assert len(rows) == 1 and rows[0].id == first_id

    # Past hysteresis -> closes as a single ~3h observed span.
    recompute(session, now=START + dt.timedelta(hours=4, minutes=5))
    ce = session.query(CoalescedEvent).one()
    assert ce.is_active is False and ce.resolved is True
    span = (ce.observed_end - ce.observed_start).total_seconds()
    assert abs(span - 3 * 3600) < 1
    assert "observed" in duration_label(ce)


def test_tiny_dip_recompute_makes_no_severe_event():
    session = _session()
    _seed_fragments(session, 7, actual=394, baseline=398)   # ~1% dip
    recompute(session, now=START + dt.timedelta(hours=3, minutes=20))
    ce = session.query(CoalescedEvent).one()
    assert ce.severity in ("normal", "minor")
    assert ce.severity != "severe"


def test_small_dip_shutdown_recomputes_as_disruption():
    # Raw rows labeled "shutdown" (IODA bgp datasource) with a 5.6% drop must
    # coalesce into a "disruption" event, not a "shutdown".
    session = _session()
    _seed_fragments(session, 3, actual=134, baseline=142, etype="shutdown")
    recompute(session, now=START + dt.timedelta(hours=1, minutes=20))
    ce = session.query(CoalescedEvent).one()
    assert ce.event_type == "disruption"
    assert ce.severity == "minor"


def test_collapse_shutdown_recomputes_as_shutdown():
    session = _session()
    _seed_fragments(session, 3, actual=20, baseline=400, etype="shutdown")
    recompute(session, now=START + dt.timedelta(hours=1, minutes=20))
    ce = session.query(CoalescedEvent).one()
    assert ce.event_type == "shutdown"
    assert ce.severity == "severe"


def test_existing_shutdown_row_is_retyped_not_duplicated():
    # A coalesced "shutdown" row written before the type-downgrade fix must be
    # re-matched and re-typed in place — not orphaned next to a duplicate.
    session = _session()
    _seed_fragments(session, 3, actual=134, baseline=142, etype="shutdown")
    session.add(CoalescedEvent(
        country_code="XX", region_name=None, event_type="shutdown",
        country_name="Xland", severity="minor", severity_score=10.1,
        drop_pct=5.6, actual_value=134, baseline_value=142,
        sources="ioda", source="ioda", source_url="https://example/x",
        title="Internet disruption detected in Xland", description="d",
        observed_start=START, observed_end=START + dt.timedelta(hours=1),
        sample_count=3, is_active=True, resolved=False,
    ))
    session.commit()
    recompute(session, now=START + dt.timedelta(hours=1, minutes=20))
    rows = session.query(CoalescedEvent).all()
    assert len(rows) == 1
    assert rows[0].event_type == "disruption"


# ── Two-tier confidence: confirmation is derived and reported honestly ──────

def test_severe_collapse_is_magnitude_confirmed():
    session = _session()
    _seed_fragments(session, 3, actual=20, baseline=400)     # 95% collapse
    recompute(session, now=START + dt.timedelta(hours=1, minutes=20))
    ce = session.query(CoalescedEvent).one()
    assert ce.confirmation == "magnitude"
    assert "self-evident" in ce.description


def test_small_dip_is_unconfirmed_and_says_so():
    session = _session()
    _seed_fragments(session, 3, actual=134, baseline=142)    # 5.6% dip
    # Active: awaiting corroboration.
    recompute(session, now=START + dt.timedelta(hours=1, minutes=20))
    ce = session.query(CoalescedEvent).one()
    assert ce.confirmation == "unconfirmed"
    assert "awaiting independent corroboration" in ce.description
    # Resolved without corroboration: flagged as possible false positive,
    # never presented as a verified outage that ended.
    recompute(session, now=START + dt.timedelta(hours=3))
    ce = session.query(CoalescedEvent).one()
    assert ce.resolved is True
    assert ce.confirmation == "unconfirmed"
    assert "possible false positive" in ce.description


def test_source_crosscheck_beats_other_tiers():
    session = _session()
    _seed_fragments(session, 3, actual=20, baseline=400,
                    probe_confirmed=True, source_confirmed=True)
    recompute(session, now=START + dt.timedelta(hours=1, minutes=20))
    ce = session.query(CoalescedEvent).one()
    assert ce.confirmation == "source"                       # strongest tier wins
    assert "verified outage feed" in ce.description


def test_probe_confirmation_tier():
    session = _session()
    _seed_fragments(session, 3, actual=134, baseline=142, probe_confirmed=True)
    recompute(session, now=START + dt.timedelta(hours=1, minutes=20))
    ce = session.query(CoalescedEvent).one()
    assert ce.confirmation == "probe"


# ── Verifier: curated-feed parsing and conservative matching ────────────────

def test_parse_curated_country_and_region():
    from backend.verifier import parse_curated
    entries = [
        {"location": "country/PS", "start": 1753160100, "duration": 1800,
         "location_name": "Palestinian Territories"},
        {"location": "region/1226", "start": 1753160100, "duration": 1800,
         "location_name": "Gaza Strip"},
    ]
    country = parse_curated(entries)
    assert country == [("PS", None,
                        dt.datetime.utcfromtimestamp(1753160100),
                        dt.datetime.utcfromtimestamp(1753161900))]
    # Region entries are only usable when the per-country query context is
    # passed in (CODF region entries don't carry their country).
    region = parse_curated(entries, country_code="PS")
    assert ("PS", "Gaza Strip",
            dt.datetime.utcfromtimestamp(1753160100),
            dt.datetime.utcfromtimestamp(1753161900)) in region


def test_match_confirmations_requires_key_and_overlap():
    from backend.verifier import match_confirmations

    def _row(cc="PS", region="Gaza Strip", start=START, end=None):
        return OutageEvent(country_code=cc, region_name=region,
                           start_time=start, end_time=end or start,
                           updated_at=end or start)

    curated = [("PS", "Gaza Strip", START, START + dt.timedelta(minutes=30))]
    slack   = dt.timedelta(hours=1)

    overlapping = _row()
    wrong_region = _row(region="West Bank")
    wrong_cc     = _row(cc="MZ")                      # "Gaza" (Mozambique) etc.
    too_late     = _row(start=START + dt.timedelta(hours=3),
                        end=START + dt.timedelta(hours=4))
    near_edge    = _row(start=START + dt.timedelta(minutes=80))   # inside slack

    rows = [overlapping, wrong_region, wrong_cc, too_late, near_edge]
    matched = match_confirmations(rows, curated, slack=slack)
    assert overlapping in matched
    assert near_edge in matched
    assert wrong_region not in matched
    assert wrong_cc not in matched
    assert too_late not in matched


# ── Criterion 4: one open post + one close post per coalesced event ──────────

def test_mastodon_posts_open_and_close_at_most_once(monkeypatch):
    import backend.alerts as alerts

    session = _session()
    ce = CoalescedEvent(
        country_code="XX", country_name="Xland", event_type="disruption",
        severity="severe", severity_score=90, sources="ioda", source="ioda",
        source_url="https://example/x", title="Internet disruption detected in Xland",
        description="d", actual_value=100, baseline_value=400, drop_pct=75.0,
        confirmation="magnitude",
        observed_start=START, observed_end=START + dt.timedelta(hours=3),
        sample_count=7, is_active=True, resolved=False,
    )
    session.add(ce)
    session.commit()

    opens = {"n": 0}
    closes = {"n": 0}

    async def fake_send(ev, resolved=False):
        (closes if resolved else opens)["n"] += 1
        return True

    monkeypatch.setattr(alerts, "_channels", lambda: [("mastodon", fake_send)])
    monkeypatch.setattr(alerts, "_POST_INTERVAL_SEC", 0)

    # Open: called twice, must still post once.
    asyncio.run(alerts.check_and_send_alerts(session, [ce.id]))
    asyncio.run(alerts.check_and_send_alerts(session, [ce.id]))
    assert opens["n"] == 1

    # Close: gated on the open having been announced; called twice, posts once.
    ce.is_active = False
    ce.resolved = True
    ce.resolved_at = ce.observed_end
    session.commit()
    asyncio.run(alerts.check_and_send_resolved_alerts(session, [ce.id]))
    asyncio.run(alerts.check_and_send_resolved_alerts(session, [ce.id]))
    assert closes["n"] == 1


def test_unconfirmed_event_is_never_posted(monkeypatch):
    # An event nothing corroborates must not be announced publicly — raw
    # source alerts get retracted, and a post can't be walked back.
    import backend.alerts as alerts

    session = _session()
    ce = CoalescedEvent(
        country_code="XX", country_name="Xland", event_type="disruption",
        severity="significant", severity_score=55, sources="ioda", source="ioda",
        source_url="https://example/x", title="t", description="d",
        actual_value=280, baseline_value=400, drop_pct=30.0,
        confirmation="unconfirmed",
        observed_start=START, observed_end=START + dt.timedelta(hours=1),
        sample_count=3, is_active=True, resolved=False,
    )
    session.add(ce)
    session.commit()

    sent = {"n": 0}

    async def fake_send(ev, resolved=False):
        sent["n"] += 1
        return True

    monkeypatch.setattr(alerts, "_channels", lambda: [("mastodon", fake_send)])
    monkeypatch.setattr(alerts, "_POST_INTERVAL_SEC", 0)

    asyncio.run(alerts.check_and_send_alerts(session, [ce.id]))
    assert sent["n"] == 0

    # Once corroborated (e.g. the curated feed publishes it), it posts.
    ce.confirmation = "source"
    session.commit()
    asyncio.run(alerts.check_and_send_alerts(session, [ce.id]))
    assert sent["n"] == 1


def test_config_defaults_match_spec():
    assert GAP_MERGE == dt.timedelta(minutes=90)
    assert CLEAR_HYSTERESIS == dt.timedelta(minutes=60)
