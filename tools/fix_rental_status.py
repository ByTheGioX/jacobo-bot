"""
Corrige propiedades de ALQUILER que quedaron publicadas como EN VENTA.

Causa: el scraper detectaba alquiler/venta mirando si la URL contenía
"/alquiler/", pero las URLs de perfil de agencia (/pro/<agencia>/inmueble/<id>/)
nunca llevan ese segmento — así que todo se clasificaba como venta. Ya
arreglado en scraper/idealista_scraper.py (detecta por título/precio), pero
las propiedades publicadas ANTES del fix quedaron con el status equivocado.

Uso:
    python -m tools.fix_rental_status               # dry-run, solo muestra qué cambiaría
    python -m tools.fix_rental_status --apply       # aplica los cambios
    python -m tools.fix_rental_status --apply --limit 10   # limita a N (recomendado 1ª vez)
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


def _fetch_all(wp: WPClient) -> list[dict]:
    out: list[dict] = []
    page = 1
    while True:
        try:
            chunk = wp._get(WP_PROPERTY_REST_BASE, {
                "per_page": 100, "page": page, "status": "publish",
                "_fields": "id,title",
            })
        except Exception as e:
            logger.warning("Página %d falló: %s", page, e)
            break
        if not chunk:
            break
        out.extend(chunk)
        if len(chunk) < 100:
            break
        page += 1
    return out


def _get_current_status(post_id: int) -> str:
    proxy = xmlrpc.client.ServerProxy(f"{WP_URL}/xmlrpc.php", transport=_RequestsTransport())
    try:
        post = proxy.wp.getPost(1, WP_USER, WP_APP_PASSWORD, post_id, ["custom_fields"])
        for cf in post.get("custom_fields", []):
            if cf["key"] == "fave_property_status":
                return cf["value"]
    except Exception as e:
        logger.debug("XML-RPC getPost %d falló: %s", post_id, e)
    return ""


def _expected_slug(title: str) -> str:
    return "en-alquiler" if "alquiler" in title.lower() else "en-venta"


def _fix_with_retry(wp: WPClient, post_id: int, slug: str, label: str, max_retries: int = 4) -> bool:
    for attempt in range(1, max_retries + 1):
        try:
            status_id = wp.get_or_create_term("property_status", label)
            ok_meta = wp.set_post_meta(post_id, {"fave_property_status": slug})
            ok_tax = True
            if status_id:
                try:
                    wp.update_post(WP_PROPERTY_REST_BASE, post_id, {"property_status": [status_id]})
                except Exception as e:
                    ok_tax = False
                    logger.warning("  taxonomía falló post %d: %s", post_id, e)
            if ok_meta:
                return ok_tax
        except Exception as e:
            logger.warning("  intento %d falló (%s) — esperando antes de reintentar", attempt, str(e)[:80])
        if attempt < max_retries:
            wait = 30 * attempt
            logger.info("  esperando %ds para reintentar post %d...", wait, post_id)
            time.sleep(wait)
    return False


def main():
    parser = argparse.ArgumentParser(description="Corrige status venta/alquiler mal asignado")
    parser.add_argument("--apply", action="store_true", help="Aplica los cambios (sin esto, solo muestra)")
    parser.add_argument("--limit", type=int, default=0, help="Procesa solo N propiedades")
    parser.add_argument("--write-sleep", type=int, default=15,
                        help="Segundos de espera entre escrituras (default 15). Sube si la web se cae.")
    args = parser.parse_args()

    wp = WPClient()
    props = _fetch_all(wp)
    if args.limit:
        props = props[:args.limit]
    logger.info("Analizando %d propiedades publicadas...", len(props))

    to_change: list[tuple[int, str, str, str]] = []  # (post_id, title, current_slug, expected_slug)
    for i, prop in enumerate(props, 1):
        title = prop.get("title", {}).get("rendered", "") if isinstance(prop.get("title"), dict) else str(prop.get("title", ""))
        expected = _expected_slug(title)
        current = _get_current_status(prop["id"])
        if current and current != expected:
            to_change.append((prop["id"], title, current, expected))
            logger.info("  [%d/%d] post %d: '%s' — '%s' -> '%s'", i, len(props), prop["id"], title[:50], current, expected)
        else:
            logger.debug("  [%d/%d] post %d: '%s' (sin cambio, %s)", i, len(props), prop["id"], title[:50], current)
        time.sleep(8)

    print("\n=== RESUMEN ===")
    print(f"Total revisadas:    {len(props)}")
    print(f"Necesitan cambio:   {len(to_change)}")
    print(f"Modo:               {'APPLY' if args.apply else 'DRY-RUN (sin cambios)'}")

    if not args.apply:
        print("\nPara aplicar: vuelve a correr con --apply")
        return

    print(f"\nAplicando cambios (sleep {args.write_sleep}s + reintentos con backoff)...")
    fixed = 0
    failed: list[int] = []
    for i, (post_id, title, _current, expected) in enumerate(to_change, 1):
        label = "En alquiler" if expected == "en-alquiler" else "En venta"
        logger.info("[%d/%d] escribiendo post %d → '%s'", i, len(to_change), post_id, expected)
        if _fix_with_retry(wp, post_id, expected, label):
            fixed += 1
            logger.info("  OK post %d", post_id)
        else:
            failed.append(post_id)
            logger.error("  FAIL definitivo post %d", post_id)
        time.sleep(args.write_sleep)
    print(f"\nArreglados: {fixed}/{len(to_change)}")
    if failed:
        print(f"Fallaron (servidor lento, reintentar): {failed}")
    if fixed:
        try:
            wp.purge_all_cache()
        except Exception as e:
            logger.warning("Purge de cache falló (no crítico): %s", e)


if __name__ == "__main__":
    main()
