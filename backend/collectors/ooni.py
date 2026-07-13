import datetime
import logging

from .base import BaseCollector

log = logging.getLogger(__name__)

NAMES = {
    "AF":"Afghanistan","AL":"Albania","DZ":"Algeria","AO":"Angola","AR":"Argentina",
    "AM":"Armenia","AU":"Australia","AT":"Austria","AZ":"Azerbaijan","BH":"Bahrain",
    "BD":"Bangladesh","BY":"Belarus","BE":"Belgium","BJ":"Benin","BT":"Bhutan",
    "BO":"Bolivia","BA":"Bosnia and Herzegovina","BW":"Botswana","BR":"Brazil",
    "BF":"Burkina Faso","BN":"Brunei","BG":"Bulgaria","MM":"Myanmar","BI":"Burundi","KH":"Cambodia",
    "CM":"Cameroon","CA":"Canada","CF":"Central African Republic","TD":"Chad",
    "CI":"Ivory Coast","CN":"China","CO":"Colombia","CD":"DR Congo","CG":"Congo",
    "CR":"Costa Rica","HR":"Croatia","CU":"Cuba","CY":"Cyprus","CZ":"Czech Republic",
    "DJ":"Djibouti","DK":"Denmark","DO":"Dominican Republic","EC":"Ecuador","EG":"Egypt",
    "SV":"El Salvador","ER":"Eritrea","EE":"Estonia","ET":"Ethiopia","FJ":"Fiji",
    "FI":"Finland","FR":"France","GA":"Gabon","GM":"Gambia","GE":"Georgia",
    "DE":"Germany","GH":"Ghana","GR":"Greece","GT":"Guatemala","GN":"Guinea","GW":"Guinea-Bissau",
        "GY":"Guyana","HT":"Haiti","HN":"Honduras","HU":"Hungary","IS":"Iceland","IN":"India",
    "ID":"Indonesia","IR":"Iran","IQ":"Iraq","IE":"Ireland","IL":"Israel",
    "IT":"Italy","JM":"Jamaica","JP":"Japan","JO":"Jordan","KZ":"Kazakhstan",
    "KE":"Kenya","KP":"North Korea","KR":"South Korea","KW":"Kuwait",
    "KG":"Kyrgyzstan","LA":"Laos","LV":"Latvia","LB":"Lebanon","LS":"Lesotho",
    "LR":"Liberia","LY":"Libya","LT":"Lithuania","LU":"Luxembourg","MG":"Madagascar",
    "MW":"Malawi","MY":"Malaysia","MV":"Maldives","ML":"Mali","MR":"Mauritania",
    "MX":"Mexico","MD":"Moldova","MN":"Mongolia","ME":"Montenegro","MA":"Morocco",
    "MZ":"Mozambique","NA":"Namibia","NP":"Nepal","NL":"Netherlands","NZ":"New Zealand",
    "NI":"Nicaragua","NE":"Niger","NG":"Nigeria","MK":"North Macedonia","NO":"Norway",
    "OM":"Oman","PK":"Pakistan","PA":"Panama","PG":"Papua New Guinea","PY":"Paraguay",
    "PE":"Peru","PH":"Philippines","PL":"Poland","PT":"Portugal","PR":"Puerto Rico","QA":"Qatar",
    "RO":"Romania","RU":"Russia","RW":"Rwanda","SA":"Saudi Arabia","SN":"Senegal",
    "RS":"Serbia","SL":"Sierra Leone","SG":"Singapore","SK":"Slovakia","SI":"Slovenia",
    "SO":"Somalia","ZA":"South Africa","SS":"South Sudan","ES":"Spain","LK":"Sri Lanka",
    "SD":"Sudan","SE":"Sweden","CH":"Switzerland","SY":"Syria","TW":"Taiwan",
    "TJ":"Tajikistan","TZ":"Tanzania","TH":"Thailand","TG":"Togo",
    "TT":"Trinidad and Tobago","TN":"Tunisia","TR":"Turkey","TM":"Turkmenistan",
    "UG":"Uganda","UA":"Ukraine","AE":"United Arab Emirates","GB":"United Kingdom",
    "US":"United States","UY":"Uruguay","UZ":"Uzbekistan","VE":"Venezuela",
    "VN":"Vietnam","YE":"Yemen","ZM":"Zambia","ZW":"Zimbabwe",
}


class OONICollector(BaseCollector):
    name = "ooni"

    async def collect(self) -> list:
        now   = datetime.datetime.utcnow()
        since = (now - datetime.timedelta(days=1)).strftime("%Y-%m-%d")
        until = now.strftime("%Y-%m-%d")
        data  = await self._get(
            "https://api.ooni.io/api/v1/aggregation",
            params={
                "test_name": "web_connectivity",
                "since": since, "until": until,
                "axis_x": "probe_cc", "limit": 300,
            },
        )
        events = []
        for row in data.get("result", []):
            cc    = row.get("probe_cc", "")
            if not cc or cc == "ZZ":
                continue
            total = row.get("measurement_count", 0)
            if total < 10:
                continue
            anomaly   = row.get("anomaly_count",   0)
            confirmed = row.get("confirmed_count", 0)
            ar = (anomaly   / total) * 100
            cr = (confirmed / total) * 100
            if ar < 15 and cr < 5:
                continue
            if cr > 20 or ar > 60:
                sev, score = "severe",      min(95, cr * 2 + ar)
            elif cr > 10 or ar > 35:
                sev, score = "significant", min(75, cr * 2 + ar * 0.5)
            else:
                sev, score = "minor",       min(39, ar)
            name = NAMES.get(cc, cc)
            events.append({
                "country_code":   cc,
                "country_name":   name,
                "title":          f"Web censorship anomalies in {name}",
                "description":    (
                    f"OONI: {ar:.1f}% anomaly rate, {cr:.1f}% confirmed blocking "
                    f"across {total:,} measurements."
                ),
                "event_type":     "censorship",
                "severity":       sev,
                "severity_score": float(min(100, score)),
                "source":         "ooni",
                "source_url":     f"https://explorer.ooni.org/country/{cc}",
                # Use utcnow() so events are never outside the 24-hour filter window
                "start_time":     now,
                "end_time":       None,
                "is_active":      True,
            })
        log.info(f"[ooni] {len(events)} events")
        return events
