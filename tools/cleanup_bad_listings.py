"""
Elimina de WordPress las propiedades que no aparecen en el listing público
(fave_property_status vacío o con label inválido) y limpia su wp_post_id en
la BD local para que el bot las re-publique correctamente en el próximo ciclo.

Uso:
    python -m tools.cleanup_bad_listings          # dry-run (solo muestra qué haría)
    python -m tools.cleanup_bad_listings --delete  # borra y limpia la BD
"""

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wordpress.wp_client import WPClient, _RequestsTransport
from database.db import Database
from config.settings import WP_PROPERTY_REST_BASE, WP_URL, WP_USER, WP_APP_PASSWORD

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

_VALID_STATUS_SLUGS = {"for-sale", "for-rent"}


def _fetch_all_published(wp: WPClient) -> list[dict]:
    props, page = [], 1
    while True:
        try:
            batch = wp._get(WP_PROPERTY_REST_BASE, {
                "per_page": 100, "page": page,
                "status": "publish",
                "_fields": "id,title,link",
            })
        except Exception as e:
            logger.warning("Página %d falló: %s", page, e)
            break
        if not batch:
            break
        props.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return props


def _get_status_meta(post_id: int) -> str:
    import xmlrpc.client
    proxy = xmlrpc.client.ServerProxy(
        f"{WP_URL}/xmlrpc.php", transport=_RequestsTransport()
    )
    try:
        post = proxy.wp.getPost(1, WP_USER, WP_APP_PASSWORD, post_id, ["custom_fields"])
        for cf in post.get("custom_fields", []):
            if cf["key"] == "fave_property_status":
                return cf["value"]
    except Exception as e:
        logger.debug("XML-RPC getPost %d falló: %s", post_id, e)
    return ""


def main():
    parser = argparse.ArgumentParser(description="Limpia propiedades con meta incorrecta")
    parser.add_argument("--delete", action="store_true",
                        help="Eliminar de WP y limpiar BD (sin este flag solo muestra)")
    args = parser.parse_args()

    if not args.delete:
        logger.info("DRY-RUN — no se borra nada. Usá --delete para ejecutar.")

    wp = WPClient()
    db = Database()

    logger.info("Obteniendo propiedades publicadas en WP...")
    props = _fetch_all_published(wp)
    logger.info("%d propiedades encontradas. Verificando meta...", len(props))

    to_fix = []
    for i, prop in enumerate(props, 1):
        status = _get_status_meta(prop["id"])
        ok = status in _VALID_STATUS_SLUGS
        if not ok:
            title = (prop.get("title", {}).get("rendered") or "")[:60]
            logger.info("  [BAD] post %d — '%s' (status='%s')", prop["id"], title, status)
            to_fix.append(prop)
        else:
            logger.debug("  [OK]  post %d (status='%s')", prop["id"], status)
        if i % 5 == 0:
            logger.info("  %d/%d verificadas...", i, len(props))
        time.sleep(8)

    logger.info("\n=== RESUMEN ===")
    logger.info("Total propiedades:    %d", len(props))
    logger.info("Con meta correcta:    %d", len(props) - len(to_fix))
    logger.info("Para borrar/reintentar: %d", len(to_fix))

    if not to_fix:
        logger.info("Nada que hacer.")
        return

    if not args.delete:
        logger.info("\nCorré con --delete para borrarlas y que el bot las re-publique.")
        return

    deleted = 0
    db_cleared = 0
    for prop in to_fix:
        wp_id = prop["id"]
        title = (prop.get("title", {}).get("rendered") or "")[:60]
        try:
            wp._delete(f"{WP_PROPERTY_REST_BASE}/{wp_id}?force=true")
            logger.info("Borrado WP post %d (%s)", wp_id, title)
            deleted += 1
        except Exception as e:
            logger.error("Error borrando WP post %d: %s", wp_id, e)
            continue

        # Limpiar wp_post_id en BD local para que el bot la re-publique
        try:
            with db._conn() as conn:
                conn.execute(
                    "UPDATE properties SET wp_post_id = NULL WHERE wp_post_id = ?",
                    (wp_id,)
                )
            db_cleared += 1
            logger.info("BD limpiada para wp_post_id=%d", wp_id)
        except Exception as e:
            logger.error("Error limpiando BD para wp_post_id=%d: %s", wp_id, e)

        time.sleep(2)

    logger.info("\n=== RESULTADO ===")
    logger.info("Borradas de WP:     %d", deleted)
    logger.info("BD limpiadas:       %d", db_cleared)
    logger.info("\nAhora corré: python main.py --once")
    logger.info("El bot re-publicará estas propiedades con fave_property_status correcto.")


if __name__ == "__main__":
    main()
