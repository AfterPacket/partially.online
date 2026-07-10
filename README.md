# Internet Outage Monitor

Real-time worldwide internet outage and restriction dashboard inspired by NetBlocks.
Aggregates data from IODA, OONI, and Cloudflare Radar into a live world map.

## Quick Start

```
pip install -r requirements.txt
cp .env.example .env
python run.py
```

Open http://localhost:8000 in your browser.

## Features

- Live world map coloured by outage severity
- Severity levels: Minor / Significant / Severe
- Historical 30-day timeline per country
- Event detail panel with source attribution
- Discord/webhook alerting for significant events
- Auto-refreshes every 15 minutes

## Data Sources

| Source | What it detects | Auth |
|--------|----------------|------|
| IODA (Georgia Tech) | BGP routing drops, ping failure | None |
| OONI | Web censorship anomalies | None |
| Cloudflare Radar | Traffic anomalies, shutdown annotations | Free API token |

## Configuration (.env)

| Variable | Default | Description |
|----------|---------|-------------|
| PORT | 8000 | HTTP port |
| COLLECTION_INTERVAL_MINUTES | 15 | Poll frequency |
| CLOUDFLARE_API_TOKEN | (empty) | Cloudflare Radar token |
| ALERT_WEBHOOK_URL | (empty) | Discord webhook URL |

## Severity

| Level | Score | Meaning |
|-------|-------|---------|
| Minor | 1-39 | Elevated anomalies, possible throttling |
| Significant | 40-74 | Clear disruption or confirmed censorship |
| Severe | 75-100 | Near-total shutdown or extreme filtering |
