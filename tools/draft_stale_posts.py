"""
Devuelve a BORRADOR los posts de propiedad publicados que son de la "era vieja"
del bot: metas con slug inglés (fave_property_status='for-sale') y
fave_property_id con formato 'idealista-...' (anterior a los códigos de agencia).

Esos posts son huérfanos: el bot ya no los trackea en la BD (re-publicó los mismos
inmuebles como posts nuevos con metas correctas). No aparecen en el listing público
(el filtro de Houzez exige slug español), pero inflan el contador del admin y si
se les arreglara la meta aparecerían DUPLICADOS y expondrían el ID 'idealista-'.
Lo correcto es despublicarlos, NO arreglarles la meta ni borrarlos.

Detección de "stale": fave_property_id empieza con 'idealista-'. Solo los posts
viejos del bot tienen ese formato — los actuales usan código de agencia (p.ej.
'2FL-...') y los posts manuales del cliente no tienen ese meta.

Protecciones:
  - NUNCA toca posts cuyo wp_post_id está trackeado en la BD local.
  - NUNCA toca posts sin el prefijo 'idealista-' (manuales, demo, actuales).
  - Pone en draft (reversible), nunca borra.

Uso:
    python -m tools.draft_stale_posts                  # dry-run: lista los stale
    python -m tools.draft_stale_posts --apply --limit 10   # primera vez (protocolo CDmon)
    python -m tools.draft_stale_posts --apply              # todos
    python -m tools.draft_stale_posts --ids 45913,45888    # restringe a esos IDs

PROTOCOLO CDmon: 1 lectura XML-RPC por post (sleep 8s) + 1 escritura REST por
stale (sleep 4s). SIEMPRE correr primero con --limit 10.
"""

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.db import Database
from wordpress.wp_client import WPClient, _RequestsTransport
from config.settings import WP_PROPERTY_REST_BASE, WP_URL, WP_USER, WP_APP_PASSWORD

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

_STALE_ID_PREFIX = "idealista-"


def _fetch_all_published(wp: WPClient) -> list[dict]:
    props, page = [], 1
    while page <= 20:
        try:
            batch = wp._get(WP_PROPERTY_REST_BASE, {
                "per_page": 100, "page": page,
                "status": "publish",
                "_fields": "id,title,link",
            })
        except Exception as e:
            logger.debug("Fin del paginado en página %d: %s", page, e)
            break
        if not batch:
            break
        props.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return props


def _get_meta(post_id: int) -> dict[str, str]:
    import xmlrpc.client
    proxy = xmlrpc.client.ServerProxy(f"{WP_URL}/xmlrpc.php", transport=_RequestsTransport())
    try:
        post = proxy.wp.getPost(1, WP_USER, WP_APP_PASSWORD, post_id, ["custom_fields"])
        return {cf["key"]: cf["value"] for cf in post.get("custom_fields", [])}
    except Exception as e:
        logger.warning("XML-RPC getPost %d falló: %s — se omite el post", post_id, e)
        return {}


def _tracked_post_ids(db: Database) -> set[int]:
    try:
        with db._conn() as conn:
            rows = conn.execute(
                "SELECT wp_post_id FROM properties WHERE wp_post_id IS NOT NULL"
            ).fetchall()
        return {int(r[0]) for r in rows}
    except Exception as e:
        logger.warning("No se pudo leer la BD (%s) — sin protección por tracking", e)
        return set()


def main():
    ap = argparse.ArgumentParser(description="Despublica posts de propiedad de la era vieja (publish -> draft)")
    ap.add_argument("--apply", action="store_true", help="Aplica los cambios (sin esto: dry-run)")
    ap.add_argument("--limit", type=int, default=0, help="Máximo de posts a despublicar (0 = todos)")
    ap.add_argument("--ids", default="", help="Lista de IDs de post separados por coma (restringe el escaneo)")
    ap.add_argument("--sleep-read", type=float, default=8.0, help="Segundos entre lecturas XML-RPC (def: 8)")
    ap.add_argument("--sleep-write", type=float, default=4.0, help="Segundos entre escrituras REST (def: 4)")
    args = ap.parse_args()

    wp = WPClient()
    tracked = _tracked_post_ids(Database())

    logger.info("Obteniendo propiedades publicadas en WP...")
    props = _fetch_all_published(wp)
    logger.info("%d posts en publish.", len(props))

    if args.ids:
        wanted = {int(x) for x in args.ids.split(",") if x.strip()}
        props = [p for p in props if p["id"] in wanted]
        logger.info("Restringido a %d posts por --ids.", len(props))

    stale: list[dict] = []
    for i, prop in enumerate(props, 1):
        pid = prop["id"]
        title = (prop.get("title", {}).get("rendered") or "")[:60]
        if pid in tracked:
            logger.debug("  [TRACKED] post %d — lo gestiona el bot, no se toca", pid)
            continue
        meta = _get_meta(pid)
        fave_id = meta.get("fave_property_id") or ""
        if fave_id.startswith(_STALE_ID_PREFIX):
            logger.info("  [STALE] post %d — '%s' (fave_property_id=%s, status=%s)",
                        pid, title, fave_id, meta.get("fave_property_status"))
            stale.append(prop)
        else:
            logger.debug("  [OK] post %d (fave_property_id=%r)", pid, fave_id)
        if i % 10 == 0:
            logger.info("  %d/%d escaneados...", i, len(props))
        if i < len(props):
            time.sleep(args.sleep_read)

    logger.info("=== %d posts STALE (era vieja) en publish ===", len(stale))
    if not stale:
        logger.info("Nada que hacer.")
        return

    if args.limit:
        stale = stale[: args.limit]
        logger.info("Limitado a %d por --limit.", len(stale))

    if not args.apply:
        logger.info("DRY-RUN. Nada modificado. Añade --apply para despublicar (empieza con --limit 10).")
        return

    ok = fail = 0
    for i, prop in enumerate(stale, 1):
        pid = prop["id"]
        try:
            logger.info("[%d/%d] Despublicando WP ID %s (publish -> draft)…", i, len(stale), pid)
            wp.update_post(WP_PROPERTY_REST_BASE, pid, {"status": "draft"})
            ok += 1
        except Exception as e:
            fail += 1
            logger.error("  ERROR despublicando WP ID %s: %s", pid, e)
        if i < len(stale):
            time.sleep(args.sleep_write)

    logger.info("=== Finalizado === Despublicadas: %d | Fallidas: %d", ok, fail)
    if ok:
        try:
            wp.purge_all_cache()
        except Exception as e:
            logger.warning("Purge de cache falló (no crítico): %s", e)


if __name__ == "__main__":
    main()
