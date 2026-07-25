"""
Borra de la BD las propiedades cuyo post ya no existe en WordPress, para que el
siguiente ciclo las vuelva a scrapear, reprocesar y publicar COMO NUEVAS.

Cuándo usar esto en vez de tools/resync_deleted_posts:
  - resync_deleted_posts  -> conserva las fotos ya procesadas (rápido y gratis, pero
                             mantiene las fotos tal cual estaban).
  - purge_and_requeue     -> borra también el cache de fotos para que KIE.AI las
                             genere de cero. Necesario cuando las fotos viejas
                             pueden estar mal: p.ej. las publicadas mientras
                             ENABLE_HOME_STAGING estaba activo llevan muebles
                             inventados en terrazas/exteriores (incidente 13).

Solo toca propiedades cuyo wp_post_id apunta a un post que YA NO EXISTE en WP.
Nunca borra nada que esté publicado.

Uso:
    python -m tools.purge_and_requeue                  # dry-run: informa y estima coste
    python -m tools.purge_and_requeue --apply          # borra filas + cache de fotos
    python -m tools.purge_and_requeue --apply --limit 10
    python -m tools.purge_and_requeue --apply --keep-photos   # conserva el cache

Después: python main.py --once   (las vuelve a scrapear y publicar)
"""

import argparse
import json
import logging
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.db import Database
from wordpress.wp_client import WPClient
from config.settings import WP_PROPERTY_REST_BASE

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

_LIVE_STATUSES = "publish,draft,pending,private,future,trash"
_KIE_COST_PER_PHOTO = 0.027  # USD, según la tarifa de KIE.AI


def _wp_existing_ids(wp: WPClient) -> set[int]:
    """IDs de todos los posts de propiedad que existen en WP, en cualquier estado."""
    out: set[int] = set()
    page = 1
    while True:
        try:
            chunk = wp._get(WP_PROPERTY_REST_BASE, {
                "per_page": 100, "page": page, "status": _LIVE_STATUSES, "_fields": "id",
            })
        except Exception as e:
            logger.warning("Fallo leyendo página %d de WP: %s", page, e)
            break
        if not chunk:
            break
        out.update(int(p["id"]) for p in chunk)
        if len(chunk) < 100:
            break
        page += 1
    return out


def _processed_dirs(row: dict) -> set[Path]:
    """Carpetas 'processed/' del cache local de esta propiedad (las fotos ya mejoradas)."""
    raw = row.get("processed_photos")
    if not raw:
        return set()
    try:
        photos = json.loads(raw)
    except Exception:
        return set()
    dirs: set[Path] = set()
    for p in photos:
        lp = p.get("local_path")
        if not lp:
            continue
        parent = Path(lp).parent
        # Solo carpetas 'processed' dentro de data/photos — nunca tocar otra cosa
        if parent.name == "processed" and "photos" in parent.parts:
            dirs.add(parent)
    return dirs


def _photo_count(row: dict) -> int:
    raw = row.get("processed_photos")
    if not raw:
        return 0
    try:
        return sum(1 for p in json.loads(raw) if not p.get("skipped"))
    except Exception:
        return 0


def main():
    ap = argparse.ArgumentParser(
        description="Borra de la BD propiedades con post inexistente en WP para republicarlas de cero")
    ap.add_argument("--apply", action="store_true", help="Aplica los cambios (sin esto: dry-run)")
    ap.add_argument("--limit", type=int, default=0, help="Limita a N propiedades (0 = todas)")
    ap.add_argument("--keep-photos", action="store_true",
                    help="No borrar el cache de fotos (se reutilizan, no gasta KIE)")
    args = ap.parse_args()

    db = Database()
    with db._conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM properties WHERE wp_post_id IS NOT NULL"
        ).fetchall()]

    if not rows:
        logger.info("No hay propiedades con wp_post_id.")
        return

    wp = WPClient()
    logger.info("Comprobando en WordPress el estado de %d propiedades...", len(rows))
    existing = _wp_existing_ids(wp)
    logger.info("WP tiene %d posts de propiedad (cualquier estado).", len(existing))

    huerfanas = [r for r in rows if int(r["wp_post_id"]) not in existing]
    if args.limit:
        huerfanas = huerfanas[: args.limit]

    if not huerfanas:
        print("\n  OK — todas las propiedades de la BD tienen su post en WordPress.\n")
        return

    total_fotos = sum(_photo_count(r) for r in huerfanas)
    coste = total_fotos * _KIE_COST_PER_PHOTO

    print()
    print("=" * 78)
    print(f"  A BORRAR DE LA BD Y REPUBLICAR DESDE CERO ({len(huerfanas)})")
    print("=" * 78)
    for r in huerfanas:
        print(f"    {r['idealista_id']:<12} wp={r['wp_post_id']:<7} "
              f"{(r.get('title') or '')[:46]}")
    print()
    print(f"  Fotos que habrá que reprocesar:  ~{total_fotos}")
    if args.keep_photos:
        print("  Cache de fotos:                   SE CONSERVA (--keep-photos) → sin coste KIE")
    else:
        print(f"  Cache de fotos:                   SE BORRA → KIE regenerará todo")
        print(f"  Coste estimado en KIE.AI:         ~{coste:.2f} USD")
    print()
    print("  Tras aplicar, ejecutar:  python main.py --once")
    print("  (las vuelve a scrapear de Idealista y publicar como nuevas)")
    print()

    if not args.apply:
        print("  DRY-RUN. Nada modificado. Añade --apply para aplicar.")
        print()
        return

    borradas = fotos_borradas = 0
    for r in huerfanas:
        idealista_id = r["idealista_id"]
        if not args.keep_photos:
            for d in _processed_dirs(r):
                try:
                    if d.exists():
                        shutil.rmtree(d)
                        fotos_borradas += 1
                        logger.info("  cache borrado: %s", d)
                except Exception as e:
                    logger.warning("  no se pudo borrar %s: %s", d, e)
        try:
            with db._conn() as conn:
                conn.execute("DELETE FROM properties WHERE idealista_id = ?", (idealista_id,))
            borradas += 1
            logger.info("BD: fila borrada %s (%s)", idealista_id, (r.get("title") or "")[:40])
        except Exception as e:
            logger.error("  ERROR borrando %s de la BD: %s", idealista_id, e)

    print()
    print(f"  Filas borradas de la BD:      {borradas}")
    if not args.keep_photos:
        print(f"  Carpetas de fotos borradas:   {fotos_borradas}")
    print()
    print("  SIGUIENTE PASO:  python main.py --once")
    print()


if __name__ == "__main__":
    main()
