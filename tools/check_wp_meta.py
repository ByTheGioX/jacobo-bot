"""
Muestra el ID interno (fave_property_id) de posts concretos de WordPress.

Sirve para decidir si dos posts son LA MISMA propiedad o dos distintas: el título
puede coincidir por casualidad (varios pisos en la misma calle), pero
fave_property_id lleva el ID de Idealista y es único.

Uso:
    python -m tools.check_wp_meta 45662 45608 46290
    python -m tools.check_wp_meta 45662 --esperado 110782952

PROTOCOLO CDmon: cada post es 1 llamada XML-RPC, con pausa entre ellas
(incidente 9: más de ~5 seguidas sin pausa tumban el PHP). No pasar
decenas de IDs de golpe.
"""

import argparse
import logging
import sys
import time
import xmlrpc.client
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wordpress.wp_client import WPClient, _RequestsTransport
from config.settings import WP_PROPERTY_REST_BASE, WP_URL, WP_USER, WP_APP_PASSWORD

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def _meta(post_id: int) -> dict:
    """Lee los metas relevantes de un post vía XML-RPC (la REST no expone los de Houzez)."""
    proxy = xmlrpc.client.ServerProxy(f"{WP_URL}/xmlrpc.php", transport=_RequestsTransport())
    out = {}
    try:
        post = proxy.wp.getPost(1, WP_USER, WP_APP_PASSWORD, post_id, ["custom_fields"])
        for cf in post.get("custom_fields", []):
            if cf["key"] in ("fave_property_id", "fave_property_status", "fave_property_price"):
                out[cf["key"]] = cf["value"]
    except Exception as e:
        out["_error"] = str(e)[:120]
    return out


def main():
    ap = argparse.ArgumentParser(description="Muestra el ID interno de posts de WordPress")
    ap.add_argument("post_ids", nargs="+", type=int, help="IDs de post a consultar")
    ap.add_argument("--esperado", default="",
                    help="ID de Idealista esperado: marca si coincide o no")
    ap.add_argument("--sleep", type=float, default=3.0, help="Segundos entre consultas (def: 3)")
    args = ap.parse_args()

    wp = WPClient()
    # Una sola petición REST para título/estado/enlace de todos los IDs
    info: dict[int, dict] = {}
    try:
        chunk = wp._get(WP_PROPERTY_REST_BASE, {
            "include": ",".join(str(i) for i in args.post_ids),
            "status": "publish,draft,pending,private,future,trash",
            "per_page": 100, "_fields": "id,status,title,link",
        })
        for p in chunk:
            title = p.get("title")
            if isinstance(title, dict):
                title = title.get("rendered", "")
            info[int(p["id"])] = {
                "status": p.get("status", "?"), "title": title or "", "link": p.get("link", ""),
            }
    except Exception as e:
        logger.warning("No se pudo leer info básica vía REST: %s", e)

    print()
    print("=" * 78)
    for i, pid in enumerate(args.post_ids):
        base = info.get(pid, {})
        m = _meta(pid)
        prop_id = m.get("fave_property_id", "(sin meta)")
        print(f"  post {pid}")
        print(f"    estado      : {base.get('status', '(no encontrado)')}")
        print(f"    título      : {base.get('title', '—')[:60]}")
        print(f"    ID interno  : {prop_id}")
        if m.get("fave_property_status"):
            print(f"    venta/alq.  : {m['fave_property_status']}")
        if base.get("link"):
            print(f"    ver         : {base['link']}")
        if args.esperado:
            coincide = args.esperado in str(prop_id)
            print(f"    ¿es {args.esperado}? : "
                  f"{'SÍ — MISMA propiedad (duplicado real)' if coincide else 'NO — es otra propiedad distinta'}")
        if m.get("_error"):
            print(f"    error       : {m['_error']}")
        print()
        if i < len(args.post_ids) - 1:
            time.sleep(args.sleep)
    print("=" * 78)
    print()


if __name__ == "__main__":
    main()
