"""
Reescribe fave_property_id de todas las propiedades publicadas para usar el
código interno (AGENCY_CODES) en lugar del slug de la agencia origen.

Ej: 'inmobiliariavasanco-111460478' → '3VCO-111460478'

Uso:
    python -m tools.fix_property_ids               # dry-run, solo muestra qué cambiaría
    python -m tools.fix_property_ids --apply       # aplica los cambios
    python -m tools.fix_property_ids --limit 5     # limita a N propiedades (test)
"""

import argparse
import logging
import sys
import time
import xmlrpc.client
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wordpress.wp_client import WPClient, _RequestsTransport
from config.settings import (
    WP_PROPERTY_REST_BASE, WP_URL, WP_USER, WP_APP_PASSWORD, AGENCY_CODES,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def _fetch_all(wp: WPClient) -> list[dict]:
    out: list[dict] = []
    page = 1
    while True:
        try:
            chunk = wp._get(WP_PROPERTY_REST_BASE, {
                "per_page": 100, "page": page, "status": "publish",
                "_fields": "id,title",
            })
        except Exception as e:
            logger.warning("Página %d falló: %s", page, e)
            break
        if not chunk:
            break
        out.extend(chunk)
        if len(chunk) < 100:
            break
        page += 1
    return out


def _get_current_id(post_id: int) -> str:
    proxy = xmlrpc.client.ServerProxy(f"{WP_URL}/xmlrpc.php", transport=_RequestsTransport())
    try:
        post = proxy.wp.getPost(1, WP_USER, WP_APP_PASSWORD, post_id, ["custom_fields"])
        for cf in post.get("custom_fields", []):
            if cf["key"] == "fave_property_id":
                return cf["value"]
    except Exception as e:
        logger.debug("XML-RPC getPost %d falló: %s", post_id, e)
    return ""


def _rewrite(current: str) -> str:
    """Convierte 'inmobiliariavasanco-111460478' → '3VCO-111460478' si la agencia está mapeada."""
    if not current or "-" not in current:
        return ""
    # Particionar en el primer '-' que va seguido de dígitos (el ID Idealista)
    # Buscamos el último segmento que sea numérico
    idx = current.rfind("-")
    if idx <= 0:
        return ""
    prefix = current[:idx]
    suffix = current[idx + 1:]
    if not suffix.isdigit():
        return ""
    short = AGENCY_CODES.get(prefix)
    if not short:
        return ""
    new_id = f"{short}-{suffix}"
    if new_id == current:
        return ""  # ya está bien
    return new_id


def _set_meta_with_retry(wp: WPClient, post_id: int, meta: dict, max_retries: int = 4) -> bool:
    """Reintenta hasta max_retries veces con backoff exponencial cuando hay timeout."""
    for attempt in range(1, max_retries + 1):
        try:
            ok = wp.set_post_meta(post_id, meta)
            if ok:
                return True
        except Exception as e:
            logger.warning("  intento %d falló (%s) — esperando antes de reintentar", attempt, str(e)[:80])
        if attempt < max_retries:
            wait = 30 * attempt  # 30s, 60s, 90s
            logger.info("  esperando %ds para reintentar post %d...", wait, post_id)
            time.sleep(wait)
    return False


def main():
    parser = argparse.ArgumentParser(description="Reescribe fave_property_id con códigos cortos")
    parser.add_argument("--apply", action="store_true", help="Aplica los cambios (sin esto, solo muestra)")
    parser.add_argument("--limit", type=int, default=0, help="Procesa solo N propiedades")
    parser.add_argument("--write-sleep", type=int, default=15,
                        help="Segundos de espera entre escrituras (default 15). Sube si la web se cae.")
    parser.add_argument("--skip-analyze", action="store_true",
                        help="Salta el análisis previo y aplica solo a las que no están en _VALID_STATUS_SLUGS (resume rápido)")
    args = parser.parse_args()

    if not AGENCY_CODES:
        logger.error("AGENCY_CODES está vacío en settings. Configura en .env y reintenta.")
        sys.exit(1)

    logger.info("Mapeo activo: %s", AGENCY_CODES)
    wp = WPClient()
    props = _fetch_all(wp)
    if args.limit:
        props = props[:args.limit]
    logger.info("Analizando %d propiedades publicadas...", len(props))

    to_change: list[tuple[int, str, str]] = []  # (post_id, current, new)
    for i, prop in enumerate(props, 1):
        current = _get_current_id(prop["id"])
        new_id = _rewrite(current)
        if new_id:
            to_change.append((prop["id"], current, new_id))
            logger.info("  [%d/%d] post %d: '%s' → '%s'", i, len(props), prop["id"], current, new_id)
        else:
            logger.debug("  [%d/%d] post %d: '%s' (sin cambio)", i, len(props), prop["id"], current)
        time.sleep(8)  # delay protector para no saturar el servidor

    print(f"\n=== RESUMEN ===")
    print(f"Total revisadas:    {len(props)}")
    print(f"Necesitan cambio:   {len(to_change)}")
    print(f"Modo:               {'APPLY' if args.apply else 'DRY-RUN (sin cambios)'}")

    if not args.apply:
        print("\nPara aplicar: vuelve a correr con --apply")
        return

    print(f"\nAplicando cambios (sleep {args.write_sleep}s + reintentos con backoff)...")
    fixed = 0
    failed: list[int] = []
    for i, (post_id, _current, new_id) in enumerate(to_change, 1):
        logger.info("[%d/%d] escribiendo post %d → '%s'", i, len(to_change), post_id, new_id)
        if _set_meta_with_retry(wp, post_id, {"fave_property_id": new_id}):
            fixed += 1
            logger.info("  OK post %d", post_id)
        else:
            failed.append(post_id)
            logger.error("  FAIL definitivo post %d", post_id)
        time.sleep(args.write_sleep)
    print(f"\nArreglados: {fixed}/{len(to_change)}")
    if failed:
        print(f"Fallaron (servidor lento, reintentar): {failed}")


if __name__ == "__main__":
    main()
