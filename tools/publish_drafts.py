"""
Publica en WordPress TODOS los posts de propiedad que estén en BORRADOR (draft).
Actúa directamente contra WP (no depende del estado de la BD), porque el borrador
puede haberse puesto por una vía que no marcó la propiedad como 'paused' en la BD
(p.ej. acción en lote manual, o desincronización WP↔BD).

De paso sincroniza la BD: las propiedades cuyo wp_post_id quede publicado se marcan
'active', para que el bot no las vuelva a tocar por inconsistencia.

IMPORTANTE: solo publica borradores cuyo wp_post_id está trackeado en la BD local.
Los borradores huérfanos (no trackeados) suelen ser posts de la era vieja del bot
(meta 'for-sale' / 'idealista-...') que NO aparecen en el listing público y que,
publicados, inflan el contador del admin con duplicados invisibles (incidente #16).
Para esos: dejarlos en borrador, o limpiarlos con tools.draft_stale_posts.

Uso:
    python -m tools.publish_drafts                 # dry-run: lista los borradores
    python -m tools.publish_drafts --apply         # publica todos (draft -> publish)
    python -m tools.publish_drafts --apply --limit 10   # limita a N (recomendado 1ª vez)
    python -m tools.publish_drafts --apply --sleep 5    # segundos entre posts (def: 4)

PROTOCOLO CDmon (hosting compartido): cada publicación es 1 escritura REST a WP.
SIEMPRE correr primero con --limit 10. Si la web se pone lenta, parar.
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.db import Database
from wordpress.wp_client import WPClient
from config.settings import WP_PROPERTY_REST_BASE

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def _fetch_drafts(wp: WPClient) -> list[dict]:
    """Devuelve todos los posts de propiedad en estado 'draft' (paginado)."""
    drafts: list[dict] = []
    page = 1
    while page <= 20:  # tope de seguridad: 20 páginas * 100 = 2000 posts
        try:
            rows = wp._get(WP_PROPERTY_REST_BASE, {
                "status": "draft",
                "per_page": 100,
                "page": page,
                "_fields": "id,title,status,link",
            })
        except Exception as e:
            # WP devuelve 400 cuando se pide una página inexistente — fin del paginado.
            logger.debug("Fin del paginado en página %d: %s", page, e)
            break
        if not rows:
            break
        drafts.extend(rows)
        if len(rows) < 100:
            break
        page += 1
    return drafts


def _tracked_post_ids(db: Database) -> set[int]:
    """IDs de post WP que la BD local reconoce como propiedades del bot."""
    try:
        with db._conn() as conn:
            rows = conn.execute(
                "SELECT wp_post_id FROM properties WHERE wp_post_id IS NOT NULL"
            ).fetchall()
        return {int(r[0]) for r in rows}
    except Exception as e:
        logger.warning("No se pudo leer la BD: %s", e)
        return set()


def _sync_db_active(db: Database, post_ids: set[int]) -> int:
    """Marca 'active' en la BD las propiedades cuyo wp_post_id quedó publicado."""
    if not post_ids:
        return 0
    try:
        with db._conn() as conn:
            placeholders = ",".join("?" for _ in post_ids)
            cur = conn.execute(
                f"UPDATE properties SET status='active' "
                f"WHERE wp_post_id IN ({placeholders}) AND status!='active'",
                tuple(post_ids),
            )
            return cur.rowcount or 0
    except Exception as e:
        logger.warning("No se pudo sincronizar la BD (no crítico): %s", e)
        return 0


def main():
    ap = argparse.ArgumentParser(description="Publica posts de propiedad en borrador (draft -> publish)")
    ap.add_argument("--apply", action="store_true", help="Aplica los cambios (sin esto: dry-run)")
    ap.add_argument("--limit", type=int, default=0, help="Limita a N posts (0 = todos)")
    ap.add_argument("--sleep", type=float, default=4.0, help="Segundos entre posts (def: 4)")
    args = ap.parse_args()

    import time

    wp = WPClient()
    db = Database()
    drafts = _fetch_drafts(wp)

    # Blindaje (incidente #16): solo se publican borradores trackeados en la BD.
    # Los huérfanos son casi siempre posts de la era vieja (meta 'for-sale' /
    # 'idealista-...') que no aparecen en el listing y duplican inmuebles actuales.
    tracked = _tracked_post_ids(db)
    orphans = [d for d in drafts if d.get("id") not in tracked]
    drafts = [d for d in drafts if d.get("id") in tracked]
    if orphans:
        logger.warning("=== %d borradores HUÉRFANOS (no trackeados en BD) — NO se publican ===", len(orphans))
        for d in orphans:
            title = (d.get("title", {}) or {}).get("rendered", "") if isinstance(d.get("title"), dict) else d.get("title", "")
            logger.warning("  WP ID %s — %s", d.get("id"), (title or "")[:60])
        logger.warning("Si alguno fuera legítimo, publicalo a mano desde el admin de WP.")

    if args.limit:
        drafts = drafts[: args.limit]

    if not drafts:
        logger.info("No hay borradores trackeados que publicar.")
        return

    logger.info("=== %d posts de propiedad en BORRADOR ===", len(drafts))
    for d in drafts:
        title = (d.get("title", {}) or {}).get("rendered", "") if isinstance(d.get("title"), dict) else d.get("title", "")
        logger.info("  WP ID %s — %s", d.get("id"), (title or "")[:60])

    if not args.apply:
        logger.info("DRY-RUN. Nada modificado. Añade --apply para publicar (empieza con --limit 10).")
        return

    ok = fail = 0
    published_ids: set[int] = set()
    for i, d in enumerate(drafts, 1):
        pid = d.get("id")
        try:
            logger.info("[%d/%d] Publicando WP ID %s…", i, len(drafts), pid)
            wp.update_post(WP_PROPERTY_REST_BASE, pid, {"status": "publish"})
            published_ids.add(pid)
            ok += 1
        except Exception as e:
            fail += 1
            logger.error("  ERROR publicando WP ID %s: %s", pid, e)
        if i < len(drafts):
            time.sleep(args.sleep)

    synced = _sync_db_active(db, published_ids)
    logger.info("=== Publicación finalizada === Publicadas: %d | Fallidas: %d | BD sincronizadas: %d",
                ok, fail, synced)
    if ok:
        try:
            wp.purge_all_cache()
        except Exception as e:
            logger.warning("Purge de cache falló (no crítico): %s", e)


if __name__ == "__main__":
    main()
