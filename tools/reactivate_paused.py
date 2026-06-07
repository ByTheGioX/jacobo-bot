"""
Reactiva en WordPress las propiedades que quedaron en BORRADOR (status='paused' en BD)
por un fallo de scrape. Solo cambia el estado del post de 'draft' a 'publish' — NO
scrapea, NO gasta KIE, NO recrea nada. El post ya existe en WP intacto.

Contexto: un scrape incompleto (rate-limit/soft-block de Scrapfly) hace que el monitor
no "vea" propiedades que SIGUEN en Idealista y las pause (draft) por error. En un ciclo
normal exitoso se reactivan solas (_handle_reappeared). Pero si el scrape sigue fallando,
esta herramienta las republica al instante.

Uso:
    python -m tools.reactivate_paused                 # dry-run: muestra qué reactivaría
    python -m tools.reactivate_paused --apply         # aplica (draft -> publish)
    python -m tools.reactivate_paused --apply --limit 10   # limita a N (recomendado 1ª vez)
    python -m tools.reactivate_paused --apply --sleep 5    # segundos entre propiedades (def: 4)

PROTOCOLO CDmon (hosting compartido): cada reactivación es 1 escritura REST a WP.
SIEMPRE correr primero con --limit 10. Si la web se pone lenta, parar.
"""

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.db import Database
from wordpress.property_publisher import PropertyPublisher

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def main():
    ap = argparse.ArgumentParser(description="Reactiva propiedades pausadas (borrador -> publicada)")
    ap.add_argument("--apply", action="store_true", help="Aplica los cambios (sin esto: dry-run)")
    ap.add_argument("--limit", type=int, default=0, help="Limita a N propiedades (0 = todas)")
    ap.add_argument("--sleep", type=float, default=4.0, help="Segundos entre propiedades (def: 4)")
    args = ap.parse_args()

    db = Database()
    paused_ids = db.get_paused_ids()
    cands = [db.get_property(pid) for pid in paused_ids]
    cands = [d for d in cands if d and d.get("wp_post_id")]
    if args.limit:
        cands = cands[: args.limit]

    skipped_no_wp = len(paused_ids) - len([d for d in (db.get_property(p) for p in paused_ids) if d and d.get("wp_post_id")])

    if not cands:
        logger.info("No hay propiedades pausadas con wp_post_id para reactivar.")
        if skipped_no_wp:
            logger.warning(
                "%d pausadas SIN wp_post_id (el post no existe en WP) — esas necesitan "
                "ciclo completo o restore_deleted, no esta herramienta.", skipped_no_wp,
            )
        return

    logger.info("=== %d propiedades pausadas reactivables ===", len(cands))
    for d in cands:
        logger.info("  %s — %s | wp_post_id: %s",
                    d["idealista_id"], (d.get("title") or "")[:50], d.get("wp_post_id"))
    if skipped_no_wp:
        logger.warning("%d pausadas sin wp_post_id se omiten (usar ciclo completo).", skipped_no_wp)

    if not args.apply:
        logger.info("DRY-RUN. Nada modificado. Añade --apply para reactivar (empieza con --limit 10).")
        return

    publisher = PropertyPublisher()
    ok = fail = 0
    for i, d in enumerate(cands, 1):
        idealista_id = d["idealista_id"]
        try:
            logger.info("[%d/%d] Reactivando %s (wp=%s)…", i, len(cands), idealista_id, d["wp_post_id"])
            publisher.unpause(d["wp_post_id"])
            db.mark_active(idealista_id)
            ok += 1
        except Exception as e:
            fail += 1
            logger.error("  ERROR reactivando %s: %s", idealista_id, e)
        if i < len(cands):
            time.sleep(args.sleep)

    logger.info("=== Reactivación finalizada === Reactivadas: %d | Fallidas: %d", ok, fail)
    if ok:
        try:
            publisher.wp.purge_all_cache()
        except Exception as e:
            logger.warning("Purge de cache falló (no crítico): %s", e)


if __name__ == "__main__":
    main()
