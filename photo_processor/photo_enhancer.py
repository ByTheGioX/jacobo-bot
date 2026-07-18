"""
Orquestador de procesamiento de fotos.
- Selecciona las mejores N fotos (configurable, por defecto 15)
- Descarga fotos de Idealista
- Salta planos y vídeos (los descarga sin modificar)
- Llama a Kie.ai para quitar marca de agua y mejorar cada foto
- Procesa todas las fotos en paralelo (hasta 3 simultáneas)
"""

import hashlib
import logging
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from photo_processor.kie_ai_client import KieAiClient, KieAiNoCreditsError
from photo_processor.photo_classifier import select_best_photos
from config.settings import MAX_PHOTOS_PER_PROPERTY, ENABLE_HOME_STAGING

logger = logging.getLogger(__name__)

PROCESSED_DIR = Path("data/photos")
_MAX_WORKERS = 5

_FLOOR_PLAN_PATTERNS = [
    r"plano",
    r"floor.?plan",
    r"croquis",
    r"blueprint",
]

_VIDEO_EXTENSIONS = (".mp4", ".mov", ".avi", ".webm")

# Mapeo de room_type del clasificador → hint específico de Home Staging.
# Solo interiores: terraza/exterior excluidos (la IA alucina muebles en piscinas/jardines).
_STAGING_ROOM_MAP = {
    "salon": "salon",
    "cocina": "cocina",
    "dormitorio": "dormitorio",
    "bano": "bano",
}


def _sanitize_folder(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*]', '', name)
    name = re.sub(r'\s+', ' ', name).strip()
    return name[:80]


def _url_hash(url: str) -> str:
    """10-char MD5 of the URL — used as a stable cache key in filenames."""
    return hashlib.md5(url.encode()).hexdigest()[:10]


def _is_floor_plan_url(url: str) -> bool:
    lower = url.lower()
    return any(re.search(p, lower) for p in _FLOOR_PLAN_PATTERNS)


def _is_video_url(url: str) -> bool:
    lower = url.lower().split("?")[0]
    return any(lower.endswith(ext) for ext in _VIDEO_EXTENSIONS) or "video" in lower


def _file_ext(url: str) -> str:
    path = url.split("?")[0]
    suffix = Path(path).suffix.lower()
    return suffix if suffix in (".jpg", ".jpeg", ".png", ".webp") else ".jpg"


def _select_photos(
    photo_urls: list[str],
    is_floor_plan_flags: list[bool],
    max_regular: int,
) -> list[tuple[str, bool]]:
    regular: list[tuple[str, bool]] = []
    plans: list[tuple[str, bool]] = []

    for url, is_plan in zip(photo_urls, is_floor_plan_flags):
        if is_plan or _is_floor_plan_url(url) or _is_video_url(url):
            plans.append((url, True))
        else:
            regular.append((url, False))

    selected_regular = regular[:max_regular]
    skipped = len(regular) - len(selected_regular)
    if skipped > 0:
        logger.info("Limitando a %d fotos regulares (%d descartadas, %d planos/videos ignorados)",
                    max_regular, skipped, len(plans))
    elif plans:
        logger.debug("Ignorando %d planos/videos", len(plans))
    return selected_regular


class PhotoEnhancer:
    def __init__(self):
        try:
            self.client = KieAiClient()
        except Exception:
            self.client = None
            logger.info("KIE_AI_API_KEY no configurada — fotos se guardan sin procesar.")

    def process_property_photos(
        self,
        idealista_id: str,
        photo_urls: list[str],
        is_floor_plan_flags: list[bool],
        photo_labels: list[str] = None,
        folder_name: str = None,
        report=None,
    ) -> list[dict]:
        folder = _sanitize_folder(folder_name) if folder_name else idealista_id
        raw_dir = PROCESSED_DIR / folder / "raw"
        processed_dir = PROCESSED_DIR / folder / "processed"
        raw_dir.mkdir(parents=True, exist_ok=True)
        processed_dir.mkdir(parents=True, exist_ok=True)

        selected = select_best_photos(photo_urls, is_floor_plan_flags, photo_labels, MAX_PHOTOS_PER_PROPERTY)
        if not selected:
            logger.warning("Propiedad %s: 0 fotos seleccionadas por el clasificador", idealista_id)
            if report is not None:
                report.note("0 fotos seleccionadas (clasificador)")
            return []

        if report is not None:
            report.photos_selected = len(selected)
            report.photos_dedupe_removed = max(0, len([u for u, f in zip(photo_urls, is_floor_plan_flags) if not f]) - len(selected))
            report.photos_home_staging = sum(
                1 for p in selected
                if p.get("empty") and ENABLE_HOME_STAGING and p.get("room_type") in _STAGING_ROOM_MAP
            )

        # Paso 1: descargar originales — salta si ya existe el archivo cacheado por URL
        raw_paths: dict[int, Optional[str]] = {}
        for idx, photo in enumerate(selected):
            url = photo["url"]
            is_plan = photo["is_floor_plan"]
            ext = _file_ext(url)
            prefix = f"plano_{idx + 1}" if is_plan else f"img_{idx + 1}"
            dest = raw_dir / f"{prefix}_{_url_hash(url)}{ext}"
            if dest.exists():
                logger.debug("  [CACHE RAW] foto %d ya descargada", idx + 1)
                raw_paths[idx] = str(dest)
            else:
                ok = KieAiClient.download_image(url, dest)
                raw_paths[idx] = str(dest) if ok else None

        # Paso 2: enviar a KIE.AI solo las fotos cuyo procesado no está cacheado.
        # Home Staging se activa SOLO cuando la IA detectó habitación vacía (empty=True).
        regular_items: list[tuple[int, dict]] = [
            (idx, photo) for idx, photo in enumerate(selected)
            if not photo["is_floor_plan"]
            and not (processed_dir / f"img_{idx + 1}_{_url_hash(photo['url'])}_enhanced.jpg").exists()
        ]
        enhanced_urls: dict[int, Optional[str]] = {}

        if self.client and regular_items:
            staging_count = sum(
                1 for _, p in regular_items
                if p.get("empty") and ENABLE_HOME_STAGING and p.get("room_type") in _STAGING_ROOM_MAP
            )
            logger.info(
                "Enviando %d fotos a KIE.AI (%d con Home Staging por habitación vacía)...",
                len(regular_items), staging_count,
            )
            if report is not None:
                report.photos_sent_to_kie = len(regular_items)
            with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(regular_items))) as executor:
                futures = {
                    executor.submit(
                        self.client.enhance_photo,
                        photo["url"],
                        home_staging=bool(photo.get("empty")) and ENABLE_HOME_STAGING
                            and photo.get("room_type") in _STAGING_ROOM_MAP,
                        room_type=_STAGING_ROOM_MAP.get(photo.get("room_type")),
                    ): idx
                    for idx, photo in regular_items
                }
                for future in as_completed(futures):
                    idx = futures[future]
                    try:
                        enhanced_urls[idx] = future.result()
                    except KieAiNoCreditsError:
                        raise
                    except Exception as e:
                        logger.error("Error KIE.AI foto %d: %s", idx + 1, e)
                        enhanced_urls[idx] = None

        # Paso 3: descargar mejoradas y armar resultados.
        # POLITICA COPYRIGHT: si CUALQUIER foto regular falla, abortamos la propiedad entera.
        # Cero fallback a raw (Idealista nos puede demandar por subir originales sin procesar).
        results: list[dict] = []
        failed_photos: list[tuple[int, str, str]] = []  # (idx, url, motivo)

        for idx, photo in enumerate(selected):
            url = photo["url"]
            is_plan = photo["is_floor_plan"]
            result: dict = {
                "original_url": url,
                "processed_url": None,
                "raw_path": raw_paths.get(idx),
                "local_path": raw_paths.get(idx),
                "is_floor_plan": is_plan,
                "skipped": is_plan,
                "room_type": photo.get("room_type"),
                "empty": photo.get("empty"),
                "shot": photo.get("shot"),
            }

            if is_plan:
                logger.debug("  [PLAN] foto %d (no se sube)", idx + 1)
                results.append(result)
                continue

            url_key = _url_hash(url)
            proc_dest = processed_dir / f"img_{idx + 1}_{url_key}_enhanced.jpg"

            if proc_dest.exists():
                result["processed_url"] = url
                result["local_path"] = str(proc_dest)
                logger.debug("  [CACHE PROC] foto %d ya mejorada", idx + 1)
            elif idx in enhanced_urls and enhanced_urls[idx]:
                proc_url = enhanced_urls[idx]
                ok = KieAiClient.download_image(proc_url, proc_dest)
                if ok:
                    result["processed_url"] = proc_url
                    result["local_path"] = str(proc_dest)
                    logger.info("  [OK] foto %d mejorada (%s, empty=%s, %s)",
                                idx + 1, photo.get("room_type"), photo.get("empty"), photo.get("shot"))
                else:
                    failed_photos.append((idx, url, "descarga del enhanced falló"))
            else:
                motivo = "KIE devolvió None (sin créditos o error)"
                failed_photos.append((idx, url, motivo))

            results.append(result)

        enhanced = sum(1 for r in results if not r["skipped"] and r["processed_url"])
        plans = sum(1 for r in results if r["skipped"])

        if failed_photos:
            logger.error(
                "Propiedad %s ABORTADA: %d/%d fotos sin procesar. NO se subirá a WP (riesgo copyright).",
                idealista_id, len(failed_photos), len(selected) - plans,
            )
            for idx, url, motivo in failed_photos:
                logger.error("    foto %d sin procesar [%s]: %s", idx + 1, motivo, url[:100])
            if report is not None:
                report.photos_kie_failed = len(failed_photos)
                report.photos_kie_ok = enhanced
                for idx, _url, motivo in failed_photos:
                    report.skip_reason(idx + 1, motivo)
                report.note("Propiedad abortada — riesgo copyright")
            # Devolvemos lista vacía → el publisher no sube nada
            return []

        if report is not None:
            report.photos_kie_ok = enhanced

        logger.info(
            "Propiedad %s: %d mejoradas OK | %d planos | 0 fallback",
            idealista_id, enhanced, plans,
        )
        return results
