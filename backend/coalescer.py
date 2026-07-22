"""
Event coalescing — the derived event layer.

The collectors (ingestion) write one raw OutageEvent per source measurement,
per cycle. Because OONI/IODA/Cloudflare measurements are discrete and spaced, a
single continuous outage or censorship condition is recorded as many short,
back-to-back raw rows (e.g. a 25-min row, a 5-min gap, another row). Reporting
those raw rows directly both inflates event counts and makes "duration" nothing
more than the gap between samples.

This module derives a CoalescedEvent layer on top of the untouched raw layer:

  * Raw observations for one (country, region, type) are merged into a single
    sustained event while the anomaly persists. Two observations closer
    together than GAP_MERGE are the same event; a single clean sample (one
    missed cycle) is far shorter than GAP_MERGE, so it never splits an event.
  * An event is only CLOSED once the condition has stayed normal continuously
    for at least CLEAR_HYSTERESIS. Until then it is "ongoing since <start>".
  * When an event closes, its span is the OBSERVED window (first anomalous
    sample -> last anomalous confirmation) — never the raw sample spacing.
  * Severity is (re)classified by the magnitude of the drop vs baseline, so a
    ~1% dip (394 vs 398) can never read as a shutdown/severe event.

recompute() is idempotent: it can run every cycle (steady state) or over all
history (backfill) and converges to the same coalesced set, matching existing
rows by their stable observed_start so ids — and therefore Mastodon post-once
dedup — survive across runs.
"""
import datetime
import logging
from collections import defaultdict

from sqlalchemy.orm import Session

from .config import config
from .models import CoalescedAlert, CoalescedEvent, OutageEvent

log = logging.getLogger(__name__)

# ── Tunables (see config.py for the commented env overrides) ──────────────────
GAP_MERGE        = datetime.timedelta(minutes=config.COALESCE_GAP_MERGE_MINUTES)
CLEAR_HYSTERESIS = datetime.timedelta(minutes=config.COALESCE_CLEAR_HYSTERESIS_MINUTES)

SEVERE_PCT      = config.SEVERITY_SEVERE_PCT
SIGNIFICANT_PCT = config.SEVERITY_SIGNIFICANT_PCT
MINOR_PCT       = config.SEVERITY_MINOR_PCT

_SEV_RANK           = {"normal": 0, "minor": 1, "significant": 2, "severe": 3}
_SEV_SCORE_FALLBACK = {"severe": 90.0, "significant": 55.0, "minor": 25.0, "normal": 0.0}

# How long after a coalesced event closes we still consider it "recently
# resolved" for the purpose of emitting one close post. Dedup (CoalescedAlert)
# guarantees post-once regardless; this just bounds the per-cycle scan.
_RESOLVE_POST_WINDOW = datetime.timedelta(hours=6)


# ── Severity ──────────────────────────────────────────────────────────────────

def classify_severity(actual, baseline, source_severity, source_score):
    """
    Classify severity by how far ``actual`` has dropped below ``baseline``.

    Returns ``(severity, score, drop_pct)``.

    When a baseline is present (IODA-style signal-vs-baseline), the drop percent
    is authoritative: a drop below MINOR_PCT is normal variance -> "normal"
    (never a shutdown/severe event), and the reachable-vs-baseline numbers are
    preserved so a ~1% dip reads as minor/normal, not severe. When no baseline
    is available (OONI anomaly rates, Cloudflare shutdown annotations), fall
    back to the source's own severity — the drop model only ever *downgrades*
    an over-eager source label; it never invents a baseline it doesn't have.
    """
    if actual is not None and baseline is not None and baseline > 0:
        drop_pct = max(0.0, (baseline - actual) / baseline * 100.0)
        if drop_pct >= SEVERE_PCT:
            sev = "severe"
        elif drop_pct >= SIGNIFICANT_PCT:
            sev = "significant"
        elif drop_pct >= MINOR_PCT:
            sev = "minor"
        else:
            sev = "normal"                      # < MINOR_PCT: normal variance
        score = round(min(100.0, drop_pct * 1.8), 1)
        return sev, score, round(drop_pct, 1)

    sev = source_severity if source_severity in _SEV_RANK else "minor"
    score = source_score if source_score is not None else _SEV_SCORE_FALLBACK.get(sev, 25.0)
    return sev, float(score), None


def _drop_pct(actual, baseline):
    if actual is None or baseline is None or baseline <= 0:
        return None
    return max(0.0, (baseline - actual) / baseline * 100.0)


def effective_event_type(event_type, drop_pct):
    """
    "shutdown" claims (near-)total loss of connectivity, but sources assign it
    by datasource, not magnitude — IODA labels ANY bgp-datasource alert a
    shutdown, even a 142->134 (~6%) wobble it later retracts (the Gaza Strip
    false-"shutdown" case, confirmed live). When we can measure the drop and it
    is below the severe threshold, downgrade to the generic "disruption" so the
    label matches the evidence. Like classify_severity, this only ever
    downgrades: it never upgrades a type, and never second-guesses one when no
    baseline exists to judge it by (probe/Cloudflare evidence has no signal
    numbers but genuinely means "unreachable").
    """
    if event_type == "shutdown" and drop_pct is not None and drop_pct < SEVERE_PCT:
        return "disruption"
    return event_type


def _confirmation(source_confirmed, probe_confirmed, n_sources, drop_pct):
    """
    Two-tier confidence: what independently corroborates this event beyond the
    raw source alert that created it? Raw feeds (IODA /outages/alerts) are
    noisy and get RETRACTED after reprocessing, so a raw alert alone is a
    lead, not a verified outage. Ranked strongest-first:

      source       -> the source's own curated/verified feed published a
                      matching outage (persistent + externally checkable)
      probe        -> our active probing independently confirmed it
      multi-source -> >=2 independent sources observed the same condition
      magnitude    -> the drop itself is self-evident (>= severe threshold);
                      a >=50% signal collapse isn't measurement noise
      unconfirmed  -> raw signal only; may later prove a false positive
    """
    if source_confirmed:
        return "source"
    if probe_confirmed:
        return "probe"
    if n_sources > 1:
        return "multi-source"
    if drop_pct is not None and drop_pct >= SEVERE_PCT:
        return "magnitude"
    return "unconfirmed"


_CONFIRMATION_TEXT = {
    "source":       "Corroborated by the source's verified outage feed.",
    "probe":        "Independently confirmed by active probing.",
    "multi-source": "Corroborated by multiple independent sources.",
    "magnitude":    "Signal collapse is self-evident (>= {pct:g}% drop).",
}


# ── Interval merging (pure, deterministic — the heart of coalescing) ──────────

class _Sample:
    __slots__ = ("start", "last", "row")

    def __init__(self, start, last, row):
        self.start = start
        self.last  = last
        self.row   = row


def _merge_intervals(samples, gap_merge=GAP_MERGE):
    """
    Merge time-ordered raw observation intervals into sustained groups.

    Two observations belong to the same event iff the gap between the end of one
    and the start of the next is shorter than ``gap_merge``. Returns a list of
    groups, each a list of _Sample in time order. Pure and deterministic.
    """
    if not samples:
        return []
    ordered = sorted(samples, key=lambda s: s.start)
    groups  = [[ordered[0]]]
    cur_end = ordered[0].last
    for s in ordered[1:]:
        if s.start - cur_end < gap_merge:       # bridge short recovery gaps
            groups[-1].append(s)
            if s.last > cur_end:
                cur_end = s.last
        else:                                   # sustained recovery -> new event
            groups.append([s])
            cur_end = s.last
    return groups


def plan_events(intervals, now, gap_merge=GAP_MERGE, clear_hysteresis=CLEAR_HYSTERESIS):
    """
    Pure coalescing for ONE (country, region, type) key: given a list of raw
    ``(start, end)`` observation intervals and the current time, return the
    sustained events as dicts (observed_start, observed_end, is_active,
    sample_count). This is the exact logic recompute() uses, exposed for direct
    unit testing against the acceptance criteria.
    """
    samples = [_Sample(s, e, None) for (s, e) in intervals]
    out = []
    for group in _merge_intervals(samples, gap_merge):
        observed_start = group[0].start
        observed_end   = max(s.last for s in group)
        out.append({
            "observed_start": observed_start,
            "observed_end":   observed_end,
            # Open until the condition has been normal for >= CLEAR_HYSTERESIS.
            "is_active":      (now - observed_end) < clear_hysteresis,
            "sample_count":   len(group),
        })
    return out


# ── Duration / label helpers (honest window semantics, shared with outputs) ───

def span_seconds(start, end):
    if not (start and end):
        return None
    return max(0, int((end - start).total_seconds()))


def span_label(start, end):
    """Human span of an OBSERVED window (never raw sample spacing)."""
    secs = span_seconds(start, end)
    if secs is None:
        return ""
    if secs < 60:
        return "under a minute"
    mins = secs // 60
    days, rem      = divmod(mins, 1440)
    hours, minutes = divmod(rem, 60)
    parts = []
    if days:                 parts.append(f"{days}d")
    if hours:                parts.append(f"{hours}h")
    if minutes and not days: parts.append(f"{minutes}m")
    return " ".join(parts) or "under a minute"


def duration_label(ev):
    """
    Honest label for a CoalescedEvent, used by the feed and social posts.
      ongoing  -> "ongoing since 2026-07-22 14:05 UTC"
      resolved -> "observed 2026-07-22 14:05–17:10 UTC (3h 5m)"
    """
    start = ev.observed_start
    if ev.is_active or not ev.observed_end:
        return f"ongoing since {start.strftime('%Y-%m-%d %H:%M UTC')}" if start else "ongoing"
    end      = ev.observed_end
    same_day = start and start.date() == end.date()
    start_s  = start.strftime("%Y-%m-%d %H:%M") if start else "?"
    end_s    = end.strftime("%H:%M") if same_day else end.strftime("%Y-%m-%d %H:%M")
    return f"observed {start_s}–{end_s} UTC ({span_label(start, end)})"


# ── Group -> event field summarisation ────────────────────────────────────────

def _summarize_group(group):
    """
    Collapse a merged group of raw observations into one event's fields, picking
    the worst (highest re-classified severity, then largest drop) member as
    representative and preserving its reachable-vs-baseline numbers.
    """
    best             = None
    sources          = set()
    probe_confirmed  = False
    source_confirmed = False
    country_name     = None
    for s in group:
        r = s.row
        if r.source:
            sources.add(r.source)
        probe_confirmed  = probe_confirmed or bool(getattr(r, "probe_confirmed", False))
        source_confirmed = source_confirmed or bool(getattr(r, "source_confirmed", False))
        country_name     = country_name or r.country_name
        sev, score, drop = classify_severity(
            r.actual_value, r.baseline_value, r.severity, r.severity_score)
        rank = _SEV_RANK.get(sev, 1)
        cmp  = (rank, score, drop if drop is not None else -1.0)
        if best is None or cmp > best[0]:
            best = (cmp, sev, score, drop, r)
    _, sev, score, drop, r = best
    return {
        "severity": sev, "severity_score": score, "drop_pct": drop,
        "actual_value": r.actual_value, "baseline_value": r.baseline_value,
        "source": r.source, "source_url": r.source_url, "title": r.title,
        "sources": ",".join(sorted(sources)),
        "probe_confirmed": probe_confirmed,
        "confirmation": _confirmation(source_confirmed, probe_confirmed,
                                       len(sources), drop),
        "country_name": country_name,
    }


def _describe(fields, observed_start, observed_end, is_active, sample_count):
    label = duration_label(_LabelView(observed_start, observed_end, is_active))
    sig = ""
    a, b = fields.get("actual_value"), fields.get("baseline_value")
    if a is not None and b is not None:
        sig = f" Signal {a:g} vs baseline {b:g}"
        if fields.get("drop_pct") is not None:
            sig += f" ({fields['drop_pct']:g}% drop)"
        sig += "."
    src = "/".join(s.upper() for s in fields["sources"].split(",") if s)
    n   = sample_count
    conf = fields.get("confirmation", "unconfirmed")
    if conf in _CONFIRMATION_TEXT:
        conf_text = " " + _CONFIRMATION_TEXT[conf].format(pct=SEVERE_PCT)
    elif is_active:
        conf_text = " Unconfirmed — raw signal only, awaiting independent corroboration."
    else:
        conf_text = (" Unconfirmed — no independent corroboration was observed; "
                     "possible false positive.")
    return (f"{src}: {label}. Coalesced from {n} "
            f"observation{'s' if n != 1 else ''}.{sig}{conf_text}")


class _LabelView:
    """Minimal duck-typed view so duration_label() can format an in-flight group."""
    __slots__ = ("observed_start", "observed_end", "is_active")

    def __init__(self, observed_start, observed_end, is_active):
        self.observed_start = observed_start
        self.observed_end   = observed_end
        self.is_active      = is_active


def _title(key, fields):
    return fields.get("title") or f"{key[2].title()} in {fields.get('country_name') or key[0]}"


# ── recompute: derive the coalesced layer from the raw layer ──────────────────

def _match(pool, used, observed_start):
    """Best existing coalesced row for a group, matched by nearest stable start."""
    best = None
    for ce in pool:
        if id(ce) in used or ce.observed_start is None:
            continue
        delta = abs((ce.observed_start - observed_start).total_seconds())
        if delta <= GAP_MERGE.total_seconds() and (best is None or delta < best[0]):
            best = (delta, ce)
    if best:
        used.add(id(best[1]))
        return best[1]
    return None


def _apply(ce, key, fields, observed_start, observed_end, is_active, sample_count, now):
    ce.country_code   = key[0]
    ce.region_name    = key[1]
    ce.event_type     = key[2]
    ce.country_name   = fields["country_name"] or ce.country_name or key[0]
    ce.severity       = fields["severity"]
    ce.severity_score = fields["severity_score"]
    ce.drop_pct       = fields["drop_pct"]
    ce.actual_value   = fields["actual_value"]
    ce.baseline_value = fields["baseline_value"]
    ce.sources        = fields["sources"]
    ce.source         = fields["source"]
    ce.source_url     = fields["source_url"]
    ce.probe_confirmed = fields["probe_confirmed"]
    ce.confirmation   = fields["confirmation"]
    ce.title          = _title(key, fields)
    ce.observed_start = observed_start
    ce.observed_end   = observed_end
    ce.sample_count   = sample_count
    ce.is_active      = is_active
    ce.resolved       = not is_active
    ce.resolved_at    = None if is_active else observed_end
    ce.description    = _describe(fields, observed_start, observed_end, is_active, sample_count)
    ce.updated_at     = now


def _close(ce, now):
    ce.is_active   = False
    ce.resolved    = True
    ce.resolved_at = ce.observed_end or ce.resolved_at or now
    ce.updated_at  = now


def recompute(db: Session, now=None, window_days=None):
    """
    Re-derive the CoalescedEvent layer from raw OutageEvent observations.

    Idempotent and safe to run every cycle: existing coalesced rows are matched
    by their stable observed_start and updated in place, so ids — and therefore
    Mastodon post-once dedup — survive across runs. Returns
    ``(active_ids, resolved_ids)`` of alert-worthy (significant/severe) events;
    the caller hands these to the alert layer, whose CoalescedAlert dedup makes
    the actual posting at-most-once.
    """
    now         = now or datetime.datetime.utcnow()
    window_days = window_days or config.COALESCE_WINDOW_DAYS
    since       = now - datetime.timedelta(days=window_days)

    raw = db.query(OutageEvent).filter(OutageEvent.start_time >= since).all()

    by_key = defaultdict(list)
    for r in raw:
        if not r.country_code:
            continue
        start = r.start_time or r.created_at
        if start is None:
            continue
        last = r.end_time or r.resolved_at or r.updated_at or start
        if last < start:
            last = start
        etype = effective_event_type(r.event_type, _drop_pct(r.actual_value, r.baseline_value))
        by_key[(r.country_code, r.region_name, etype)].append(_Sample(start, last, r))

    existing_by_key = defaultdict(list)
    for ce in db.query(CoalescedEvent).filter(CoalescedEvent.observed_start >= since).all():
        # Normalize the stored type the same way the raw keys are normalized,
        # so a coalesced "shutdown" row written before the type downgrade
        # existed re-matches its (now "disruption") group and is re-typed in
        # place, instead of being orphaned while a duplicate row is created.
        etype = effective_event_type(ce.event_type, ce.drop_pct)
        existing_by_key[(ce.country_code, ce.region_name, etype)].append(ce)

    touched = set()
    for key, samples in by_key.items():
        pool = list(existing_by_key.get(key, []))
        used = set()
        for group in _merge_intervals(samples):
            observed_start = group[0].start
            observed_end   = max(s.last for s in group)
            is_active      = (now - observed_end) < CLEAR_HYSTERESIS
            fields         = _summarize_group(group)

            ce = _match(pool, used, observed_start)
            if ce is None:
                ce = CoalescedEvent(country_code=key[0], region_name=key[1], event_type=key[2])
                db.add(ce)
            _apply(ce, key, fields, observed_start, observed_end, is_active, len(group), now)
            db.flush()
            touched.add(ce.id)

        # Active coalesced events whose raw rows aged out of the window: close.
        for ce in pool:
            if id(ce) not in used and ce.is_active:
                _close(ce, now)
                db.flush()
                touched.add(ce.id)

    db.commit()

    if not touched:
        return [], []
    resolve_since = now - _RESOLVE_POST_WINDOW
    active_ids, resolved_ids = [], []
    for ce in db.query(CoalescedEvent).filter(CoalescedEvent.id.in_(touched)).all():
        if ce.severity not in ("significant", "severe"):
            continue
        if ce.is_active:
            active_ids.append(ce.id)
        elif ce.resolved and ce.resolved_at and ce.resolved_at >= resolve_since:
            resolved_ids.append(ce.id)
    return active_ids, resolved_ids


# ── Public status derived from the coalesced layer ────────────────────────────

def country_status(db: Session, now=None, window_hours=24) -> dict:
    """Per-country worst active coalesced event — powers the map/country list."""
    now = now or datetime.datetime.utcnow()
    cut = now - datetime.timedelta(hours=window_hours)
    rows = (db.query(CoalescedEvent)
              .filter(CoalescedEvent.is_active.is_(True),
                      CoalescedEvent.observed_start >= cut,
                      CoalescedEvent.severity != "normal")   # normal-variance isn't an event
              .all())
    status: dict = {}
    for ce in rows:
        cc = ce.country_code
        if cc not in status or ce.severity_score > status[cc]["score"]:
            ts = ce.updated_at or ce.observed_start
            status[cc] = {
                "code": cc, "name": ce.country_name, "status": ce.severity,
                "score": ce.severity_score, "active_events": 0,
                "last_updated": (ts.isoformat() + "Z") if ts else None,
            }
        status[cc]["active_events"] += 1
    return status


# ── Backfill: one-time (idempotent) re-coalescing of all history ──────────────

def _suppress_existing_alerts(db: Session, now, older_than_hours):
    """
    Mark pre-existing coalesced incidents as already-announced (synthetic
    CoalescedAlert rows) so the first live cycle after enabling this layer does
    not retro-blast Mastodon/Discord with historical events. Only events whose
    window began more than ``older_than_hours`` ago are suppressed; a genuinely
    new incident near ``now`` is left to post normally.
    """
    cutoff   = now - datetime.timedelta(hours=older_than_hours)
    channels = ["discord", "mastodon", "discord-resolved", "mastodon-resolved"]
    n = 0
    for ce in db.query(CoalescedEvent).filter(CoalescedEvent.observed_start < cutoff).all():
        have = {a.channel for a in
                db.query(CoalescedAlert).filter(CoalescedAlert.event_id == ce.id).all()}
        for ch in channels:
            if ch not in have:
                db.add(CoalescedAlert(event_id=ce.id, channel=ch,
                                      message="(backfill: pre-existing, suppressed)"))
                n += 1
    db.commit()
    return n


def backfill(db: Session, now=None, suppress_alerts_older_than_hours=2):
    """
    Re-coalesce ALL historical raw observations into the CoalescedEvent layer,
    retroactively correcting inflated 30-day counts and dishonest durations.
    Idempotent. Suppresses alerts for pre-existing incidents (see
    _suppress_existing_alerts) so enabling the layer doesn't spam social posts.
    """
    now = now or datetime.datetime.utcnow()
    recompute(db, now=now)
    suppressed = _suppress_existing_alerts(db, now, suppress_alerts_older_than_hours)
    total = db.query(CoalescedEvent).count()
    log.info(f"[coalescer] Backfill complete: {total} coalesced events derived, "
             f"{suppressed} pre-existing alert(s) marked already-announced")
    return {"coalesced_events": total, "alerts_suppressed": suppressed}
