import datetime
import logging

from .base import BaseCollector
from .ooni import NAMES as SHORT_NAMES
from ..config import config

log = logging.getLogger(__name__)
CF = "https://api.cloudflare.com/client/v4/radar"


class CloudflareRadarCollector(BaseCollector):
    name = "cloudflare"

    async def collect(self) -> list:
        if not config.CLOUDFLARE_API_TOKEN:
            log.debug("[cloudflare] No token configured, skipping")
            return []
        now   = datetime.datetime.utcnow()
        since = now - datetime.timedelta(hours=24)
        data  = await self._get(
            f"{CF}/annotations/outages",
            headers={"Authorization": f"Bearer {config.CLOUDFLARE_API_TOKEN}"},
            params={
                "dateStart": since.isoformat() + "Z",
                "dateEnd":   now.isoformat()   + "Z",
                "limit": 100,
            },
        )
        if not data.get("success"):
            return []
        events = []
        for ann in data.get("result", {}).get("annotations", []):
            otype = ann.get("outageType", "").lower()
            if "shutdown" in otype:
                sev, score, etype = "severe",      90, "shutdown"
            elif "disruption" in otype:
                sev, score, etype = "significant", 60, "disruption"
            else:
                sev, score, etype = "minor",       30, "disruption"
            try:
                start = datetime.datetime.fromisoformat(
                    ann.get("startDate", now.isoformat()).replace("Z", "+00:00")
                )
            except Exception:
                start = now
            for loc in ann.get("locations", []):
                cc   = loc.get("code", "").upper()
                name = loc.get("name", cc)
                short = SHORT_NAMES.get(cc)
                events.append({
                    "country_code":   cc,
                    "country_name":   name,
                    "title":          ann.get("title") or f"Internet outage in {short or name}",
                    "description":    ann.get("description", ""),
                    "event_type":     etype,
                    "severity":       sev,
                    "severity_score": float(score),
                    "source":         "cloudflare",
                    "source_url":     "https://radar.cloudflare.com/outage-center",
                    "start_time":     start,
                    "end_time":       None,
                    "is_active":      True,
                })
        log.info(f"[cloudflare] {len(events)} events")
        return events
