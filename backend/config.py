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

    # ── Security ──────────────────────────────────────────────────────────────
    # Required to use any admin endpoint (POST /api/refresh, etc.)
    # Generate: python -c "import secrets; print(secrets.token_hex(32))"
    ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "")

    # Comma-separated allowed CORS origins for the public read API.
    # Use * to allow any origin (fine for public read-only data).
    ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*")


config = Config()
