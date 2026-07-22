import datetime

from sqlalchemy import (Boolean, Column, DateTime, Float, ForeignKey,
                        Integer, String, Text)
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class OutageEvent(Base):
    __tablename__ = "outage_events"

    id             = Column(Integer, primary_key=True, index=True)
    country_code   = Column(String(3),   index=True)
    country_name   = Column(String(100))
    region_name    = Column(String(100), nullable=True)  # set for sub-national alerts (e.g. IODA entityType=region)
    title          = Column(String(200))
    description    = Column(Text)
    event_type     = Column(String(50))    # shutdown | censorship | disruption
    severity       = Column(String(20))    # normal | minor | significant | severe
    severity_score = Column(Float,   default=0)
    source         = Column(String(50))    # ioda | ooni | cloudflare | manual
    source_url     = Column(String(500))
    actual_value   = Column(Float, nullable=True)   # raw signal value at alert time (source-specific units)
    baseline_value = Column(Float, nullable=True)   # expected/baseline value for comparison
    start_time     = Column(DateTime)
    end_time       = Column(DateTime, nullable=True)
    is_active      = Column(Boolean, default=True)
    created_at     = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at     = Column(DateTime, default=datetime.datetime.utcnow,
                            onupdate=datetime.datetime.utcnow)

    # Probe confirmation
    probe_confirmed = Column(Boolean, default=False)
    probe_note      = Column(Text,    nullable=True)

    # Source cross-check: True once IODA's curated /outages/events feed
    # (post-processed, persistent — unlike the raw alerts feed, which IODA
    # retracts after reprocessing) publishes an outage overlapping this
    # observation. See backend/verifier.py.
    source_confirmed = Column(Boolean, default=False)

    # Resolution tracking
    resolved    = Column(Boolean,  default=False)   # True once the outage ends
    resolved_at = Column(DateTime, nullable=True)   # when it was marked resolved


class AlertSent(Base):
    __tablename__ = "alerts_sent"

    id       = Column(Integer, primary_key=True)
    event_id = Column(Integer, ForeignKey("outage_events.id"))
    channel  = Column(String(100))
    sent_at  = Column(DateTime, default=datetime.datetime.utcnow)
    message  = Column(Text)


class CoalescedEvent(Base):
    """
    Derived, user-facing event: ONE row per sustained incident, coalesced from
    the raw per-cycle OutageEvent observations by backend/coalescer.py.

    OutageEvent is the RAW layer — every source measurement pass/fail, spaced
    and discrete, which fragments one continuous outage into many short
    back-to-back rows whose "duration" is really just the gap between samples.
    This table is the DERIVED layer the feed, counts and Mastodon read from:
    consecutive/near raw observations for one (country, region, type) are merged
    into a single event with an honest OBSERVED window, and severity is
    re-classified by drop-vs-baseline rather than a source's own label. The raw
    layer is never mutated by coalescing.
    """
    __tablename__ = "coalesced_events"

    id             = Column(Integer, primary_key=True, index=True)
    country_code   = Column(String(3),   index=True)
    country_name   = Column(String(100))
    region_name    = Column(String(100), nullable=True)
    event_type     = Column(String(50))    # shutdown | censorship | disruption
    severity       = Column(String(20))    # normal | minor | significant | severe
    severity_score = Column(Float, default=0)
    sources        = Column(String(200))   # comma-joined contributing sources
    source         = Column(String(50))    # primary (worst) contributing source
    source_url     = Column(String(500))
    title          = Column(String(200))
    description    = Column(Text)
    actual_value   = Column(Float, nullable=True)   # representative worst-drop signal
    baseline_value = Column(Float, nullable=True)   # its baseline, preserved for display
    drop_pct       = Column(Float, nullable=True)   # representative % drop vs baseline

    # Honest OBSERVED window: first anomalous sample -> last anomalous
    # confirmation before sustained recovery. NEVER derived from sample spacing.
    observed_start = Column(DateTime, index=True)
    observed_end   = Column(DateTime)
    sample_count   = Column(Integer, default=1)     # raw observations merged in

    is_active       = Column(Boolean, default=True)  # ongoing (not yet cleared)
    resolved        = Column(Boolean, default=False)
    resolved_at     = Column(DateTime, nullable=True)
    probe_confirmed = Column(Boolean, default=False)

    # Two-tier confidence: what (if anything) independently corroborates this
    # event beyond the raw source alert that created it.
    #   source       -> IODA's curated outage feed published a matching outage
    #   probe        -> our active probing independently confirmed it
    #   multi-source -> ≥2 independent sources contributed observations
    #   magnitude    -> the drop itself is self-evident (>= severe threshold)
    #   unconfirmed  -> raw signal only; may be retracted by the source later
    confirmation = Column(String(20), default="unconfirmed")

    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow,
                        onupdate=datetime.datetime.utcnow)


class CoalescedAlert(Base):
    """
    Post-once dedup for CoalescedEvent notifications. Deliberately separate from
    AlertSent (which keys on raw outage_events.id) so the two id-spaces can
    never collide and false-dedup each other.
    """
    __tablename__ = "coalesced_alerts"

    id       = Column(Integer, primary_key=True)
    event_id = Column(Integer, ForeignKey("coalesced_events.id"), index=True)
    channel  = Column(String(100))
    sent_at  = Column(DateTime, default=datetime.datetime.utcnow)
    message  = Column(Text)


class Banner(Base):
    """
    Dismissible site-wide banner notices.
    Shown to all visitors until they close them (tracked via cookie by the
    banner id). Only active banners are served to the public API; admin
    endpoints manage CRUD.
    """
    __tablename__ = "banners"

    id         = Column(Integer, primary_key=True, index=True)
    message    = Column(Text, nullable=False)       # plain-text or minimal markdown
    level      = Column(String(20), default="info")  # info | warning | success
    active     = Column(Boolean,  default=True)       # only active banners are shown
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow,
                           onupdate=datetime.datetime.utcnow)


class CountryBelief(Base):
    """
    Persistent Trinocular belief state per country (see collectors/trinocular.py).
    Carrying belief_up and availability across probe cycles — rather than
    reclassifying from scratch each time — is what lets the model use few
    probes when a country's status is stable and more when it's uncertain.
    """
    __tablename__ = "country_belief"

    country_code = Column(String(3), primary_key=True)
    belief_up    = Column(Float,    default=0.99)   # B(U): belief the country is reachable
    availability = Column(Float,    default=0.9)    # A(E(b)): running response-rate estimate
    state        = Column(String(10), default="up") # up | down | uncertain
    updated_at   = Column(DateTime, default=datetime.datetime.utcnow,
                          onupdate=datetime.datetime.utcnow)
