import logging

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from .config import config
from .models import Base

log = logging.getLogger(__name__)

_extra = {"check_same_thread": False} if "sqlite" in config.DATABASE_URL else {}
engine = create_engine(config.DATABASE_URL, connect_args=_extra)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def init_db():
    Base.metadata.create_all(bind=engine)
    _migrate()


def _migrate():
    """Add any missing columns to existing tables (idempotent)."""
    new_cols = [
        ("probe_confirmed", "BOOLEAN DEFAULT 0"),
        ("probe_note",      "TEXT"),
        ("resolved",        "BOOLEAN DEFAULT 0"),
        ("resolved_at",     "DATETIME"),
        ("region_name",     "VARCHAR(100)"),
        ("actual_value",    "FLOAT"),
        ("baseline_value",  "FLOAT"),
    ]
    with engine.connect() as conn:
        for col, typedef in new_cols:
            try:
                conn.execute(text(f"SELECT {col} FROM outage_events LIMIT 1"))
            except Exception:
                conn.execute(text(f"ALTER TABLE outage_events ADD COLUMN {col} {typedef}"))
                conn.commit()
                log.info(f"[db] Added column outage_events.{col}")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
