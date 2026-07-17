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

    # ── Advertising ────────────────────────────────────────────────────────────
    # Only ad *parameters* (IDs, URLs) — never raw HTML/scripts.
    # The frontend constructs ad tags from these validated values, so there
    # is zero XSS surface even if an env var is tampered with.
    ADSENSE_CLIENT_ID = os.getenv("ADSENSE_CLIENT_ID", "")   # e.g. ca-pub-1234567890
    ADSENSE_AD_SLOTS  = os.getenv("ADSENSE_AD_SLOTS", "")     # JSON: {"header":"123","sidebar":"456"}
    # Custom ad scripts (HilltopAds, etc). JSON: {"header":"//domain.com/path..."}
    # Values must be https:// or protocol-relative // URLs.
    # The backend extracts the domain for CSP script-src allowlisting.
    AD_SCRIPTS = os.getenv("AD_SCRIPTS", "")

    # ── Security ──────────────────────────────────────────────────────────────
    # Required to use any admin endpoint (POST /api/refresh, etc.)
    # Generate: python -c "import secrets; print(secrets.token_hex(32))"
    ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "")

    # Comma-separated allowed CORS origins for the public read API.
    # Use * to allow any origin (fine for public read-only data).
    ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*")


config = Config()
