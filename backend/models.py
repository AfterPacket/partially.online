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
