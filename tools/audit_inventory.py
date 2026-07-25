"""
Auditoría de inventario: qué hay en la BD local vs qué está publicado en WordPress.

Responde a "¿cuántas propiedades tenemos realmente y dónde está el hueco?" sin
gastar créditos de Scrapfly ni KIE. Desglosa por agencia y por estado, y lista
las pausadas (borrador en WP) para poder comprobarlas a mano en Idealista.

Uso:
    python -m tools.audit_inventory            # BD + WordPress
    python -m tools.audit_inventory --no-wp    # solo BD (100% offline, sin tocar la web)
"""

import argparse
import logging
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.db import Database
from config.settings import AGENCY_CODES, WP_PROPERTY_REST_BASE

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def _agency_of(url: str) -> str:
    """Extrae el slug de agencia de https://www.idealista.com/pro/<slug>/inmueble/<id>/"""
    m = re.search(r"/pro/([^/]+)/", url or "")
    return m.group(1) if m else "(desconocida)"


def _wp_published_count() -> int:
    """Cuenta propiedades publicadas en WP. Pocas peticiones (100 por página)."""
    return len(_wp_posts_by_status("publish"))


def _wp_posts_by_status(status: str) -> dict[int, str]:
    """Devuelve {post_id: status} de todas las propiedades con ese/esos estados.

    Trae 100 por petición (3-4 llamadas para todo el catálogo) en vez de una por
    propiedad — CDmon tumba el PHP con ráfagas largas (ver incidente 9).
    """
    from wordpress.wp_client import WPClient
    wp = WPClient()
    out: dict[int, str] = {}
    page = 1
    while True:
        chunk = wp._get(WP_PROPERTY_REST_BASE, {
            "per_page": 100, "page": page, "status": status, "_fields": "id,status",
        })
        if not chunk:
            break
        for post in chunk:
            out[int(post["id"])] = post.get("status", "?")
        if len(chunk) < 100:
            break
        page += 1
    return out


def main():
    ap = argparse.ArgumentParser(description="Auditoría de inventario BD vs WordPress")
    ap.add_argument("--no-wp", action="store_true", help="No consultar WordPress (solo BD)")
    args = ap.parse_args()

    db = Database()
    with db._conn() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM properties").fetchall()]

    if not rows:
        print("La base de datos está vacía.")
        return

    by_agency: dict[str, dict] = defaultdict(lambda: {
        "activas_publicadas": 0, "activas_sin_publicar": 0, "pausadas": 0,
    })
    paused_rows = []
    unpublished_rows = []

    for r in rows:
        agency = _agency_of(r.get("url", ""))
        status = r.get("status") or "active"
        has_wp = bool(r.get("wp_post_id"))
        if status == "paused":
            by_agency[agency]["pausadas"] += 1
            paused_rows.append(r)
        elif has_wp:
            by_agency[agency]["activas_publicadas"] += 1
        else:
            by_agency[agency]["activas_sin_publicar"] += 1
            unpublished_rows.append(r)

    print()
    print("=" * 78)
    print("  INVENTARIO POR AGENCIA (base de datos local)")
    print("=" * 78)
    print(f"{'Agencia':<42} {'Código':<7} {'Public.':>8} {'Pend.':>7} {'Pausa.':>7}")
    print("-" * 78)
    tot_pub = tot_pend = tot_pause = 0
    for agency in sorted(by_agency):
        d = by_agency[agency]
        code = AGENCY_CODES.get(agency, "—")
        print(f"{agency[:42]:<42} {code:<7} {d['activas_publicadas']:>8} "
              f"{d['activas_sin_publicar']:>7} {d['pausadas']:>7}")
        tot_pub += d["activas_publicadas"]
        tot_pend += d["activas_sin_publicar"]
        tot_pause += d["pausadas"]
    print("-" * 78)
    print(f"{'TOTAL':<42} {'':<7} {tot_pub:>8} {tot_pend:>7} {tot_pause:>7}")
    print()
    print(f"  Publicadas  = visibles en la web ahora mismo")
    print(f"  Pendientes  = en BD pero sin publicar (fallo de fotos/WP; reintentan solas)")
    print(f"  Pausadas    = borrador en WP (no aparecían en Idealista al último escaneo)")
    print()

    if unpublished_rows:
        print("=" * 78)
        print(f"  PENDIENTES DE PUBLICAR ({len(unpublished_rows)})")
        print("=" * 78)
        for r in unpublished_rows:
            print(f"  {r['idealista_id']:<12} {(r.get('title') or '')[:56]}")
        print()

    if paused_rows:
        print("=" * 78)
        print(f"  PAUSADAS / EN BORRADOR ({len(paused_rows)}) — comprobar si siguen en Idealista")
        print("=" * 78)
        for r in paused_rows:
            print(f"  {r['idealista_id']:<12} {(r.get('title') or '')[:56]}")
            print(f"               {r.get('url')}")
        print()
        print("  Si alguna SIGUE publicada en Idealista, se reactiva sola en el próximo")
        print("  ciclo, o al instante con: python -m tools.reactivate_paused --apply --limit 10")
        print()

    if not args.no_wp:
        print("=" * 78)
        print("  CONTRASTE CON WORDPRESS")
        print("=" * 78)
        try:
            # Un solo barrido con todos los estados: sabemos el estado REAL de cada
            # post sin una petición por propiedad (CDmon no aguanta esas ráfagas).
            wp_all = _wp_posts_by_status("publish,draft,pending,private,future")
            wp_pub = {pid for pid, st in wp_all.items() if st == "publish"}
            print(f"  Publicadas en WordPress:   {len(wp_pub)}")
            print(f"  Publicadas según la BD:    {tot_pub}")
            print(f"  Posts totales en WP:       {len(wp_all)} (cualquier estado)")
            print()

            # Desglose exacto: qué le pasó a cada propiedad que la BD cree publicada
            desaparecidas, mal_estado = [], []
            for r in rows:
                if (r.get("status") or "active") == "paused" or not r.get("wp_post_id"):
                    continue
                pid = int(r["wp_post_id"])
                if pid not in wp_all:
                    desaparecidas.append((r, pid))
                elif wp_all[pid] != "publish":
                    mal_estado.append((r, pid, wp_all[pid]))

            if desaparecidas:
                print(f"  BORRADAS DE WP ({len(desaparecidas)}) — la BD las cree publicadas pero")
                print("  el post ya no existe (borrado desde el admin):")
                for r, pid in desaparecidas:
                    print(f"    {r['idealista_id']:<12} wp={pid:<7} {(r.get('title') or '')[:44]}")
                print()
                print("    OJO: NO se republican solas. Mientras el puntero viejo siga en la BD,")
                print("    el bot las da por publicadas y no las vuelve a crear nunca.")
                print("    Arreglar con: python -m tools.resync_deleted_posts")
                print()
            if mal_estado:
                print(f"  EN ESTADO INCORRECTO ({len(mal_estado)}) — el post existe pero no está")
                print("  publicado (borrador/pendiente):")
                for r, pid, st in mal_estado:
                    print(f"    {r['idealista_id']:<12} wp={pid:<7} [{st}] {(r.get('title') or '')[:38]}")
                print()
                print("    Arreglar con: python -m tools.publish_drafts")
                print()

            huerfanos = len(wp_pub) - (tot_pub - len(desaparecidas) - len(mal_estado))
            if huerfanos > 0:
                print(f"  HUÉRFANOS ({huerfanos}): posts publicados en WP que la BD no controla.")
                print("  Suelen ser copias de una era anterior; el bot podría duplicarlos.")
                print("  Revisar con: python -m tools.adopt_orphan_posts")
                print()
            if not desaparecidas and not mal_estado and huerfanos <= 0:
                print("  OK — la BD y WordPress coinciden.")
                print()
        except Exception as e:
            print(f"  No se pudo consultar WordPress: {e}")
            print()


if __name__ == "__main__":
    main()
