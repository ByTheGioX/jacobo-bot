"""
Comprueba si el bot sigue pudiendo entrar en WordPress y cuenta las propiedades
publicadas en la web (3-4 peticiones, sin escribir nada).

Tras la limpieza del malware es fácil que el usuario del bot o su Application
Password se hayan borrado/revocado, o que Wordfence desactive las Application
Passwords: entonces el bot no puede publicar y las propiedades se quedan
"pendientes". Esto lo detecta y dice el motivo que da WordPress.

Uso:
    python -m tools.check_wp_access
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

import requests
from requests.auth import HTTPBasicAuth

from config.settings import WP_URL, WP_USER, WP_APP_PASSWORD, WP_PROPERTY_REST_BASE

TIMEOUT = 30


def _wp_error(resp: requests.Response) -> str:
    try:
        body = resp.json()
        return f"{body.get('code', '?')}: {body.get('message', '')}"
    except ValueError:
        return resp.text[:200]


def _count(base: str, status: str, auth=None):
    """Devuelve (total, None) o (None, motivo) usando la cabecera X-WP-Total."""
    resp = requests.get(
        f"{base}/{WP_PROPERTY_REST_BASE}",
        params={"per_page": 1, "status": status, "_fields": "id"},
        auth=auth, timeout=TIMEOUT,
    )
    if resp.status_code != 200:
        return None, f"HTTP {resp.status_code} — {_wp_error(resp)}"
    return int(resp.headers.get("X-WP-Total", "0")), None


def main():
    base = f"{WP_URL.replace('http://', 'https://').rstrip('/')}/wp-json/wp/v2"
    auth = HTTPBasicAuth(WP_USER, WP_APP_PASSWORD)
    print(f"\nWordPress: {base}")
    print(f"Usuario del bot (WP_USER): {WP_USER or '(vacío)'}\n")

    total, err = _count(base, "publish")
    print(f"[1] Propiedades publicadas en la web (público): {total if err is None else err}")

    resp = requests.get(f"{base}/users/me", params={"context": "edit", "_fields": "id,name,roles"},
                        auth=auth, timeout=TIMEOUT)
    if resp.status_code == 200:
        me = resp.json()
        print(f"[2] Login del bot: OK — id={me.get('id')} nombre={me.get('name')} roles={me.get('roles')}")
    else:
        print(f"[2] Login del bot: FALLA — HTTP {resp.status_code} — {_wp_error(resp)}")
        print("    → El bot NO puede publicar. Causas típicas tras limpiar malware:")
        print("      - el usuario del bot fue borrado o le quitaron el rol")
        print("      - su Application Password fue revocada (hay que crear una nueva)")
        print("      - Wordfence/seguridad desactivó las Application Passwords")
        print()
        return

    for status in ("draft", "pending", "private"):
        n, err = _count(base, status, auth)
        print(f"[3] Propiedades en estado '{status}': {n if err is None else err}")
    print()


if __name__ == "__main__":
    main()
