"""
Registro de perfiles de agencias para el alta automática.

Cuando se aprueba una solicitud de alta:
  1. Se genera un código corto único (estilo 1RG / 3VCO) que NO revela el nombre real.
  2. Se añade una línea `NOMBRE | URL | CODIGO` a configuracion/perfiles.txt.
  3. Se refrescan los globals ya cargados en memoria (AGENCY_CODES / IDEALISTA_PROFILE_URLS)
     para que el scrape inmediato use el código correcto sin reiniciar el proceso.

OJO (incidente 6 de CLAUDE.md): configuracion/ está en .gitignore y solo existe en el VPS.
Este append ocurre en runtime sobre el archivo del VPS; el git pull nunca lo trae.
"""

import logging
import random
import re
import string
import threading
from pathlib import Path
from urllib.parse import urlparse

import config.settings as settings

logger = logging.getLogger(__name__)

_PROFILES_PATH = Path(__file__).parent.parent / "configuracion" / "perfiles.txt"
_SLUG_RE = re.compile(r"/pro/([^/]+)/?")

# Serializa la generación de código + append a perfiles.txt: dos aprobaciones a la
# vez no pueden generar el mismo código ni entrelazar escrituras del archivo.
_REGISTER_LOCK = threading.Lock()

# Hosts exactos permitidos (sin comodines ni substrings: evita bypass tipo
# idealista.com.evil.com o evil.com/?idealista.). Es la frontera anti-SSRF:
# la URL validada se acaba fetcheando por el scraper al aprobar el alta.
_ALLOWED_HOSTS = {"idealista.com", "www.idealista.com"}
_PROFILE_PATH_RE = re.compile(r"/pro/[A-Za-z0-9._-]+/?$")


def extract_slug(url: str) -> str:
    """Devuelve el slug `<slug>` de una URL idealista.com/pro/<slug>/... o "" si no hay."""
    m = _SLUG_RE.search(url or "")
    return m.group(1) if m else ""


def validate_idealista_profile_url(url: str) -> bool:
    """True solo si es una URL de perfil profesional de Idealista con slug.

    Validación estricta (frontera anti-SSRF — el formulario es público y la URL
    aprobada se fetchea): esquema http(s), host EXACTO idealista.com/www.idealista.com
    (no substring), sin user:pass, puerto por defecto, y path anclado /pro/<slug>/.
    """
    url = (url or "").strip()
    if not url:
        return False
    try:
        p = urlparse(url)
    except ValueError:
        return False
    if p.scheme not in ("http", "https"):
        return False
    if p.username or p.password:
        return False
    if (p.hostname or "").rstrip(".").lower() not in _ALLOWED_HOSTS:
        return False
    try:
        if p.port not in (None, 80, 443):
            return False
    except ValueError:
        return False
    return bool(_PROFILE_PATH_RE.match(p.path or ""))


def _existing_codes() -> set[str]:
    """Códigos ya en uso (en memoria; AGENCY_CODES refleja perfiles.txt + altas en runtime)."""
    return {c.upper() for c in settings.AGENCY_CODES.values() if c}


def generate_unique_code() -> str:
    """Genera un código corto único: 1 dígito + 3 letras mayúsculas (ej. 3VCO).

    Pseudo-aleatorio a propósito — NO derivar del nombre de la agencia (incidente 8:
    el código es público y no debe exponer la inmobiliaria de origen).
    """
    used = _existing_codes()
    for _ in range(200):
        code = random.choice("123456789") + "".join(random.choices(string.ascii_uppercase, k=3))
        if code not in used:
            return code
    raise RuntimeError("No se pudo generar un código único tras 200 intentos")


def _profiles_text() -> str:
    if _PROFILES_PATH.exists():
        return _PROFILES_PATH.read_text(encoding="utf-8", errors="ignore")
    return ""


def _url_already_registered(url: str) -> str:
    """Si la URL ya está en perfiles.txt, devuelve su código; si no, "" ."""
    target = url.rstrip("/").lower()
    for raw in _profiles_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 3 and parts[1].rstrip("/").lower() == target:
            return parts[2]
    return ""


def append_profile(name: str, url: str, code: str) -> None:
    """Añade `NOMBRE | URL | CODIGO` a configuracion/perfiles.txt (crea el archivo si falta)."""
    _PROFILES_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = _profiles_text()
    prefix = "" if (not existing or existing.endswith("\n")) else "\n"
    with _PROFILES_PATH.open("a", encoding="utf-8") as fh:
        fh.write(f"{prefix}{name} | {url} | {code}\n")
    logger.info("Perfil añadido a perfiles.txt: %s | %s | %s", name, url, code)


def refresh_runtime(url: str, code: str) -> None:
    """Muta en sitio los globals ya importados para que el scrape inmediato use el código.

    AGENCY_CODES lo lee property_publisher._build_property_id (slug → código).
    IDEALISTA_PROFILE_URLS lo recorre el ciclo completo del scraper.
    """
    slug = extract_slug(url)
    if slug:
        settings.AGENCY_CODES[slug] = code
    if url not in settings.IDEALISTA_PROFILE_URLS:
        settings.IDEALISTA_PROFILE_URLS.append(url)


def register_agency_profile(name: str, url: str) -> str:
    """Registra un perfil aprobado y devuelve su código corto.

    Idempotente: si la URL ya estaba en perfiles.txt reutiliza su código y solo
    refresca los globals; si no, genera código nuevo, lo persiste y refresca.
    """
    url = url.strip()
    with _REGISTER_LOCK:
        existing = _url_already_registered(url)
        if existing:
            refresh_runtime(url, existing)
            logger.info("URL ya registrada (%s) — reutilizando código %s", url, existing)
            return existing
        code = generate_unique_code()
        append_profile(name, url, code)
        refresh_runtime(url, code)
        return code
