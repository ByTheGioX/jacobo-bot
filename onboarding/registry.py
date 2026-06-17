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
from pathlib import Path

import config.settings as settings

logger = logging.getLogger(__name__)

_PROFILES_PATH = Path(__file__).parent.parent / "configuracion" / "perfiles.txt"
_SLUG_RE = re.compile(r"/pro/([^/]+)/?")


def extract_slug(url: str) -> str:
    """Devuelve el slug `<slug>` de una URL idealista.com/pro/<slug>/... o "" si no hay."""
    m = _SLUG_RE.search(url or "")
    return m.group(1) if m else ""


def validate_idealista_profile_url(url: str) -> bool:
    """True solo si es una URL de perfil profesional de Idealista con slug.

    Acepta: https://www.idealista.com/pro/<slug>/  (con o sin www, con o sin barra final).
    Rechaza cualquier otra cosa (evita quemar créditos KIE con URLs basura).
    """
    url = (url or "").strip()
    if not url:
        return False
    if "idealista." not in url.lower():
        return False
    return bool(extract_slug(url))


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
    existing = _url_already_registered(url)
    if existing:
        refresh_runtime(url, existing)
        logger.info("URL ya registrada (%s) — reutilizando código %s", url, existing)
        return existing
    code = generate_unique_code()
    append_profile(name, url, code)
    refresh_runtime(url, code)
    return code
