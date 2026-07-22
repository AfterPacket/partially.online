import datetime
import logging

from .base import BaseCollector
from .ooni import NAMES as SHORT_NAMES
from ..config import config

log = logging.getLogger(__name__)

IODA_BASE = "https://api.ioda.inetintel.cc.gatech.edu/v2"
_SEV = {"critical": ("severe", 90), "warning": ("significant", 55), "info": ("minor", 25)}


class IODACollector(BaseCollector):
    name = "ioda"

    async def collect(self) -> list:
        now = datetime.datetime.utcnow()
        # IODA's alerts endpoint returns discrete point-in-time entries, not
        # "current status" -- a country that alerted once keeps reappearing
        # in every query whose window still covers that entry's timestamp.
        # A fixed 24h lookback (this used to be hardcoded) meant a single
        # alert stayed "seen" by check_resolutions() for up to 24h after it
        # fired, even if the underlying disruption ended minutes later and
        # IODA never emitted an explicit "back to normal" entry to supersede
        # it (confirmed live: Mongolia showed exactly one alert entry, from
        # hours earlier, with nothing more recent -- yet kept blocking
        # resolution on every 15-minute poll because it was still inside the
        # window). Scoping the window to a small multiple of how often we
        # actually poll lets a stale entry age out and go absent -- and
        # therefore resolve -- within roughly an hour instead of a day.
        # Wide enough to survive a couple of missed/slow cycles, narrow
        # enough that resolution isn't held hostage by ancient history.
        lookback_minutes = max(60, config.COLLECTION_INTERVAL_MINUTES * 3)
        since = now - datetime.timedelta(minutes=lookback_minutes)
        events = []
        # Query country- and region-level alerts separately (the API takes a
        # single entityType per request). Region-level matters: a state/
        # province-scoped shutdown (e.g. Jammu & Kashmir) is usually too
        # small a share of its country's total traffic to move the national
        # aggregate enough to alert on its own — country-only polling would
        # silently miss it. IODA tracks sub-national regions via NetAcuity
        # geo entities (confirmed live: e.g. "Ladakh" and "Chhattisgarh" in
        # India both alert as their own region entities).
        for entity_type in ("country", "region"):
            data = await self._get(
                f"{IODA_BASE}/outages/alerts",
                params={
                    "from":        int(since.timestamp()),
                    "until":       int(now.timestamp()),
                    "limit":       500,
                    "entityType":  entity_type,
                },
            )
            for alert in (data.get("data") or []):
                ev = self._parse_alert(alert, entity_type, since)
                if ev:
                    events.append(ev)
        log.info(f"[ioda] {len(events)} events")
        return events

    def _parse_alert(self, alert: dict, entity_type: str, since: datetime.datetime) -> dict | None:
        entity = alert.get("entity", {})
        if entity.get("type") != entity_type:
            return None
        level = alert.get("level", "info").lower()
        if level == "normal":
            return None
        sev, score = _SEV.get(level, ("minor", 25))
        ds    = alert.get("datasource", "")

        def _num(x):
            try:
                return float(x)
            except (TypeError, ValueError):
                return None

        actual   = _num(alert.get("value"))
        baseline = _num(alert.get("historyValue"))

        # IODA assigns "critical" by condition (any dip below 99% of median),
        # not magnitude — so a bgp-datasource alert for a 142->134 wobble would
        # read as a "shutdown" if we keyed the type on datasource alone
        # (confirmed live: the Gaza Strip false-"shutdown", later retracted by
        # IODA itself). Only call it a shutdown when the signal actually
        # collapsed; keep datasource semantics when no numbers are available.
        # Mirrors coalescer.effective_event_type, which also guards history.
        drop = None
        if actual is not None and baseline is not None and baseline > 0:
            drop = max(0.0, (baseline - actual) / baseline * 100.0)
        etype = "disruption"
        if "bgp" in ds and (drop is None or drop >= config.SEVERITY_SEVERE_PCT):
            etype = "shutdown"

        start = datetime.datetime.utcfromtimestamp(
            alert.get("time", since.timestamp())
        )

        if entity_type == "region":
            attrs       = entity.get("attrs", {})
            cc          = attrs.get("country_code", "").upper()
            country_nm  = attrs.get("country_name", cc)
            region_name = entity.get("name", "")
            short       = SHORT_NAMES.get(cc)
            location    = f"{region_name}, {short or country_nm}"
            # Link to the region entity's own page — region alerts don't
            # necessarily move the country-level aggregate, so the country
            # page routinely shows nothing for a region-scoped event.
            source_url  = f"https://ioda.inetintel.cc.gatech.edu/region/{entity.get('code', '')}"
        else:
            cc          = entity.get("code", "").upper()
            country_nm  = entity.get("name", cc)
            region_name = None
            short       = SHORT_NAMES.get(cc)
            location    = short or country_nm
            source_url  = f"https://ioda.inetintel.cc.gatech.edu/country/{cc}"

        if not cc:
            return None

        return {
            "country_code":   cc,
            "country_name":   short or country_nm,
            "region_name":    region_name,
            "title":          f"Internet disruption detected in {location}",
            "description":    (
                f"IODA {level}-level alert via {ds}. "
                f"Method: {alert.get('method','?')}. "
                f"Signal: {alert.get('value')} vs baseline {alert.get('historyValue')}."
            ),
            "event_type":     etype,
            "severity":       sev,
            "severity_score": float(score),
            "source":         "ioda",
            "source_url":     source_url,
            "actual_value":   actual,
            "baseline_value": baseline,
            "start_time":     start,
            "end_time":       None,
            "is_active":      True,
        }
