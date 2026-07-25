"""
Re-sincroniza la BD con WordPress cuando el post de una propiedad ya no existe.

Caso: la BD cree que una propiedad está publicada (tiene wp_post_id) pero ese post
no aparece en WP. Pasa cuando alguien borra posts desde el admin. Mientras el
puntero viejo siga en la BD, el bot cree que ya está publicada y NUNCA la vuelve
a crear: la propiedad desaparece de la web para siempre.

Dos caminos, ambos SIN gastar KIE.AI:
  - Post en la PAPELERA  -> se restaura tal cual (instantáneo, conserva fotos y metas).
  - Post borrado del todo -> se limpia el wp_post_id de la BD para que el ciclo normal
    lo republique reutilizando las fotos ya procesadas del cache local.

Uso:
    python -m tools.resync_deleted_posts                 # dry-run: solo informa
    python -m tools.resync_deleted_posts --apply         # aplica
    python -m tools.resync_deleted_posts --apply --limit 10   # recomendado la 1ª vez

PROTOCOLO CDmon: restaurar de papelera es 1 escritura por post. Empezar con --limit 10.
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.db import Database
from wordpress.wp_client import WPClient
from config.settings import WP_PROPERTY_REST_BASE

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

_LIVE_STATUSES = "publish,draft,pending,private,future"


def _wp_ids(wp: WPClient, status: str) -> dict[int, str]:
    """{post_id: status} en bloques de 100 (CDmon no aguanta una petición por post)."""
    out: dict[int, str] = {}
    page = 1
    while True:
        try:
            chunk = wp._get(WP_PROPERTY_REST_BASE, {
                "per_page": 100, "page": page, "status": status, "_fields": "id,status",
            })
        except Exception as e:
            logger.warning("No se pudo leer estado '%s' de WP: %s", status, e)
            break
        if not chunk:
            break
        for post in chunk:
            out[int(post["id"])] = post.get("status", "?")
        if len(chunk) < 100:
            break
        page += 1
    return out


def _cached_photos(row: dict) -> int:
    """Cuántas fotos ya procesadas siguen en disco (republicar sin gastar KIE)."""
    raw = row.get("processed_photos")
    if not raw:
        return 0
    try:
        photos = json.loads(raw)
    except Exception:
        return 0
    return sum(
        1 for p in photos
        if p.get("local_path") and Path(p["local_path"]).exists() and not p.get("skipped")
    )


def main():
    ap = argparse.ArgumentParser(description="Re-sincroniza la BD con posts borrados de WP")
    ap.add_argument("--apply", action="store_true", help="Aplica los cambios (sin esto: dry-run)")
    ap.add_argument("--limit", type=int, default=0, help="Limita a N propiedades (0 = todas)")
    ap.add_argument("--sleep", type=float, default=3.0, help="Segundos entre escrituras (def: 3)")
    args = ap.parse_args()

    db = Database()
    with db._conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM properties WHERE wp_post_id IS NOT NULL AND status != 'paused'"
        ).fetchall()]

    if not rows:
        logger.info("No hay propiedades con wp_post_id que revisar.")
        return

    wp = WPClient()
    logger.info("Leyendo estado de %d propiedades desde WordPress...", len(rows))
    live = _wp_ids(wp, _LIVE_STATUSES)
    trashed = _wp_ids(wp, "trash")
    logger.info("WP: %d posts vivos, %d en papelera", len(live), len(trashed))

    en_papelera, borrados = [], []
    for r in rows:
        pid = int(r["wp_post_id"])
        if pid in live:
            continue
        if pid in trashed:
            en_papelera.append((r, pid))
        else:
            borrados.append((r, pid))

    if args.limit:
        en_papelera = en_papelera[: args.limit]
        borrados = borrados[: args.limit]

    print()
    print("=" * 78)
    print("  POSTS QUE LA BD CREE PUBLICADOS PERO NO ESTÁN EN WORDPRESS")
    print("=" * 78)

    if en_papelera:
        print(f"\n  EN LA PAPELERA ({len(en_papelera)}) — se restauran tal cual, gratis e instantáneo:")
        for r, pid in en_papelera:
            print(f"    {r['idealista_id']:<12} wp={pid:<7} {(r.get('title') or '')[:44]}")

    if borrados:
        print(f"\n  BORRADOS DEFINITIVAMENTE ({len(borrados)}) — se limpia el puntero para que")
        print("  el ciclo los republique (reutiliza fotos del cache, sin gastar KIE):")
        for r, pid in borrados:
            n = _cached_photos(r)
            aviso = "" if n else "  [!] sin fotos en cache: gastará KIE al republicar"
            print(f"    {r['idealista_id']:<12} wp={pid:<7} fotos cache: {n:<3} "
                  f"{(r.get('title') or '')[:34]}{aviso}")

    if not en_papelera and not borrados:
        print("\n  OK — todos los posts que la BD cree publicados existen en WordPress.")
        print()
        return

    print()
    if not args.apply:
        print("  DRY-RUN. Nada modificado. Añade --apply para aplicar (empieza con --limit 10).")
        print()
        return

    restaurados = limpiados = fallidos = 0

    for i, (r, pid) in enumerate(en_papelera, 1):
        try:
            logger.info("[papelera %d/%d] restaurando post %s (%s)...",
                        i, len(en_papelera), pid, r["idealista_id"])
            wp.update_post(WP_PROPERTY_REST_BASE, pid, {"status": "publish"})
            db.mark_active(r["idealista_id"])
            restaurados += 1
        except Exception as e:
            fallidos += 1
            logger.error("  ERROR restaurando %s: %s", pid, e)
        if i < len(en_papelera):
            time.sleep(args.sleep)

    for r, pid in borrados:
        try:
            db.clear_wp_post_id(r["idealista_id"])
            limpiados += 1
            logger.info("Puntero limpiado: %s (wp=%s ya no existe)", r["idealista_id"], pid)
        except Exception as e:
            fallidos += 1
            logger.error("  ERROR limpiando %s: %s", r["idealista_id"], e)

    print()
    print(f"  Restaurados de papelera: {restaurados}")
    print(f"  Punteros limpiados:      {limpiados}  (se republican en el próximo ciclo)")
    if fallidos:
        print(f"  Fallidos:                {fallidos}")
    print()
    if limpiados:
        print("  Siguiente paso: python main.py --once   (republica las limpiadas)")
    if restaurados:
        try:
            wp.purge_all_cache()
        except Exception as e:
            logger.warning("Purge de cache falló (no crítico): %s", e)
    print()


if __name__ == "__main__":
    main()
