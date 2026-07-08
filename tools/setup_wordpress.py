"""
Configura el plugin Jacobo Agency Manager en WordPress desde el propio VPS.

Hace por API (con las credenciales WP del .env):
  1. Fija la URL pública del bot en el plugin (jacobo_api_url).
  2. Fija el API secret del plugin (ruta de solo-escritura jacobo/v1/secret).
  3. Verifica la versión del plugin instalada.
  4. Verifica que la página /unete/ renderiza el formulario de alta.

Uso (en el VPS, con el bot corriendo en otra ventana):
    python -m tools.setup_wordpress
    python -m tools.setup_wordpress --public-url http://1.2.3.4:8080   # forzar URL

La URL pública por defecto se autodetecta: http://<IP-publica-del-VPS>:<FLASK_PORT>.
"""

import argparse
import re
import shutil
import sys
from pathlib import Path
from urllib.parse import urlparse

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

from config.settings import WP_URL, WP_USER, WP_APP_PASSWORD, FLASK_SECRET, FLASK_PORT

OK, FAIL = "[OK]", "[FALLO]"

_ROOT = Path(__file__).resolve().parent.parent
_PLUGIN_DIR = _ROOT / "wp-plugin" / "jacobo-agency-manager"


def _vtuple(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v or "")[:3])


def _local_plugin_version() -> str:
    m = re.search(
        r"Version:\s*([\d.]+)",
        (_PLUGIN_DIR / "jacobo-agency-manager.php").read_text(encoding="utf-8"),
    )
    return m.group(1) if m else "0"


def _make_plugin_zip(version: str) -> Path:
    """Crea el zip del plugin desde la carpeta ACTUAL del repo (imposible subir uno viejo)."""
    out = _ROOT / f"jacobo-agency-manager-v{version}"
    path = shutil.make_archive(
        str(out), "zip",
        root_dir=str(_PLUGIN_DIR.parent),
        base_dir="jacobo-agency-manager",
    )
    return Path(path)


def _canonical_base(url: str) -> str:
    """Sigue la redirección (ej. sin-www → www) y devuelve la base canónica.
    Si se llama con el host no canónico, requests pierde el Authorization al
    redirigir y TODO da 401. Por eso se resuelve primero."""
    try:
        r = requests.get(f"{url}/wp-json/", timeout=30, allow_redirects=True,
                         headers={"User-Agent": "Mozilla/5.0"})
        p = urlparse(r.url)
        if p.scheme and p.netloc:
            return f"{p.scheme}://{p.netloc}"
    except Exception:
        pass
    return url


def main():
    ap = argparse.ArgumentParser(description="Configura el plugin de WordPress desde el VPS")
    ap.add_argument("--public-url", help="URL pública del bot (defecto: autodetectar IP)")
    args = ap.parse_args()

    problems = []
    auth = (WP_USER, WP_APP_PASSWORD)

    if not (WP_URL and WP_USER and WP_APP_PASSWORD):
        print(f"{FAIL} Faltan WP_URL / WP_USER / WP_APP_PASSWORD en el .env")
        sys.exit(1)
    if not FLASK_SECRET:
        print(f"{FAIL} Falta FLASK_SECRET en el .env")
        sys.exit(1)

    # Zip del plugin SIEMPRE desde la carpeta actual (así nunca se sube uno viejo)
    local_ver = _local_plugin_version()
    zip_path = _make_plugin_zip(local_ver)
    print(f"Zip del plugin listo (v{local_ver}): {zip_path}")

    # Host canónico (evita el 401 por redirección sin-www → www)
    base = _canonical_base(WP_URL.rstrip("/"))
    print(f"WordPress: {base}")

    # URL pública del bot
    public_url = args.public_url
    if not public_url:
        try:
            ip = requests.get("https://ifconfig.me/ip", timeout=15).text.strip()
            public_url = f"http://{ip}:{FLASK_PORT}"
        except Exception as e:
            print(f"{FAIL} No pude autodetectar la IP pública ({e}). Usa --public-url")
            sys.exit(1)
    print(f"URL pública del bot: {public_url}")

    # 1) Version del plugin instalada en WP
    try:
        r = requests.get(f"{base}/wp-json/wp/v2/plugins", auth=auth, timeout=30)
        r.raise_for_status()
        ver = next(
            (p.get("version") for p in r.json() if "jacobo" in (p.get("name") or "").lower()),
            None,
        )
        if ver and _vtuple(ver) >= _vtuple(local_ver):
            print(f"{OK} Plugin instalado: v{ver}")
        elif ver:
            print(f"{FAIL} Plugin instalado v{ver} pero el actual es v{local_ver} → sube el zip de arriba (Reemplazar)")
            problems.append(f"subir {zip_path.name} en WP: Plugins → Añadir nuevo → Subir → Reemplazar")
        else:
            print(f"{FAIL} Plugin Jacobo no encontrado en WordPress → sube el zip de arriba")
            problems.append(f"instalar {zip_path.name}")
    except Exception as e:
        print(f"{FAIL} No pude listar plugins: {e}")
        problems.append(f"listar plugins: {e}")

    # 2) URL del bot en el plugin
    try:
        r = requests.post(
            f"{base}/wp-json/wp/v2/settings",
            auth=auth, json={"jacobo_api_url": public_url}, timeout=30,
        )
        r.raise_for_status()
        saved = r.json().get("jacobo_api_url", "")
        if saved.rstrip("/") == public_url.rstrip("/"):
            print(f"{OK} jacobo_api_url = {saved}")
        else:
            print(f"{FAIL} jacobo_api_url no quedó bien (guardado: '{saved}')")
            problems.append("jacobo_api_url no persiste (¿plugin < 1.1.1?)")
    except Exception as e:
        print(f"{FAIL} No pude fijar jacobo_api_url: {e}")
        problems.append(f"jacobo_api_url: {e}")

    # 3) Secret del plugin (solo-escritura)
    try:
        r = requests.post(
            f"{base}/wp-json/jacobo/v1/secret",
            auth=auth, json={"secret": FLASK_SECRET}, timeout=30,
        )
        if r.status_code == 200 and r.json().get("updated"):
            print(f"{OK} API secret configurado en el plugin")
        else:
            print(f"{FAIL} Secret: HTTP {r.status_code} — {r.text[:120]}")
            problems.append("no pude fijar el secret (¿plugin < 1.1.2?)")
    except Exception as e:
        print(f"{FAIL} No pude fijar el secret: {e}")
        problems.append(f"secret: {e}")

    # 4) La página /unete/ renderiza el formulario
    try:
        r = requests.get(f"{base}/unete/", timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        if "jacobo_onboard_submit" in r.text:
            print(f"{OK} /unete/ muestra el formulario de alta")
        elif "jacobo_onboarding_form" in r.text:
            print(f"{FAIL} /unete/ muestra el shortcode como texto → plugin viejo o inactivo")
            problems.append("/unete/ no renderiza (plugin viejo/inactivo)")
        else:
            print(f"{FAIL} /unete/ responde HTTP {r.status_code} sin formulario")
            problems.append(f"/unete/ HTTP {r.status_code}")
    except Exception as e:
        print(f"{FAIL} No pude cargar /unete/: {e}")
        problems.append(f"/unete/: {e}")

    print()
    if problems:
        print("PENDIENTE:")
        for p in problems:
            print(f"  - {p}")
        sys.exit(1)
    print("=== WORDPRESS CONFIGURADO — todo conectado ===")


if __name__ == "__main__":
    main()
