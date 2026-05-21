"""
Bot de verificación retroactiva.

Para cada propiedad publicada en WordPress, comprueba que TODAS sus fotos están
realmente procesadas por KIE.AI (sin marca de agua de Idealista) — de lo contrario,
hay riesgo de copyright.

Detección en cascada:
  1. pHash local: compara contra `data/photos/<folder>/processed/` (gratis, rápido)
  2. OpenRouter vision: si pHash no matchea, pregunta a la IA si tiene marca de agua

Acción cuando detecta fotos crudas:
  --dry-run (default): solo reporta a CSV
  --reprocess:          baja la foto raw, la pasa por KIE.AI y la reemplaza en WP

Uso:
    python -m tools.verify_uploaded                       # dry-run
    python -m tools.verify_uploaded --reprocess           # reprocesar fotos crudas
    python -m tools.verify_uploaded --reprocess --limit 5 # solo primeras 5 propiedades
"""

import argparse
import csv
import io
import logging
import sys
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests

from wordpress.wp_client import WPClient
from photo_processor.kie_ai_client import KieAiClient
from config.settings import (
    WP_PROPERTY_REST_BASE, OPENROUTER_API_KEY, OPENROUTER_MODEL,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

PROCESSED_DIR = Path("data/photos")
_PHASH_MATCH_THRESHOLD = 10  # hamming distance. Más permisivo que dedup (mismo origen pero recomprimido)
_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

_WATERMARK_CHECK_PROMPT = (
    "Does this real estate photo have ANY watermark, logo, or text overlay on it "
    "(idealista, agency name, website URL, copyright text, etc)? "
    "Respond with ONLY one word: 'yes' or 'no'."
)


def _phash_of_url(url: str) -> Optional[str]:
    try:
        import imagehash
        from PIL import Image
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        return str(imagehash.phash(Image.open(io.BytesIO(resp.content))))
    except Exception as e:
        logger.debug("pHash fallido %s: %s", url[:80], e)
        return None


def _phash_of_file(path: Path) -> Optional[str]:
    try:
        import imagehash
        from PIL import Image
        return str(imagehash.phash(Image.open(path)))
    except Exception as e:
        logger.debug("pHash file fallido %s: %s", path, e)
        return None


def _hamming(h1: str, h2: str) -> int:
    try:
        import imagehash
        return imagehash.hex_to_hash(h1) - imagehash.hex_to_hash(h2)
    except Exception:
        return 64


def _build_processed_index() -> dict[str, str]:
    """Indexa todos los pHash de archivos en processed/. Devuelve {phash: filepath}."""
    index: dict[str, str] = {}
    if not PROCESSED_DIR.exists():
        return index
    for proc_file in PROCESSED_DIR.rglob("processed/*_enhanced.jpg"):
        h = _phash_of_file(proc_file)
        if h:
            index[h] = str(proc_file)
    logger.info("Índice processed/: %d archivos indexados", len(index))
    return index


def _matches_processed_cache(url_phash: str, index: dict[str, str]) -> bool:
    """True si url_phash matchea con alguna foto del cache local processed/."""
    if url_phash is None:
        return False
    return any(_hamming(url_phash, h) <= _PHASH_MATCH_THRESHOLD for h in index)


def _has_watermark_ai(image_url: str) -> Optional[bool]:
    """Pregunta a OpenRouter si la imagen tiene marca de agua. None si no se puede determinar."""
    if not OPENROUTER_API_KEY:
        return None
    try:
        resp = requests.post(
            _OPENROUTER_URL,
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": OPENROUTER_MODEL,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": image_url}},
                        {"type": "text", "text": _WATERMARK_CHECK_PROMPT},
                    ],
                }],
                "max_tokens": 5,
            },
            timeout=30,
        )
        resp.raise_for_status()
        ans = resp.json()["choices"][0]["message"]["content"].strip().lower()
        return "yes" in ans
    except Exception as e:
        logger.warning("Watermark check IA falló: %s", e)
        return None


def _fetch_property_with_media(wp: WPClient) -> list[dict]:
    """Obtiene todas las propiedades + sus media URLs."""
    page = 1
    props = []
    while True:
        try:
            batch = wp._get(WP_PROPERTY_REST_BASE, {
                "per_page": 100, "page": page, "status": "publish",
                "_fields": "id,title,link,featured_media,meta",
            })
        except Exception:
            break
        if not batch:
            break
        props.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return props


def _fetch_media_urls_for_post(wp: WPClient, post_id: int) -> list[dict]:
    """Devuelve [{id, source_url}, ...] para todas las fotos de un post."""
    try:
        media = wp._get("media", {"parent": post_id, "per_page": 100, "_fields": "id,source_url"})
        return media or []
    except Exception as e:
        logger.debug("media de post %d falló: %s", post_id, e)
        return []


def _reprocess_photo(wp: WPClient, kie: KieAiClient, original_url: str, media_id: int, post_id: int) -> bool:
    """Procesa una foto cruda con KIE y reemplaza la versión en WP."""
    try:
        # Pasamos la URL original a KIE — devuelve URL de la versión limpia
        enhanced_url = kie.enhance_photo(original_url, home_staging=False)
        if not enhanced_url:
            logger.error("KIE no devolvió enhanced para %s", original_url[:80])
            return False
        # Descargar la versión limpia
        tmp = Path(f"data/tmp_reprocess_{media_id}.jpg")
        ok = KieAiClient.download_image(enhanced_url, tmp)
        if not ok:
            return False
        # Subir como nueva media y adjuntar al post (reemplazar es complicado en WP REST)
        new_media = wp.upload_media(str(tmp), title=f"Reprocesada {media_id}", post_id=post_id)
        tmp.unlink(missing_ok=True)
        if new_media:
            # Borrar la media cruda
            try:
                wp._delete(f"media/{media_id}")
            except Exception as e:
                logger.warning("No pude borrar media cruda %d: %s", media_id, e)
            logger.info("Foto media %d → reemplazada por media %d", media_id, new_media["id"])
            return True
    except Exception as e:
        logger.error("Reprocesamiento de media %d falló: %s", media_id, e)
    return False


def main():
    parser = argparse.ArgumentParser(description="Verificación retroactiva de fotos en WP")
    parser.add_argument("--reprocess", action="store_true",
                        help="Reprocesar y reemplazar fotos crudas detectadas (necesita tokens KIE)")
    parser.add_argument("--limit", type=int, default=0, help="Limitar a primeras N propiedades (default: todas, usa un número para limitar)")
    parser.add_argument("--csv", default="data/verification_report.csv", help="Ruta del CSV de salida")
    parser.add_argument("--skip-ai", action="store_true",
                        help="Omitir paso 2 (vision IA) — solo pHash local")
    args = parser.parse_args()

    Path(args.csv).parent.mkdir(parents=True, exist_ok=True)

    wp = WPClient()
    kie = None
    if args.reprocess:
        try:
            kie = KieAiClient()
        except Exception as e:
            logger.error("--reprocess pedido pero KIE_AI_API_KEY no configurada: %s", e)
            sys.exit(1)

    logger.info("Construyendo índice pHash de fotos procesadas...")
    index = _build_processed_index()

    logger.info("Obteniendo propiedades de WP...")
    props = _fetch_property_with_media(wp)
    if args.limit:
        props = props[:args.limit]
        logger.info("Limitando a %d propiedades (usa --limit 0 para todas).", args.limit)
    logger.info("Verificando %d propiedades...", len(props))

    rows: list[dict] = []
    flagged_count = 0
    reprocessed_count = 0

    for i, prop in enumerate(props, 1):
        post_id = prop["id"]
        title = (prop.get("title", {}).get("rendered") or "")[:60]
        media_list = _fetch_media_urls_for_post(wp, post_id)

        raw_photos: list[dict] = []
        for m in media_list:
            url = m.get("source_url")
            if not url:
                continue

            phash = _phash_of_url(url)
            matched_cache = _matches_processed_cache(phash, index)

            has_wm = None
            if not matched_cache and not args.skip_ai:
                has_wm = _has_watermark_ai(url)

            # Es cruda si: no matchea cache local Y la IA detecta marca de agua
            # (o si la IA no responde, asumimos sospechoso pero no certero)
            is_raw = (not matched_cache) and (has_wm is True)

            if is_raw:
                raw_photos.append({"media_id": m["id"], "url": url, "phash": phash})

        ok = len(raw_photos) == 0
        row = {
            "post_id": post_id,
            "title": title,
            "link": prop.get("link", ""),
            "total_photos": len(media_list),
            "raw_photos_detected": len(raw_photos),
            "raw_media_ids": ",".join(str(p["media_id"]) for p in raw_photos),
            "ok": ok,
            "reprocessed": 0,
        }

        if not ok:
            flagged_count += 1
            logger.warning("[FLAG] post %d (%s): %d fotos crudas", post_id, title[:40], len(raw_photos))
            if args.reprocess and kie:
                for rp in raw_photos:
                    if _reprocess_photo(wp, kie, rp["url"], rp["media_id"], post_id):
                        row["reprocessed"] += 1
                        reprocessed_count += 1

        rows.append(row)
        if i % 5 == 0:
            logger.info("  %d/%d propiedades verificadas...", i, len(props))
        time.sleep(8)  # delay generoso para no saturar el servidor (plan CDmon compartido)

    # CSV
    if rows:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

    # Resumen
    print(f"\n=== VERIFICACIÓN RETROACTIVA ===")
    print(f"Propiedades verificadas:     {len(rows)}")
    print(f"Propiedades con fotos crudas:{flagged_count}")
    print(f"Fotos crudas detectadas:     {sum(r['raw_photos_detected'] for r in rows)}")
    if args.reprocess:
        print(f"Fotos reprocesadas:          {reprocessed_count}")
    print(f"\nCSV detallado: {args.csv}")


if __name__ == "__main__":
    main()
