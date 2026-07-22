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
                               duration_label, plan_events, recompute)
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


# ── DB-level: recompute is idempotent and collapses fragments ────────────────

def _seed_fragments(session, n, actual=100, baseline=400, source="ioda",
                    etype="disruption", start=START, step=STEP):
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


# ── Criterion 4: one open post + one close post per coalesced event ──────────

def test_mastodon_posts_open_and_close_at_most_once(monkeypatch):
    import backend.alerts as alerts

    session = _session()
    ce = CoalescedEvent(
        country_code="XX", country_name="Xland", event_type="disruption",
        severity="severe", severity_score=90, sources="ioda", source="ioda",
        source_url="https://example/x", title="Internet disruption detected in Xland",
        description="d", actual_value=100, baseline_value=400, drop_pct=75.0,
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


def test_config_defaults_match_spec():
    assert GAP_MERGE == dt.timedelta(minutes=90)
    assert CLEAR_HYSTERESIS == dt.timedelta(minutes=60)
