"""
One-shot backfill: re-coalesce all historical raw OutageEvent observations into
the derived CoalescedEvent layer, correcting the inflated 30-day counts and
sample-spacing "durations" retroactively.

Usage (from the repo root):

    python -m backend.backfill

Idempotent — safe to run repeatedly. Pre-existing incidents are marked
already-announced, so this never emits Mastodon/Discord posts.
"""
import logging

from .coalescer import backfill
from .database import SessionLocal, init_db

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(name)-24s %(levelname)s  %(message)s")


def main():
    init_db()
    db = SessionLocal()
    try:
        summary = backfill(db)
    finally:
        db.close()
    print(f"Backfill complete: {summary['coalesced_events']} coalesced events, "
          f"{summary['alerts_suppressed']} pre-existing alert rows suppressed.")


if __name__ == "__main__":
    main()
