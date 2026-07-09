"""
Reconcilia el estado de la BD con la realidad de WordPress.

Tras el incidente de borrados masivos + restore, la BD puede tener propiedades
marcadas 'removed'/'paused' cuyo post en WP está en realidad PUBLICADO (la web
las muestra pero el buscador las ignora). Esta herramienta consulta el estado
real de cada post en WP y re-marca 'active' en la BD las que estén publicadas.

Solo cambia la BD local — NO escribe nada en WordPress (solo lecturas).

Uso (en el VPS):
    python -m tools.sync_db_status            # dry-run: muestra qué haría
    python -m tools.sync_db_status --apply    # aplica los cambios en la BD
"""

import argparse
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

from config.settings import WP_URL, WP_USER, WP_APP_PASSWORD, WP_PROPERTY_REST_BASE
from database.db import Database
from tools.setup_wordpress import _canonical_base


def main():
    ap = argparse.ArgumentParser(description="Re-marca activas en BD las propiedades publicadas en WP")
    ap.add_argument("--apply", action="store_true", help="Aplica (sin esto: dry-run)")
    ap.add_argument("--sleep", type=float, default=0.5, help="Segundos entre consultas a WP (def: 0.5)")
    args = ap.parse_args()

    auth = (WP_USER, WP_APP_PASSWORD)
    base = _canonical_base(WP_URL.rstrip("/"))
    db = Database()

    with db._conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT idealista_id, title, status, wp_post_id FROM properties "
            "WHERE status != 'active' AND wp_post_id IS NOT NULL"
        )]

    if not rows:
        print("No hay propiedades no-activas con wp_post_id — nada que reconciliar.")
        return

    print(f"WordPress: {base} | candidatas a reconciliar: {len(rows)}\n")

    to_activate, missing, errors = [], [], 0
    for i, row in enumerate(rows):
        try:
            r = requests.get(
                f"{base}/wp-json/wp/v2/{WP_PROPERTY_REST_BASE}/{row['wp_post_id']}",
                params={"_fields": "id,status"}, auth=auth, timeout=30,
            )
            if r.status_code == 200 and r.json().get("status") == "publish":
                to_activate.append(row)
                print(f"  [PUBLICADA] {row['idealista_id']} wp={row['wp_post_id']} | {(row['title'] or '')[:55]}")
            elif r.status_code == 404:
                missing.append(row)
            else:
                print(f"  [{r.json().get('status', r.status_code)}] {row['idealista_id']} wp={row['wp_post_id']} — se deja como está")
        except Exception as e:
            errors += 1
            print(f"  [ERROR] {row['idealista_id']}: {e}")
        if i < len(rows) - 1:
            time.sleep(args.sleep)

    print(f"\nPublicadas en WP (se marcarían activas): {len(to_activate)}")
    print(f"Post ya no existe en WP: {len(missing)} | Errores: {errors}")

    if not args.apply:
        print("\nDRY-RUN: la BD no se ha tocado. Añade --apply para aplicar.")
        return

    for row in to_activate:
        db.mark_active(row["idealista_id"])
    print(f"\n[OK] {len(to_activate)} propiedades re-marcadas como activas en la BD.")
    print("El buscador ya puede encontrarlas — prueba: python -m tools.test_search \"duplex\"")


if __name__ == "__main__":
    main()
