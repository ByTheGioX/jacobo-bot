"""
Diagnostica por qué algunas propiedades aparecen en /properties/ pero NO en
/listing-v6-full-width/ (el listado público de Houzez).

Uso:
    python -m tools.diagnose_wp_listing
    python -m tools.diagnose_wp_listing --fix-meta   # añade fave_property_status faltante

Salida: reporte CSV en data/wp_diagnostic.csv con qué metas le faltan a cada propiedad.
"""

import argparse
import csv
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wordpress.wp_client import WPClient
from config.settings import WP_PROPERTY_REST_BASE

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Metas que Houzez requiere para que la propiedad aparezca en listados públicos
_REQUIRED_METAS = [
    "fave_property_status",   # slug: "for-sale" o "for-rent"
    "fave_property_price",
    "fave_property_id",
    "fave_property_type",
]

_VALID_STATUS_SLUGS = {"for-sale", "for-rent", "venta", "alquiler"}


def _fetch_all_properties(wp: WPClient) -> list[dict]:
    """Obtiene todas las propiedades del WP (paginado)."""
    all_props: list[dict] = []
    page = 1
    while True:
        try:
            page_data = wp._get(WP_PROPERTY_REST_BASE, {
                "per_page": 100,
                "page": page,
                "status": "publish,pending,draft",
                "_fields": "id,title,status,link,meta",
            })
        except Exception as e:
            logger.warning("Página %d falló: %s — terminando", page, e)
            break
        if not page_data:
            break
        all_props.extend(page_data)
        if len(page_data) < 100:
            break
        page += 1
    return all_props


def _fetch_meta_via_xmlrpc(wp: WPClient, post_id: int) -> dict[str, str]:
    """Obtiene custom_fields de un post vía XML-RPC."""
    import xmlrpc.client
    from config.settings import WP_URL, WP_USER, WP_APP_PASSWORD
    proxy = xmlrpc.client.ServerProxy(f"{WP_URL}/xmlrpc.php")
    try:
        post = proxy.wp.getPost(1, WP_USER, WP_APP_PASSWORD, post_id, ["custom_fields"])
        meta: dict[str, str] = {}
        for cf in post.get("custom_fields", []):
            meta[cf["key"]] = cf["value"]
        return meta
    except Exception as e:
        logger.debug("XML-RPC getPost %d falló: %s", post_id, e)
        return {}


def _analyze(prop: dict, meta: dict[str, str]) -> dict:
    """Devuelve un dict con campos: id, title, status, missing_metas, invalid_status, ok_for_listing."""
    missing = [m for m in _REQUIRED_METAS if not meta.get(m)]
    status_value = meta.get("fave_property_status", "")
    invalid_status = bool(status_value) and status_value.lower() not in _VALID_STATUS_SLUGS

    return {
        "id": prop["id"],
        "wp_status": prop.get("status", ""),
        "title": (prop.get("title", {}).get("rendered") or "")[:80],
        "link": prop.get("link", ""),
        "fave_property_status": status_value,
        "fave_property_price": meta.get("fave_property_price", ""),
        "fave_property_type": meta.get("fave_property_type", ""),
        "missing_metas": ",".join(missing),
        "invalid_status_slug": invalid_status,
        "ok_for_listing": (not missing) and (not invalid_status) and prop.get("status") == "publish",
    }


def _fix_status_meta(wp: WPClient, prop_id: int, current_meta: dict[str, str]) -> bool:
    """Setea fave_property_status='for-sale' si está vacío o con label en español."""
    current = current_meta.get("fave_property_status", "")
    if current in _VALID_STATUS_SLUGS:
        return False  # ya está bien
    # Inferir slug: si el label dice 'alquiler' → for-rent, si no → for-sale
    new_slug = "for-rent" if "alqu" in current.lower() else "for-sale"
    try:
        wp.set_post_meta(prop_id, {"fave_property_status": new_slug})
        logger.info("Fix aplicado a post %d: fave_property_status='%s' → '%s'", prop_id, current, new_slug)
        return True
    except Exception as e:
        logger.error("Fix falló para post %d: %s", prop_id, e)
        return False


def main():
    parser = argparse.ArgumentParser(description="Diagnóstico de listings de Houzez")
    parser.add_argument("--fix-meta", action="store_true",
                        help="Si se especifica, intenta arreglar fave_property_status faltante.")
    parser.add_argument("--limit", type=int, default=20,
                        help="Máximo de propiedades a analizar (default: 20, 0 = sin límite)")
    parser.add_argument("--csv", default="data/wp_diagnostic.csv", help="Ruta del CSV de salida")
    args = parser.parse_args()

    Path(args.csv).parent.mkdir(parents=True, exist_ok=True)

    wp = WPClient()
    logger.info("Obteniendo propiedades de WordPress...")
    props = _fetch_all_properties(wp)
    if args.limit:
        props = props[:args.limit]
        logger.info("Limitando análisis a %d propiedades (usa --limit 0 para todas).", args.limit)
    logger.info("Analizando %d propiedades vía XML-RPC...", len(props))

    rows: list[dict] = []
    fixed = 0
    for i, prop in enumerate(props, 1):
        meta = _fetch_meta_via_xmlrpc(wp, prop["id"])
        row = _analyze(prop, meta)
        rows.append(row)
        if i % 10 == 0:
            logger.info("  %d/%d analizadas...", i, len(props))

        if args.fix_meta and not row["ok_for_listing"]:
            if _fix_status_meta(wp, prop["id"], meta):
                fixed += 1

        time.sleep(1.5)  # evitar saturar el servidor con llamadas XML-RPC en serie

    # Escribir CSV
    fieldnames = list(rows[0].keys()) if rows else []
    with open(args.csv, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    # Resumen
    total = len(rows)
    ok = sum(1 for r in rows if r["ok_for_listing"])
    missing_status = sum(1 for r in rows if "fave_property_status" in r["missing_metas"])
    invalid = sum(1 for r in rows if r["invalid_status_slug"])
    print(f"\n=== DIAGNÓSTICO HOUZEZ ===")
    print(f"Total propiedades:           {total}")
    print(f"Listas para aparecer:        {ok}")
    print(f"Sin fave_property_status:    {missing_status}")
    print(f"Status con label inválido:   {invalid}  (deber ser slug: for-sale / for-rent)")
    if args.fix_meta:
        print(f"Metas corregidas:            {fixed}")
    print(f"\nCSV completo: {args.csv}")


if __name__ == "__main__":
    main()
