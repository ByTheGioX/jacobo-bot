"""
Re-vincula una propiedad de la BD con un post que YA existe en WordPress.

Caso: el wp_post_id de la BD apunta a un post borrado, pero la propiedad sigue
publicada en WP bajo otro post (huérfano de una era anterior). Republicarla
crearía un duplicado; lo correcto es apuntar la BD al post bueno para que el bot
lo actualice en vez de crear una copia.

Antes de tocar nada comprueba que el fave_property_id del post contiene el ID de
Idealista indicado — si no coincide, no hace nada (protege de vincular la
propiedad equivocada).

Uso:
    python -m tools.relink_post 110782952 45662           # dry-run: solo comprueba
    python -m tools.relink_post 110782952 45662 --apply   # aplica
"""

import argparse
import logging
import sys
import xmlrpc.client
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.db import Database
from wordpress.wp_client import WPClient, _RequestsTransport
from config.settings import WP_PROPERTY_REST_BASE, WP_URL, WP_USER, WP_APP_PASSWORD

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def _fave_property_id(post_id: int) -> str:
    proxy = xmlrpc.client.ServerProxy(f"{WP_URL}/xmlrpc.php", transport=_RequestsTransport())
    try:
        post = proxy.wp.getPost(1, WP_USER, WP_APP_PASSWORD, post_id, ["custom_fields"])
        for cf in post.get("custom_fields", []):
            if cf["key"] == "fave_property_id":
                return cf["value"]
    except Exception as e:
        logger.error("No se pudo leer el post %s: %s", post_id, e)
    return ""


def main():
    ap = argparse.ArgumentParser(description="Re-vincula una propiedad con un post existente de WP")
    ap.add_argument("idealista_id", help="ID de Idealista de la propiedad en la BD")
    ap.add_argument("wp_post_id", type=int, help="ID del post de WordPress al que vincularla")
    ap.add_argument("--apply", action="store_true", help="Aplica el cambio (sin esto: dry-run)")
    args = ap.parse_args()

    db = Database()
    row = db.get_property(args.idealista_id)
    if not row:
        logger.error("La propiedad %s no está en la base de datos.", args.idealista_id)
        sys.exit(1)

    print()
    print(f"  Propiedad     : {args.idealista_id} — {(row.get('title') or '')[:50]}")
    print(f"  Puntero actual: {row.get('wp_post_id')}  (el post borrado)")
    print(f"  Nuevo puntero : {args.wp_post_id}")

    # Verificación: el post destino debe ser REALMENTE esta propiedad
    prop_id = _fave_property_id(args.wp_post_id)
    print(f"  ID interno del post {args.wp_post_id}: {prop_id or '(sin meta)'}")
    if args.idealista_id not in str(prop_id):
        print()
        print(f"  ABORTADO: el post {args.wp_post_id} NO corresponde a {args.idealista_id}.")
        print("  Vincularlos mezclaría dos propiedades distintas.")
        print()
        sys.exit(1)
    print("  Verificado: el post corresponde a esta propiedad.")
    print()

    if not args.apply:
        print("  DRY-RUN. Nada modificado. Añade --apply para vincular.")
        print()
        return

    db.set_wp_post_id(args.idealista_id, args.wp_post_id)
    db.mark_active(args.idealista_id)
    print(f"  OK — {args.idealista_id} vinculada al post {args.wp_post_id}.")
    print("  El bot ya la gestiona: la actualizará en vez de crear un duplicado.")
    print()


if __name__ == "__main__":
    main()
