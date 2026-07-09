import json
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ──────────────────────────────────────────────────────────────
# Leer archivos TXT de la carpeta configuracion/
# ──────────────────────────────────────────────────────────────

def _load_txt_config() -> dict:
    config_dir = Path(__file__).parent.parent / "configuracion"
    result: dict = {}
    if not config_dir.exists():
        return result
    for txt_file in sorted(config_dir.glob("*.txt")):
        for line in txt_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip()
                if k:
                    result[k] = v
    return result


def _build_agencies(cfg: dict) -> list[dict]:
    """Convierte AGENCIA_N_* del TXT en la lista de agencias."""
    agencies = []
    for i in range(1, 11):
        nombre = cfg.get(f"AGENCIA_{i}_NOMBRE", "").strip()
        if not nombre:
            continue
        email = cfg.get(f"AGENCIA_{i}_EMAIL", "").strip()
        zonas_raw = cfg.get(f"AGENCIA_{i}_ZONAS", "")
        zonas = [z.strip() for z in zonas_raw.split(",") if z.strip()]
        agencies.append({"name": nombre, "email": email, "zones": zonas})
    return agencies


_TXT = _load_txt_config()


def _load_agency_table() -> tuple[list[str], dict[str, str]]:
    """Lee configuracion/perfiles*.txt como tabla con formato pipe-separado:

        NOMBRE | URL_IDEALISTA | CODIGO_INTERNO

    Una línea por agencia. Líneas con # al inicio se ignoran.
    Devuelve (urls_idealista, mapeo_slug_a_codigo).
    """
    import re as _re
    config_dir = Path(__file__).parent.parent / "configuracion"
    urls: list[str] = []
    codes: dict[str, str] = {}
    if not config_dir.exists():
        return urls, codes
    for txt_file in sorted(config_dir.glob("perfiles*.txt")):
        for raw in txt_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 3:
                continue
            _name, url, code = parts[0], parts[1], parts[2]
            if not url or not code:
                continue
            urls.append(url)
            m = _re.search(r"/pro/([^/]+)/", url)
            if m:
                codes[m.group(1)] = code
    return urls, codes


_TABLE_URLS, _TABLE_CODES = _load_agency_table()


def reload_agency_table() -> None:
    """Relee configuracion/perfiles*.txt y actualiza los globals EN SITIO.

    Muta las listas/dicts existentes (no rebindea) para que los modulos que
    hicieron `from config.settings import IDEALISTA_PROFILE_URLS` vean el cambio.
    Permite agregar agencias (tools/add_agency o edicion manual del txt) sin
    reiniciar el bot: sin esto, un ciclo en curso no conoceria la agencia nueva
    y pausaria sus propiedades como "desaparecidas" (variante del incidente 15).
    Si la tabla no existe o esta vacia, no toca nada (fallback .env intacto).
    """
    urls, codes = _load_agency_table()
    if not urls:
        return
    IDEALISTA_PROFILE_URLS[:] = urls
    AGENCY_CODES.clear()
    AGENCY_CODES.update(codes)


def _get(key: str, default: str = "") -> str:
    """TXT files tienen prioridad sobre .env."""
    val = _TXT.get(key)
    if val is not None:
        return val
    return os.getenv(key, default)


def _list(key: str, sep: str = ",") -> list[str]:
    val = _get(key, "")
    return [v.strip() for v in val.split(sep) if v.strip()]


def _int(key: str, default: int) -> int:
    try:
        return int(_get(key, str(default)))
    except ValueError:
        return default


def _float(key: str, default: float) -> float:
    try:
        return float(_get(key, str(default)))
    except ValueError:
        return default


# ──────────────────────────────────────────────────────────────
# Variables de configuración
# ──────────────────────────────────────────────────────────────

# Idealista (la tabla configuracion/perfiles*.txt tiene prioridad sobre .env)
IDEALISTA_PROFILE_URLS: list[str] = _TABLE_URLS if _TABLE_URLS else _list("IDEALISTA_PROFILE_URLS")

# Kie.ai
KIE_AI_API_KEY: str = _get("KIE_AI_API_KEY")
MAX_PHOTOS_PER_PROPERTY: int = _int("MAX_PHOTOS_PER_PROPERTY", 15)
ENABLE_HOME_STAGING: bool = _get("ENABLE_HOME_STAGING", "false").lower() == "true"
SKIP_WORDPRESS: bool = _get("SKIP_WORDPRESS", "false").lower() == "true"
MAX_PROPERTIES_PER_AGENCY: int = _int("MAX_PROPERTIES_PER_AGENCY", 0)

# WordPress
WP_URL: str = _get("WP_URL").rstrip("/")
WP_USER: str = _get("WP_USER")
WP_APP_PASSWORD: str = _get("WP_APP_PASSWORD")
WP_PROPERTY_POST_TYPE: str = _get("WP_PROPERTY_POST_TYPE", "property")
WP_PROPERTY_REST_BASE: str = _get("WP_PROPERTY_REST_BASE", "properties")

# Database
DB_PATH: str = _get("DB_PATH", "data/jacobo_bot.db")

# Email / SMTP
SMTP_HOST: str = _get("SMTP_HOST")
SMTP_PORT: int = _int("SMTP_PORT", 587)
SMTP_USER: str = _get("SMTP_USER")
SMTP_PASSWORD: str = _get("SMTP_PASSWORD")
EMAIL_FROM: str = _get("EMAIL_FROM")
EMAIL_FROM_NAME: str = _get("EMAIL_FROM_NAME", "Inmobiliaria")

# Agencias colaboradoras (desde TXT o .env como fallback)
_agencies_from_txt = _build_agencies(_TXT)
if _agencies_from_txt:
    COLLABORATING_AGENCIES: list[dict] = _agencies_from_txt
else:
    try:
        _raw = os.getenv("COLLABORATING_AGENCIES", "[]")
        COLLABORATING_AGENCIES = json.loads(_raw) if _raw else []
    except json.JSONDecodeError:
        COLLABORATING_AGENCIES = []

# Marcas de competidores
COMPETITOR_BRANDS: list[str] = _list("COMPETITOR_BRANDS")

# Proxy residencial (opcional)
PROXY_SERVER: str = _get("PROXY_SERVER", "")
PROXY_USER: str = _get("PROXY_USER", "")
PROXY_PASSWORD: str = _get("PROXY_PASSWORD", "")

# Scheduler
SCRAPE_INTERVAL_HOURS: int = _int("SCRAPE_INTERVAL_HOURS", 72)

# Scraper delays
SCRAPE_DELAY_MIN: float = _float("SCRAPE_DELAY_MIN", 25.0)
SCRAPE_DELAY_MAX: float = _float("SCRAPE_DELAY_MAX", 55.0)

# Scrape.do — tokens rotativos (lista) y token único legacy
SCRAPE_DO_TOKENS: list[str] = [t for t in _list("SCRAPE_DO_TOKENS") if t]
SCRAPE_DO_TOKEN: str = SCRAPE_DO_TOKENS[0] if SCRAPE_DO_TOKENS else _get("SCRAPE_DO_TOKEN")

# Scrapfly — alternativa a Scrape.do, pasa DataDome con ASP residencial
SCRAPFLY_API_KEY: str = _get("SCRAPFLY_API_KEY", "")

# Mapeo slug Idealista → código corto interno (oculta el nombre de la agencia origen).
# Fuente preferida: tabla configuracion/perfiles*.txt. Fallback: AGENCY_CODES JSON en .env.
if _TABLE_CODES:
    AGENCY_CODES: dict[str, str] = _TABLE_CODES
else:
    _agency_codes_raw = _get("AGENCY_CODES", "")
    try:
        AGENCY_CODES = json.loads(_agency_codes_raw) if _agency_codes_raw else {}
    except json.JSONDecodeError:
        AGENCY_CODES = {}

# OpenRouter (reemplaza a Claude AI)
OPENROUTER_API_KEY: str = _get("OPENROUTER_API_KEY")
OPENROUTER_MODEL: str = _get("OPENROUTER_MODEL", "openai/gpt-4o-mini")

# Anthropic (legacy — ya no se usa)
ANTHROPIC_API_KEY: str = _get("ANTHROPIC_API_KEY")

# Flask API
FLASK_PORT: int         = _int("FLASK_PORT", 8080)
FLASK_SECRET: str       = _get("FLASK_SECRET", "")
DASHBOARD_PASSWORD: str = _get("DASHBOARD_PASSWORD", "")

# Scraper HTTP headers
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
