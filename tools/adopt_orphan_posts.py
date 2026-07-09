"""
Re-vincula la BD local con posts de WP "huérfanos" (publicados pero no trackeados).

Caso de uso (incidente #16): la BD perdió el wp_post_id de algunas propiedades
(borrado masivo, restore parcial, etc.) y sus posts siguen publicados en WP.
Si no se re-vinculan, el bot las re-publica como posts NUEVOS en el próximo
ciclo → duplicados en la web.

Qué hace:
  1. Lee todos los posts de propiedad en publish y draft de WP.
  2. Para los NO trackeados en la BD, lee fave_property_id vía XML-RPC y
     extrae el ID de Idealista (sufijo numérico tras el último '-').
  3. Si una propiedad ACTIVA de la BD no tiene wp_post_id válido (NULL o
     apunta a un post que ya no existe) y hay un post publish huérfano con su
     ID de Idealista → adopta ese post (UPDATE wp_post_id).
  4. Reporta huérfanos sin propiedad activa correspondiente (candidatos a
     despublicar con tools.draft_stale_posts).

EJECUTAR EN EL VPS (donde vive la BD real del bot), idealmente antes del
próximo ciclo del monitor.

Uso:
    python -m tools.adopt_orphan_posts            # dry-run
    python -m tools.adopt_orphan_posts --apply    # aplica los cambios en la BD

PROTOCOLO CDmon: 1 lectura XML-RPC por post huérfano (sleep 8s). No escribe
nada en WP — solo lee; las escrituras son en la BD local (SQLite).
"""

import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database.db import Database
from wordpress.wp_client import WPClient, _RequestsTransport
from config.settings import WP_PROPERTY_REST_BASE, WP_URL, WP_USER, WP_APP_PASSWORD

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def _fetch_posts(wp: WPClient, status: str, retries: int = 3, wait: float = 60.0) -> list[dict]:
    """Lista los posts de un status. Si WP no responde en la PRIMERA página
    (servidor saturado), reintenta con pausa en vez de devolver [] en silencio —
    un [] falso haría creer al llamador que la web está vacía."""
    out, page = [], 1
    while page <= 20:
        try:
            batch = wp._get(WP_PROPERTY_REST_BASE, {
                "per_page": 100, "page": page, "status": status,
                "_fields": "id,title,status",
            })
        except Exception as e:
            if page == 1 and retries > 0:
                logger.warning(
                    "WP no responde listando '%s' (%s). Servidor probablemente "
                    "saturado — reintento en %.0fs (%d restantes)...",
                    status, e, wait, retries,
                )
                time.sleep(wait)
                retries -= 1
                continue
            if page == 1:
                raise RuntimeError(f"WP no respondió listando posts '{status}': {e}")
            logger.debug("Fin del paginado (%s) en página %d: %s", status, page, e)
            break
        if not batch:
            break
        out.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return out


def _get_fave_id(post_id: int) -> str:
    import xmlrpc.client
    proxy = xmlrpc.client.ServerProxy(f"{WP_URL}/xmlrpc.php", transport=_RequestsTransport())
    try:
        post = proxy.wp.getPost(1, WP_USER, WP_APP_PASSWORD, post_id, ["custom_fields"])
        for cf in post.get("custom_fields", []):
            if cf["key"] == "fave_property_id":
                return cf["value"] or ""
    except Exception as e:
        logger.warning("XML-RPC getPost %d falló: %s", post_id, e)
    return ""


def _idealista_id(fave_id: str) -> str:
    """'3VCO-110814373' / 'idealista-110814373' → '110814373'."""
    if "-" not in fave_id:
        return ""
    suffix = fave_id.rsplit("-", 1)[1]
    return suffix if suffix.isdigit() else ""


def main():
    ap = argparse.ArgumentParser(description="Adopta posts huérfanos de WP en la BD local")
    ap.add_argument("--apply", action="store_true", help="Aplica los cambios en la BD (sin esto: dry-run)")
    ap.add_argument("--sleep-read", type=float, default=8.0, help="Segundos entre lecturas XML-RPC (def: 8)")
    ap.add_argument("--cached", action="store_true",
                    help="Reutiliza el último escaneo (data/adopt_scan.json, <24h) sin volver a preguntar a WP")
    args = ap.parse_args()

    cache_path = Path("data/adopt_scan.json")
    db = Database()

    with db._conn() as conn:
        rows = conn.execute(
            "SELECT idealista_id, wp_post_id, status FROM properties"
        ).fetchall()
    props = {str(r[0]): {"wp_post_id": r[1], "status": r[2]} for r in rows}
    tracked = {int(p["wp_post_id"]) for p in props.values() if p["wp_post_id"]}

    if args.cached:
        if not cache_path.exists():
            logger.error("[ABORTADO] No existe %s — corre primero sin --cached.", cache_path)
            sys.exit(1)
        cache = json.loads(cache_path.read_text(encoding="utf-8"))
        age_h = (time.time() - cache.get("ts", 0)) / 3600
        if age_h > 24:
            logger.error("[ABORTADO] El escaneo cacheado tiene %.1f h (>24h) — corre sin --cached.", age_h)
            sys.exit(1)
        orphan_map = {str(k): int(v) for k, v in cache["orphan_map"].items()}
        existing_ids = set(cache["existing_ids"])
        logger.info("Usando escaneo cacheado de hace %.1f h: %d huérfanos mapeados.", age_h, len(orphan_map))
    else:
        wp = WPClient()  # solo en modo escaneo — con --cached no se toca WP en absoluto
        try:
            published = _fetch_posts(wp, "publish")
            drafts = _fetch_posts(wp, "draft", retries=1)
        except RuntimeError as e:
            logger.error("[ABORTADO] %s — no se toca nada. Espera 10 min y reintenta.", e)
            sys.exit(1)
        if not published:
            logger.error(
                "[ABORTADO] WP devolvió 0 posts publicados. O el servidor está saturado "
                "o algo va mal — con 0 posts esta herramienta no tiene nada que hacer. "
                "No se toca nada. Espera 10 min y reintenta."
            )
            sys.exit(1)
        existing_ids = {p["id"] for p in published} | {p["id"] for p in drafts}
        logger.info("WP: %d publish, %d draft. BD: %d propiedades (%d con wp_post_id).",
                    len(published), len(drafts), len(props), len(tracked))

        orphans = [p for p in published if p["id"] not in tracked]
        logger.info("Posts publish huérfanos (no trackeados): %d", len(orphans))

        # Mapear huérfanos → ID de Idealista
        orphan_map = {}
        for i, p in enumerate(orphans, 1):
            fave = _get_fave_id(p["id"])
            iid = _idealista_id(fave)
            title = (p.get("title", {}).get("rendered") or "")[:55]
            if iid:
                orphan_map.setdefault(iid, p["id"])
                logger.info("  huérfano %d (%s) → idealista %s", p["id"], title, iid)
            else:
                logger.info("  huérfano %d (%s) → sin fave_property_id, se ignora", p["id"], title)
            if i < len(orphans):
                time.sleep(args.sleep_read)

        cache_path.parent.mkdir(exist_ok=True)
        cache_path.write_text(json.dumps({
            "ts": time.time(),
            "orphan_map": orphan_map,
            "existing_ids": sorted(existing_ids),
        }), encoding="utf-8")
        logger.info("Escaneo guardado en %s — para re-aplicar sin re-escanear: --apply --cached", cache_path)

    # Decidir adopciones. Se consideran TODAS las filas (también 'removed'/'paused'):
    # un post PUBLICADO en WP con el mismo ID de Idealista es evidencia suficiente
    # de que la propiedad está viva — p.ej. tras el incidente de borrados+restore
    # la BD quedó entera en 'removed' apuntando a posts 47xxx ya borrados,
    # mientras los posts vivos eran la serie 46xxx.
    adoptions: list[tuple[str, int]] = []  # (idealista_id, post_id)
    leftovers: dict[str, int] = dict(orphan_map)
    for iid, info in props.items():
        has_valid_post = info["wp_post_id"] and int(info["wp_post_id"]) in existing_ids
        if has_valid_post:
            continue
        if iid in orphan_map:
            adoptions.append((iid, orphan_map[iid]))
            leftovers.pop(iid, None)

    logger.info("=== Adopciones propuestas: %d ===", len(adoptions))
    for iid, pid in adoptions:
        logger.info("  propiedad %s ← post WP %d", iid, pid)
    if leftovers:
        logger.info("=== Huérfanos SIN propiedad activa en BD: %d (candidatos a draft_stale_posts) ===", len(leftovers))
        for iid, pid in leftovers.items():
            logger.info("  post WP %d (idealista %s)", pid, iid)

    if not args.apply:
        logger.info("DRY-RUN. Nada modificado. Añade --apply para escribir en la BD.")
        return

    changed = 0
    with db._conn() as conn:
        for iid, pid in adoptions:
            cur = conn.execute(
                "UPDATE properties SET wp_post_id = ?, status = 'active' WHERE idealista_id = ?",
                (pid, iid),
            )
            changed += cur.rowcount or 0
    logger.info("=== BD actualizada: %d propiedades re-vinculadas ===", changed)


if __name__ == "__main__":
    main()
