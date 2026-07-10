import datetime
import logging

from .base import BaseCollector

log = logging.getLogger(__name__)

IODA_BASE = "https://api.ioda.inetintel.cc.gatech.edu/v2"
_SEV = {"critical": ("severe", 90), "warning": ("significant", 55), "info": ("minor", 25)}


class IODACollector(BaseCollector):
    name = "ioda"

    async def collect(self) -> list:
        now   = datetime.datetime.utcnow()
        since = now - datetime.timedelta(hours=24)
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
        etype = "shutdown" if "bgp" in ds else "disruption"
        start = datetime.datetime.utcfromtimestamp(
            alert.get("time", since.timestamp())
        )

        if entity_type == "region":
            attrs       = entity.get("attrs", {})
            cc          = attrs.get("country_code", "").upper()
            country_nm  = attrs.get("country_name", cc)
            region_name = entity.get("name", "")
            location    = f"{region_name}, {country_nm}"
        else:
            cc          = entity.get("code", "").upper()
            country_nm  = entity.get("name", cc)
            region_name = None
            location    = country_nm

        if not cc:
            return None

        def _num(x):
            try:
                return float(x)
            except (TypeError, ValueError):
                return None

        return {
            "country_code":   cc,
            "country_name":   country_nm,
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
            "source_url":     f"https://ioda.inetintel.cc.gatech.edu/country/{cc}",
            "actual_value":   _num(alert.get("value")),
            "baseline_value": _num(alert.get("historyValue")),
            "start_time":     start,
            "end_time":       None,
            "is_active":      True,
        }
