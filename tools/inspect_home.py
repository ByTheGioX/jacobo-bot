"""
Imprime un resumen del árbol Elementor de la portada (o de la página --page N).

Sirve para localizar dónde está el buscador del hero antes de insertar la
cajita [jacobo_search_box] con tools/place_search_box.py. Solo LEE, no cambia nada.

Uso (en el VPS):
    python -m tools.inspect_home
    python -m tools.inspect_home --page 44402
"""

import argparse
import json
import sys
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


def _walk(elems, depth=0):
    for e in elems or []:
        kind = e.get("elType", "?")
        widget = e.get("widgetType") or ""
        s = e.get("settings") or {}
        extra = ""
        for key in ("title", "shortcode", "editor", "html", "text"):
            val = s.get(key)
            if val and isinstance(val, str):
                extra = " | " + " ".join(val.split())[:70]
                break
        label = f"{kind}/{widget}" if widget else kind
        print("  " * depth + f"- {label}  id={e.get('id')}{extra}")
        _walk(e.get("elements"), depth + 1)


def main():
    ap = argparse.ArgumentParser(description="Resumen del layout Elementor de la portada")
    ap.add_argument("--page", type=int, help="ID de página (defecto: la portada)")
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

    r = requests.get(f"{base}/wp-json/jacobo/v1/elementor/{page_id}", auth=auth, timeout=30)
    r.raise_for_status()
    raw = r.json().get("data") or ""
    if not raw:
        print(f"La página {page_id} no tiene datos de Elementor (¿no está hecha con Elementor?)")
        sys.exit(1)

    tree = json.loads(raw)
    print(f"=== Elementor de la página {page_id} ({base}) ===")
    _walk(tree)
    print("=== fin ===")


if __name__ == "__main__":
    main()
