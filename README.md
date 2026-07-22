# Internet Outage Monitor

Real-time worldwide internet outage and restriction dashboard inspired by NetBlocks.
Aggregates data from IODA, OONI, and Cloudflare Radar into a live world map.

**[Live site →](https://partially.online)**

## Features

- Live world map coloured by outage severity
- Severity levels: Minor / Significant / Severe
- Trinocular-style Bayesian probing for independent outage confirmation
- Two-tier confidence: every event is labeled by what independently
  corroborates it (see [Confirmation tiers](#confirmation-tiers))
- Historical 30-day timeline per country
- Event detail panel with source attribution and probe confirmation
- Discord/webhook alerting for significant events
- Automatic Mastodon posts for significant events
- Dismissible site banners for announcements
- Auto-refreshes every 60 seconds (data collected every 15 min)

## Quick Start

```bash
pip install -r requirements.txt
cp .env.example .env          # edit as needed
python run.py
```

Open http://localhost:8000 in your browser.

## Data Sources

| Source | What it detects | Auth |
|--------|----------------|------|
| IODA (Georgia Tech) | BGP routing drops, ping/scan failure | None |
| OONI | Web censorship anomalies | None |
| Cloudflare Radar | Traffic anomalies, shutdown annotations | Free API token |

## Configuration (.env)

| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | 8000 | HTTP port |
| `HOST` | 0.0.0.0 | Bind address |
| `DATABASE_URL` | sqlite:///./outage_monitor.db | SQLAlchemy connection string |
| `COLLECTION_INTERVAL_MINUTES` | 15 | API data poll frequency |
| `PROBE_INTERVAL_MINUTES` | 30 | Trinocular probe frequency |
| `CLOUDFLARE_API_TOKEN` | (empty) | Cloudflare Radar token |
| `ALERT_WEBHOOK_URL` | (empty) | Discord webhook URL |
| `MASTODON_INSTANCE_URL` | (empty) | Mastodon instance, e.g. `https://mastodon.social` |
| `MASTODON_ACCESS_TOKEN` | (empty) | Access token with `write:statuses` scope |
| `MASTODON_VISIBILITY` | public | Post visibility: `public` / `unlisted` / `private` |
| `PUBLIC_SITE_URL` | (empty) | Public URL of this site, linked in social posts |
| `SITE_HASHTAG` | (empty) | Site hashtag added to social posts, e.g. `#PartiallyOnline` |
| `SPONSOR_GOOGLE_ID` | (empty) | Google AdSense client ID (e.g. `ca-pub-1234567890123456`) |
| `SPONSOR_GOOGLE_SLOTS` | (empty) | JSON map of placements to slot IDs, e.g. `{"header":"123","sidebar":"456"}` |
| `SPONSOR_SCRIPTS` | (empty) | JSON map of placements to sponsor script URLs, e.g. `{"header":"//unfoldedtrade.com/..."}` |
| `ADMIN_API_KEY` | (empty) | Required for admin endpoints |
| `ALLOWED_ORIGINS` | * | CORS origins (comma-separated) |

## API

### Public (read-only)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/status` | Active event counts |
| GET | `/api/events` | List active events |
| GET | `/api/events/resolved` | Recently resolved events |
| GET | `/api/countries` | Country status summary |
| GET | `/api/countries/{code}` | Country detail + history |
| GET | `/api/countries/{code}/history` | Event-level history log |
| GET | `/api/banners` | Active site banners |

### Admin (requires `X-Admin-Key` header)

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/admin/refresh` | Trigger data collection cycle |
| POST | `/api/admin/probe` | Trigger probe cycle |
| GET | `/api/admin/banners` | List all banners |
| POST | `/api/admin/banners` | Create a banner |
| PATCH | `/api/admin/banners/{id}` | Update a banner |
| DELETE | `/api/admin/banners/{id}` | Delete a banner |

### Banner examples

Create an info banner with a link:

```bash
curl -X POST http://localhost:8000/api/admin/banners \
  -H "Content-Type: application/json" \
  -H "X-Admin-Key: YOUR_KEY" \
  -d '{"message": "Now open source! [View on GitHub](https://github.com/AfterPacket/partially.online)", "level": "success"}'
```

Banner levels: `info` (blue), `warning` (amber), `success` (green).

Visitors dismiss banners with the × button — the ID is stored in localStorage
so the banner stays dismissed across page refreshes.

## Severity

| Level | Score | Meaning |
|-------|-------|---------|
| Minor | 1–39 | Elevated anomalies, possible throttling |
| Significant | 40–74 | Clear disruption or confirmed censorship |
| Severe | 75–100 | Near-total shutdown or extreme filtering |

When a signal-vs-baseline measurement exists, severity is classified by the
size of the drop — never by the source's own label. The same rule applies to
the event *type*: a source may call any BGP-datasource alert a "shutdown",
but it is only reported as one here if the signal actually collapsed
(≥ `SEVERITY_SEVERE_PCT`, default 50%); smaller dips are reported as the
generic "disruption".

## Confirmation tiers

Raw source alert feeds are early-warning signals, not verified outages —
IODA, for example, reprocesses its data and *retracts* raw alerts after the
fact, so an alert we ingested can later vanish from the source entirely.
Rather than presenting every raw alert as a confirmed incident, each event
carries a confirmation tier stating exactly what corroborates it:

| Tier | Meaning |
|------|---------|
| `source` | The source's curated/verified outage feed (IODA `/outages/events`) published a matching outage — persistent and externally checkable |
| `probe` | Our active Trinocular-style probing independently confirmed it |
| `multi-source` | Two or more independent sources observed the same condition |
| `magnitude` | The drop is self-evident (≥ severe threshold); a ≥50% signal collapse is not measurement noise |
| `unconfirmed` | Raw signal only — may later prove a false positive |

How it shapes reporting:

- Every collection cycle cross-checks recent raw IODA rows against IODA's
  curated outage feed (`backend/verifier.py`) and upgrades matches to
  `source`.
- Event descriptions state the evidence in plain language; an event that
  ends without any corroboration is labeled *"Unconfirmed — no independent
  corroboration was observed; possible false positive"*, never presented as
  a verified outage that ended.
- Social posts (Discord/Mastodon) are **only sent for corroborated events**.
  A genuine shutdown is `magnitude`-confirmed on the first cycle, so real
  incidents still alert immediately; a wobble that nothing backs up is never
  announced.
- The API exposes `confirmation` and a boolean `confirmed` on every event.

## Deployment

See `deploy/` for systemd unit file and nginx reverse proxy config.

```bash
# Install
sudo useradd -r -s /bin/false outagemonitor
sudo cp deploy/outage-monitor.service /etc/systemd/system/
sudo cp deploy/nginx.conf /etc/nginx/sites-available/outage-monitor
sudo ln -s /etc/nginx/sites-available/outage-monitor /etc/nginx/sites-enabled/

# Configure
cd /opt/internet-outage-monitor
sudo -u outagemonitor python -m venv venv
sudo -u outagemonitor venv/bin/pip install -r requirements.txt
sudo -u outagemonitor cp .env.example .env   # edit with real values

# Run
sudo systemctl enable --now outage-monitor
sudo systemctl reload nginx
```

## Security

- **XSS protection**: All user-facing data is HTML-escaped before rendering.
  CSS class names are sanitized. URLs are validated (http/https only).
  Banner messages strip HTML at the API layer and only support
  `[text](url)` link syntax with protocol-whitelisted URLs.
- **CSP**: `script-src 'self' cdn.jsdelivr.net` — no inline scripts.
  `style-src` allows `'unsafe-inline'` for Leaflet only.
  `frame-ancestors 'none'` prevents clickjacking.
- **Admin auth**: Write endpoints require an `X-Admin-Key` header.
  All admin actions are IP-logged.
- **Rate limiting**: 120 requests/minute per IP on public endpoints.
- **No secrets in client**: API keys and webhook URLs stay server-side.

## License

This project is licensed under the [GNU Affero General Public License v3.0](LICENSE).

- ✅ You can use, modify, and sell services based on this software
- ✅ You must make source code available if you run a modified version as a network service
- ✅ All derivative works must use the same license