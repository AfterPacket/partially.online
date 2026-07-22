from dotenv import load_dotenv
import os

load_dotenv()


class Config:
    # ── Server ────────────────────────────────────────────────────────────────
    DATABASE_URL  = os.getenv("DATABASE_URL", "sqlite:///./outage_monitor.db")
    HOST          = os.getenv("HOST", "0.0.0.0")
    PORT          = int(os.getenv("PORT", "8000"))

    # ── Collection intervals ──────────────────────────────────────────────────
    COLLECTION_INTERVAL_MINUTES = int(os.getenv("COLLECTION_INTERVAL_MINUTES", "15"))
    PROBE_INTERVAL_MINUTES      = int(os.getenv("PROBE_INTERVAL_MINUTES", "30"))

    # ── Event coalescing (the derived event layer, see backend/coalescer.py) ──
    # OONI/IODA/Cloudflare measurements are discrete and spaced, so one
    # continuous outage/censorship condition is *recorded* as many short,
    # back-to-back raw observations. The coalescer merges those raw observations
    # into sustained events so the feed, counts and Mastodon report ONE event
    # per real incident, with an honest observed window. Both knobs are in
    # minutes; tune to taste.
    #
    # GAP_MERGE — consecutive anomalous observations closer together than this
    # are treated as the SAME event. One missed/clean cycle is far shorter than
    # this, so a single clean sample can never split (fragment) an event. Raise
    # to bridge longer flapping gaps; lower to split incidents more eagerly.
    COALESCE_GAP_MERGE_MINUTES = int(os.getenv("COALESCE_GAP_MERGE_MINUTES", "90"))
    # CLEAR_HYSTERESIS — an open event is only closed once the condition has
    # stayed normal continuously for at least this long (no fresh anomalous
    # observation). Prevents one clean sample from prematurely resolving an
    # ongoing outage.
    COALESCE_CLEAR_HYSTERESIS_MINUTES = int(os.getenv("COALESCE_CLEAR_HYSTERESIS_MINUTES", "60"))
    # Window the coalescer — and the public feed/history/counts — operate over.
    COALESCE_WINDOW_DAYS = int(os.getenv("COALESCE_WINDOW_DAYS", "30"))

    # ── Severity thresholds (percent drop vs baseline) ────────────────────────
    # Severity is classified by how far a signal has dropped below its baseline,
    # NOT by a source's own label. A ~1% dip (e.g. 394 vs 398) is normal
    # variance and must never read as a shutdown/severe event. Percentages.
    SEVERITY_SEVERE_PCT      = float(os.getenv("SEVERITY_SEVERE_PCT",      "50"))
    SEVERITY_SIGNIFICANT_PCT = float(os.getenv("SEVERITY_SIGNIFICANT_PCT", "20"))
    SEVERITY_MINOR_PCT       = float(os.getenv("SEVERITY_MINOR_PCT",       "5"))

    # ── Optional integrations ─────────────────────────────────────────────────
    CLOUDFLARE_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN", "")
    ALERT_WEBHOOK_URL    = os.getenv("ALERT_WEBHOOK_URL", "")

    # Mastodon auto-posting for significant events.
    # Create the token on your instance: Preferences -> Development ->
    # New application, with the write:statuses scope.
    MASTODON_INSTANCE_URL = os.getenv("MASTODON_INSTANCE_URL", "")   # e.g. https://mastodon.social
    MASTODON_ACCESS_TOKEN = os.getenv("MASTODON_ACCESS_TOKEN", "")
    MASTODON_VISIBILITY   = os.getenv("MASTODON_VISIBILITY", "public")  # public | unlisted | private

    # Public base URL of this site, used to build the link appended to
    # social posts (same link the frontend Share button produces).
    PUBLIC_SITE_URL = os.getenv("PUBLIC_SITE_URL", "")

    # Site hashtag appended to social posts (backend Mastodon posts and the
    # frontend Share button), e.g. "#PartiallyOnline". Leading # optional.
    SITE_HASHTAG = os.getenv("SITE_HASHTAG", "")

    # ── Sponsor placements ────────────────────────────────────────────────────
    # Only placement parameters (IDs, URLs) — never raw HTML/scripts.
    # The frontend constructs tags from these validated values, so there
    # is zero XSS surface even if an env var is tampered with.
    # Names deliberately avoid ad‑blocker trigger words.
    SPONSOR_GOOGLE_ID    = os.getenv("SPONSOR_GOOGLE_ID", "")     # e.g. ca-pub-1234567890
    SPONSOR_GOOGLE_SLOTS = os.getenv("SPONSOR_GOOGLE_SLOTS", "")   # JSON: {"header":"123","sidebar":"456"}
    # Custom sponsor scripts (HilltopAds, etc). JSON: {"header":"//domain.com/path..."}
    # Values must be https:// or protocol-relative // URLs.
    # The backend extracts the domain for CSP script-src allowlisting.
    SPONSOR_SCRIPTS = os.getenv("SPONSOR_SCRIPTS", "")

    # ── Security ──────────────────────────────────────────────────────────────
    # Required to use any admin endpoint (POST /api/refresh, etc.)
    # Generate: python -c "import secrets; print(secrets.token_hex(32))"
    ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "")

    # Comma-separated allowed CORS origins for the public read API.
    # Use * to allow any origin (fine for public read-only data).
    ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*")


config = Config()