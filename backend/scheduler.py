import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .alerts import check_and_send_alerts, check_and_send_resolved_alerts
from .analyzer import (check_resolutions, confirm_events_with_probe,
                       expire_old_events, get_country_status, upsert_events)
from .coalescer import recompute
from .collectors.cloudflare_radar import CloudflareRadarCollector
from .collectors.ioda import IODACollector
from .collectors.ooni import OONICollector
from .collectors.probe import ProbeCollector
from .config import config
from .database import SessionLocal

log = logging.getLogger(__name__)
scheduler = AsyncIOScheduler()

# APScheduler's own max_instances=1 (its default) only stops a job from
# overlapping with itself. It does nothing for the /api/admin/refresh and
# /api/admin/probe endpoints, which fire these same functions directly via
# asyncio.create_task — so a manual trigger could run concurrently with a
# scheduled cycle (or another manual trigger). Two sessions racing to
# upsert_events()/_reset_belief_for_new_event() the same not-yet-committed
# rows produces sqlite "UNIQUE constraint failed" errors that roll back the
# whole batch. These locks make each cycle mutually exclusive regardless of
# what triggered it.
_api_lock   = asyncio.Lock()
_probe_lock = asyncio.Lock()


async def run_api_collection():
    """API cycle: IODA + OONI + Cloudflare  (every 15 min, lightweight)."""
    if _api_lock.locked():
        log.info("API collection cycle already running — skipping overlap")
        return
    async with _api_lock:
        await _run_api_collection()


async def _run_api_collection():
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
        # Raw layer (untouched): ingest this cycle's per-source observations and
        # run the existing raw resolution bookkeeping.
        expire_old_events(db)
        upsert_events(db, all_events)
        check_resolutions(db, seen_keys=seen)
        # Derived layer: coalesce raw observations into sustained events, then
        # post open/close notices on THOSE (at most once each, via dedup).
        active_ids, resolved_ids = recompute(db)
        await check_and_send_alerts(db, active_ids)
        await check_and_send_resolved_alerts(db, resolved_ids)
        log.info(f"API cycle done: {len(all_events)} raw obs, "
                 f"{len(active_ids)} active alert-worthy, {len(resolved_ids)} resolved, "
                 f"{len(seen)} (country, region) pairs seen")
    finally:
        db.close()


async def run_probe_collection():
    """Probe cycle: Trinocular-style confirmation of active events (every 30 min)."""
    if _probe_lock.locked():
        log.info("Probe cycle already running — skipping overlap")
        return
    async with _probe_lock:
        await _run_probe_collection()


async def _run_probe_collection():
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
            # Probe confirmation annotates/resolves the RAW layer only; the
            # coalescer owns actual open/close via hysteresis. Re-derive and
            # alert off the coalesced layer so a single "up" probe can't close
            # (and re-post) a still-flapping event.
            confirm_events_with_probe(db, results)
            active_ids, resolved_ids = recompute(db)
            await check_and_send_alerts(db, active_ids)
            await check_and_send_resolved_alerts(db, resolved_ids)
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
