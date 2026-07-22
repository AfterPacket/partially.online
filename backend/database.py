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
    new_cols = {
        "outage_events": [
            ("probe_confirmed",  "BOOLEAN DEFAULT 0"),
            ("probe_note",       "TEXT"),
            ("resolved",         "BOOLEAN DEFAULT 0"),
            ("resolved_at",      "DATETIME"),
            ("region_name",      "VARCHAR(100)"),
            ("actual_value",     "FLOAT"),
            ("baseline_value",   "FLOAT"),
            ("source_confirmed", "BOOLEAN DEFAULT 0"),
        ],
        "coalesced_events": [
            ("confirmation", "VARCHAR(20) DEFAULT 'unconfirmed'"),
        ],
    }
    with engine.connect() as conn:
        for table, cols in new_cols.items():
            for col, typedef in cols:
                try:
                    conn.execute(text(f"SELECT {col} FROM {table} LIMIT 1"))
                except Exception:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {typedef}"))
                    conn.commit()
                    log.info(f"[db] Added column {table}.{col}")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
