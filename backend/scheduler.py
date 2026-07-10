import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .analyzer import (check_resolutions, confirm_events_with_probe,
                       expire_old_events, get_country_status, upsert_events)
from .collectors.cloudflare_radar import CloudflareRadarCollector
from .collectors.ioda import IODACollector
from .collectors.ooni import OONICollector
from .collectors.probe import ProbeCollector
from .config import config
from .database import SessionLocal

log = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()


async def run_api_collection():
    """API cycle: IODA + OONI + Cloudflare  (every 15 min, lightweight)."""
    log.info("API collection cycle starting...")
    all_events: list = []
    for Cls in (IODACollector, OONICollector, CloudflareRadarCollector):
        col = Cls()
        try:
            all_events.extend(await col.collect())
        except Exception as exc:
            log.error(f"[{col.name}] failed: {exc}")
        finally:
            try:
                await col.close()
            except Exception:
                pass

    # (country, region) pairs actively reported in this cycle -- region is
    # None for country-wide events. Keyed this way so one region's alert
    # expiring doesn't get masked by an unrelated ongoing alert elsewhere
    # in the same country (see check_resolutions).
    seen = {(ev["country_code"], ev.get("region_name"))
             for ev in all_events if ev.get("country_code")}

    db = SessionLocal()
    try:
        expire_old_events(db)
        added = upsert_events(db, all_events)
        check_resolutions(db, seen_keys=seen)
        log.info(f"API cycle done: {len(all_events)} raw, {added} new, {len(seen)} (country, region) pairs seen")
    finally:
        db.close()


async def run_probe_collection():
    """Probe cycle: Trinocular-style confirmation of active events (every 30 min)."""
    log.info("Probe confirmation cycle starting...")
    col = ProbeCollector()
    try:
        db = SessionLocal()
        try:
            active_ccs = set(get_country_status(db).keys())
            if not active_ccs:
                log.info("[probe] No active events to confirm — skipping")
                return

            # One session spans probing (persists belief per round) and the
            # resulting event confirmation/resolution.
            results = await col.probe_countries(db, country_codes=active_ccs)
            confirm_events_with_probe(db, results)
        finally:
            db.close()
    except Exception as exc:
        log.error(f"[probe] cycle failed: {exc}")
    finally:
        try:
            await col.close()
        except Exception:
            pass


def start_scheduler():
    scheduler.add_job(run_api_collection,   "interval",
                      minutes=config.COLLECTION_INTERVAL_MINUTES, id="api_collect")
    scheduler.add_job(run_probe_collection, "interval",
                      minutes=config.PROBE_INTERVAL_MINUTES,      id="probe_collect")
    scheduler.start()
    log.info(
        f"Scheduler started — "
        f"API every {config.COLLECTION_INTERVAL_MINUTES} min, "
        f"probe every {config.PROBE_INTERVAL_MINUTES} min"
    )


def stop_scheduler():
    if scheduler.running:
        scheduler.shutdown()
