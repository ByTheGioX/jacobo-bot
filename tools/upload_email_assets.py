"""
Sube a la mediateca de WordPress el logo y la foto de la firma de los correos,
y deja escritas sus URLs en configuracion/04_email.txt.

Hace falta porque en HTML de email las imágenes tienen que ir por URL pública:
Outlook no pinta los data: URI en base64 y Gmail recorta los correos a partir
de 102 KB (la foto incrustada del cliente pesaba 1,8 MB ella sola).

Uso:
    python -m tools.upload_email_assets              # dry-run: qué subiría
    python -m tools.upload_email_assets --apply      # sube y escribe las URLs
    python -m tools.upload_email_assets --apply --no-write   # sube sin tocar la config

Ejecutar en el VPS: desde otras IPs el WAF de CDmon suele cortar.
Las imágenes ya optimizadas están en assets/email/ (vienen con el git pull).
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wordpress.wp_client import WPClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

_RAIZ = Path(__file__).resolve().parent.parent
_CONFIG = _RAIZ / "configuracion" / "04_email.txt"

# (clave en la config, archivo, título en la mediateca)
_ASSETS = [
    ("FIRMA_LOGO_URL", _RAIZ / "assets" / "email" / "firma-logo.png", "Firma email - logo"),
    ("FIRMA_FOTO_URL", _RAIZ / "assets" / "email" / "firma-foto.jpg", "Firma email - foto"),
]


def _escribir_config(valores: dict) -> None:
    """Actualiza las claves FIRMA_*_URL en 04_email.txt (las añade si no están)."""
    if not _CONFIG.exists():
        logger.warning("No existe %s — copia las líneas a mano", _CONFIG)
        return

    lineas = _CONFIG.read_text(encoding="utf-8").splitlines()
    pendientes = dict(valores)
    for i, linea in enumerate(lineas):
        clave = linea.split("=", 1)[0].strip()
        if clave in pendientes:
            lineas[i] = f"{clave}={pendientes.pop(clave)}"

    if pendientes:
        lineas.append("")
        lineas.append("# Imágenes de la firma (subidas con tools/upload_email_assets.py)")
        lineas += [f"{k}={v}" for k, v in pendientes.items()]

    _CONFIG.write_text("\n".join(lineas) + "\n", encoding="utf-8")
    logger.info("Config actualizada: %s", _CONFIG)


def main():
    ap = argparse.ArgumentParser(description="Sube el logo y la foto de la firma a WordPress")
    ap.add_argument("--apply", action="store_true", help="Sube de verdad (sin esto: dry-run)")
    ap.add_argument("--no-write", action="store_true", help="No tocar configuracion/04_email.txt")
    args = ap.parse_args()

    faltan = [str(f) for _, f, _ in _ASSETS if not f.exists()]
    if faltan:
        logger.error("No encuentro estas imágenes: %s", ", ".join(faltan))
        return

    if not args.apply:
        for clave, archivo, titulo in _ASSETS:
            logger.info("[dry-run] subiría %s (%.1f KB) como '%s' -> %s",
                        archivo.name, archivo.stat().st_size / 1024, titulo, clave)
        logger.info("Dry-run completado. Repite con --apply para subirlas.")
        return

    wp = WPClient()
    valores = {}
    for clave, archivo, titulo in _ASSETS:
        media = wp.upload_media(str(archivo), titulo)
        url = (media or {}).get("source_url")
        if not url:
            logger.error("Fallo subiendo %s — se aborta para no dejar la firma a medias", archivo.name)
            return
        logger.info("Subida %s -> %s", archivo.name, url)
        valores[clave] = url

    print("\n--- URLs PARA configuracion/04_email.txt ---")
    for clave, url in valores.items():
        print(f"{clave}={url}")

    if args.no_write:
        print("\n(--no-write: copia esas dos líneas a mano)")
    else:
        _escribir_config(valores)
        print("\nYa escritas en la config. Reinicia el bot para que las cargue.")


if __name__ == "__main__":
    main()
