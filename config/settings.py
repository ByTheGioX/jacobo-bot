import os
import json
from dotenv import load_dotenv

load_dotenv()


def _list(key: str, sep: str = ",") -> list[str]:
    val = os.getenv(key, "")
    return [v.strip() for v in val.split(sep) if v.strip()]


def _json(key: str, default):
    raw = os.getenv(key, "")
    try:
        return json.loads(raw) if raw else default
    except json.JSONDecodeError:
        return default


# Idealista
IDEALISTA_PROFILE_URLS: list[str] = _list("IDEALISTA_PROFILE_URLS")

# Kie.ai
KIE_AI_API_KEY: str = os.getenv("KIE_AI_API_KEY", "")
KIE_AI_BASE_URL: str = os.getenv("KIE_AI_BASE_URL", "https://api.kie.ai/v1")

# WordPress
WP_URL: str = os.getenv("WP_URL", "").rstrip("/")
WP_USER: str = os.getenv("WP_USER", "")
WP_APP_PASSWORD: str = os.getenv("WP_APP_PASSWORD", "")
WP_PROPERTY_POST_TYPE: str = os.getenv("WP_PROPERTY_POST_TYPE", "property")

# Database
DB_PATH: str = os.getenv("DB_PATH", "data/jacobo_bot.db")

# Email / SMTP
SMTP_HOST: str = os.getenv("SMTP_HOST", "")
SMTP_PORT: int = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER: str = os.getenv("SMTP_USER", "")
SMTP_PASSWORD: str = os.getenv("SMTP_PASSWORD", "")
EMAIL_FROM: str = os.getenv("EMAIL_FROM", "")
EMAIL_FROM_NAME: str = os.getenv("EMAIL_FROM_NAME", "Inmobiliaria")

COLLABORATING_AGENCIES: list[dict] = _json("COLLABORATING_AGENCIES", [])

# Scheduler
CHECK_INTERVAL_HOURS: int = int(os.getenv("CHECK_INTERVAL_HOURS", "24"))

# Text cleanup
COMPETITOR_BRANDS: list[str] = _list("COMPETITOR_BRANDS")

# Claude AI
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")

# Scraper HTTP headers to mimic a real browser
SCRAPER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-ES,es;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Referer": "https://www.idealista.com/",
}
