"""
Inserta la cajita [jacobo_search_box] en el hero de la portada, justo debajo del
buscador de filtros de Houzez, editando el layout de Elementor EN REMOTO
(ruta jacobo/v1/elementor del plugin v1.3.0+). El plugin guarda backup
automático antes de escribir; --restore lo devuelve tal como estaba.

Uso (en el VPS):
    python -m tools.place_search_box            # dry-run: muestra qué haría
    python -m tools.place_search_box --apply    # inserta de verdad
    python -m tools.place_search_box --restore  # deshace (vuelve al backup)

Opciones: --page N (defecto: portada) | --after WIDGET_ID | --shortcode "..."
"""

import argparse
import json
import random
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

from config.settings import WP_URL, WP_USER, WP_APP_PASSWORD
from tools.setup_wordpress import _canonical_base

DEFAULT_SHORTCODE = (
    '[jacobo_search_box '
    'titulo="¿Qué estás buscando?" '
    'subtitulo="Descríbelo con tus palabras y lo encontramos por ti" '
    'placeholder="Ej: piso de 2 habitaciones en Málaga con terraza, hasta 200.000 €"]'
)
TARGET_WIDGET = "houzez_elementor_search_builder"


def _find(elems, pred):
    """Devuelve (lista_padre, indice, elemento) del primer elemento que cumpla pred."""
    for i, e in enumerate(elems or []):
        if pred(e):
            return elems, i, e
        hit = _find(e.get("elements"), pred)
        if hit:
            return hit
    return None


def _contains_box(elems) -> bool:
    for e in elems or []:
        sc = (e.get("settings") or {}).get("shortcode") or ""
        if "jacobo_search_box" in sc:
            return True
        if _contains_box(e.get("elements")):
            return True
    return False


def main():
    ap = argparse.ArgumentParser(description="Coloca la cajita de IA en el hero (Elementor remoto)")
    ap.add_argument("--apply", action="store_true", help="Aplica el cambio (sin esto: dry-run)")
    ap.add_argument("--restore", action="store_true", help="Restaura el backup anterior y sale")
    ap.add_argument("--page", type=int, help="ID de página (defecto: la portada)")
    ap.add_argument("--after", help="ID del widget tras el que insertar (defecto: buscador Houzez)")
    ap.add_argument("--shortcode", default=DEFAULT_SHORTCODE, help="Shortcode a insertar")
    args = ap.parse_args()

    auth = (WP_USER, WP_APP_PASSWORD)
    base = _canonical_base(WP_URL.rstrip("/"))

    page_id = args.page
    if not page_id:
        r = requests.get(f"{base}/wp-json/wp/v2/settings", auth=auth, timeout=30)
        r.raise_for_status()
        page_id = r.json().get("page_on_front")
        if not page_id:
            print("No hay página de portada configurada; usa --page N")
            sys.exit(1)

    ele_url = f"{base}/wp-json/jacobo/v1/elementor/{page_id}"
    r = requests.get(ele_url, auth=auth, timeout=30)
    r.raise_for_status()
    payload = r.json()

    if args.restore:
        backup = payload.get("backup") or ""
        if not backup:
            print("No hay backup guardado para esta página — nada que restaurar.")
            sys.exit(1)
        rr = requests.post(ele_url, auth=auth, json={"data": backup}, timeout=30)
        rr.raise_for_status()
        requests.post(f"{base}/wp-json/wp/v2/pages/{page_id}", auth=auth,
                      json={"status": "publish"}, timeout=30)
        print(f"[OK] Portada restaurada al estado anterior (página {page_id}).")
        return

    raw = payload.get("data") or ""
    if not raw:
        print(f"La página {page_id} no tiene datos de Elementor.")
        sys.exit(1)
    tree = json.loads(raw)

    if _contains_box(tree):
        print("[OK] La cajita ya está colocada en esta página — nada que hacer.")
        return

    if args.after:
        hit = _find(tree, lambda e: e.get("id") == args.after)
        target_desc = f"widget id={args.after}"
    else:
        hit = _find(tree, lambda e: e.get("widgetType") == TARGET_WIDGET)
        target_desc = f"buscador Houzez ({TARGET_WIDGET})"
    if not hit:
        print(f"[FALLO] No encontré el {target_desc} en la página {page_id}.")
        print("        Usa tools/inspect_home.py para ver los IDs y pásame --after <id>.")
        sys.exit(1)

    parent, idx, target = hit
    new_el = {
        "id": "".join(random.choices("0123456789abcdef", k=7)),
        "elType": "widget",
        "widgetType": "shortcode",
        "settings": {"shortcode": args.shortcode},
        "elements": [],
    }

    print(f"Página: {page_id} | Insertar tras: {target.get('widgetType') or target.get('elType')} id={target.get('id')}")
    print(f"Shortcode: {args.shortcode}")

    if not args.apply:
        print("\nDRY-RUN: nada modificado. Añade --apply para insertar (hay backup + --restore).")
        return

    parent.insert(idx + 1, new_el)
    new_data = json.dumps(tree, ensure_ascii=False, separators=(",", ":"))
    rr = requests.post(ele_url, auth=auth, json={"data": new_data}, timeout=30)
    rr.raise_for_status()
    # Re-guardar la página: bump de modified para empujar la invalidación de caché
    requests.post(f"{base}/wp-json/wp/v2/pages/{page_id}", auth=auth,
                  json={"status": "publish"}, timeout=30)

    print(f"\n[OK] Cajita insertada (backup: {rr.json().get('backup_option', '—')}).")
    print(f"Compruébalo saltando la caché: {base}/?nc={int(time.time())}")
    print("Si algo se ve mal: python -m tools.place_search_box --restore")


if __name__ == "__main__":
    main()
