"""
Restaura en WordPress las propiedades que fueron borradas por error (status='removed'
o 'paused' en la BD) REUTILIZANDO las fotos ya procesadas — CERO gasto de KIE.AI,
CERO scraping de Idealista.

Contexto: un fallo de scraping (rate-limit de Scrapfly) hizo que el monitor
interpretara propiedades válidas como "eliminadas" y las borrara de WP. Las fotos
mejoradas siguen guardadas en la BD (processed_photos) y en data/photos/<carpeta>/processed/.
Este script vuelve a crear los posts en WP subiendo esas fotos ya procesadas.

Uso:
    python -m tools.restore_deleted                 # dry-run: muestra qué restauraría
    python -m tools.restore_deleted --apply         # aplica (crea posts en WP)
    python -m tools.restore_deleted --apply --limit 10   # limita a N (recomendado 1ª vez)
    python -m tools.restore_deleted --apply --sleep 5    # segundos entre propiedades (def: 4)

PROTOCOLO CDmon (hosting compartido): crear posts + subir fotos satura PHP.
SIEMPRE correr primero con --limit 10 y --sleep alto. Si la web se pone lenta, parar.
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.db import Database
from monitor.property_monitor import PropertyMonitor
from wordpress.property_publisher import PropertyPublisher

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def _candidates(db: Database) -> list[dict]:
    """Propiedades inactivas (removed/paused) que tienen fotos ya procesadas."""
    with db._conn() as conn:
        rows = conn.execute(
            "SELECT * FROM properties WHERE status IN ('removed', 'paused')"
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        photos_raw = d.get("processed_photos")
        if not photos_raw:
            continue
        try:
            photos = json.loads(photos_raw)
        except Exception:
            continue
        if not photos:
            continue
        d["_photos"] = photos
        out.append(d)
    return out


def main():
    ap = argparse.ArgumentParser(description="Restaura propiedades borradas reutilizando fotos KIE")
    ap.add_argument("--apply", action="store_true", help="Aplica los cambios (sin esto: dry-run)")
    ap.add_argument("--limit", type=int, default=0, help="Limita a N propiedades (0 = todas)")
    ap.add_argument("--sleep", type=float, default=4.0, help="Segundos entre propiedades del mismo lote (def: 4)")
    ap.add_argument("--batch", type=int, default=0, help="Procesa de a N y pausa entre lotes (0 = sin lotes)")
    ap.add_argument("--batch-pause", type=float, default=300.0, help="Segundos de pausa entre lotes (def: 300 = 5 min)")
    args = ap.parse_args()

    db = Database()
    cands = _candidates(db)
    if args.limit:
        cands = cands[: args.limit]

    if not cands:
        logger.info("No hay propiedades restaurables (removed/paused con fotos procesadas).")
        return

    # Cuántas fotos locales existen realmente (si el cache se borró, NO restauramos:
    # crear un post sin fotos es peor que dejarlo pendiente para un ciclo completo).
    logger.info("=== %d propiedades restaurables ===", len(cands))
    for d in cands:
        photos = d["_photos"]
        d["_local_ok"] = sum(
            1 for p in photos
            if p.get("local_path") and Path(p["local_path"]).exists() and not p.get("skipped")
        )
        total = sum(1 for p in photos if not p.get("skipped"))
        flag = "" if d["_local_ok"] else "  ⚠ SIN FOTOS LOCALES — se omite (correr ciclo completo)"
        logger.info(
            "  %s — %s | fotos locales: %d/%d | wp_post_id viejo: %s%s",
            d["idealista_id"], (d.get("title") or "")[:45], d["_local_ok"], total, d.get("wp_post_id"), flag,
        )

    ready = [d for d in cands if d["_local_ok"] > 0]
    skipped_no_photos = len(cands) - len(ready)
    if skipped_no_photos:
        logger.warning(
            "%d propiedad(es) sin fotos locales se omitirán. Para esas, ejecutar un ciclo "
            "completo (python main.py --once) que reutiliza el cache KIE o reprocesa.",
            skipped_no_photos,
        )

    if not args.apply:
        logger.info("DRY-RUN. Nada modificado. Añade --apply para restaurar (empieza con --limit 10).")
        return

    cands = ready
    if not cands:
        logger.info("Nada que restaurar con fotos locales disponibles.")
        return

    publisher = PropertyPublisher()
    restored = failed = 0

    for i, d in enumerate(cands, 1):
        idealista_id = d["idealista_id"]
        try:
            prop = PropertyMonitor._db_prop_to_property(d)
            photos = d["_photos"]
            logger.info("[%d/%d] Restaurando %s …", i, len(cands), idealista_id)
            # existing_wp_id=None → crea un post NUEVO (el viejo fue borrado de WP).
            new_id = publisher.publish(prop, photos, existing_wp_id=None)
            if new_id:
                db.set_wp_post_id(idealista_id, new_id)
                db.mark_active(idealista_id)
                restored += 1
                logger.info("  OK → nuevo wp_post_id=%s", new_id)
            else:
                failed += 1
                logger.error("  FALLÓ — publish devolvió None")
        except Exception as e:
            failed += 1
            logger.error("  ERROR restaurando %s: %s", idealista_id, e)

        if i < len(cands):
            # Si terminó un lote completo → pausa larga entre lotes; si no, pausa corta.
            if args.batch and i % args.batch == 0:
                logger.info(
                    "Lote de %d completado (%d/%d). Pausando %.0fs antes del próximo lote…",
                    args.batch, i, len(cands), args.batch_pause,
                )
                time.sleep(args.batch_pause)
            else:
                time.sleep(args.sleep)

    logger.info("=== Restauración finalizada === Restauradas: %d | Fallidas: %d", restored, failed)
    if restored:
        try:
            publisher.wp.purge_all_cache()
        except Exception as e:
            logger.warning("Purge de cache falló (no crítico): %s", e)


if __name__ == "__main__":
    main()
