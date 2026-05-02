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

from photo_processor.kie_ai_client import KieAiClient
from config.settings import MAX_PHOTOS_PER_PROPERTY, ENABLE_HOME_STAGING

logger = logging.getLogger(__name__)

PROCESSED_DIR = Path("data/photos")
_MAX_WORKERS = 3

_FLOOR_PLAN_PATTERNS = [
    r"plano",
    r"floor.?plan",
    r"croquis",
    r"blueprint",
]

_VIDEO_EXTENSIONS = (".mp4", ".mov", ".avi", ".webm")

_LABEL_TO_ROOM_TYPE = {
    "salon": "salon", "salón": "salon", "living": "salon", "comedor": "salon",
    "cocina": "cocina", "kitchen": "cocina",
    "dormitorio": "dormitorio", "habitacion": "dormitorio", "habitación": "dormitorio",
    "dormitorio principal": "dormitorio_principal", "suite": "dormitorio_principal",
    "baño": "bano", "bano": "bano", "aseo": "bano",
    "terraza": "terraza", "balcón": "terraza", "balcon": "terraza", "patio": "terraza",
    "jardín": "jardin", "jardin": "jardin", "garden": "jardin",
    "garaje": "garaje", "parking": "garaje",
}


def _label_to_room_type(label: str) -> Optional[str]:
    low = label.lower()
    for key, room_type in sorted(_LABEL_TO_ROOM_TYPE.items(), key=lambda x: len(x[0]), reverse=True):
        if key in low:
            return room_type
    return None


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
    ) -> list[dict]:
        folder = _sanitize_folder(folder_name) if folder_name else idealista_id
        raw_dir = PROCESSED_DIR / folder / "raw"
        processed_dir = PROCESSED_DIR / folder / "processed"
        raw_dir.mkdir(parents=True, exist_ok=True)
        processed_dir.mkdir(parents=True, exist_ok=True)

        selected = _select_photos(photo_urls, is_floor_plan_flags, MAX_PHOTOS_PER_PROPERTY)
        url_to_label = dict(zip(photo_urls, photo_labels)) if photo_labels else {}
        if not selected:
            return []

        # Paso 1: descargar originales — salta si ya existe el archivo cacheado por URL
        raw_paths: dict[int, Optional[str]] = {}
        for idx, (url, is_plan) in enumerate(selected):
            ext = _file_ext(url)
            prefix = f"plano_{idx + 1}" if is_plan else f"img_{idx + 1}"
            dest = raw_dir / f"{prefix}_{_url_hash(url)}{ext}"
            if dest.exists():
                logger.debug("  [CACHE RAW] foto %d ya descargada", idx)
                raw_paths[idx] = str(dest)
            else:
                ok = KieAiClient.download_image(url, dest)
                raw_paths[idx] = str(dest) if ok else None

        # Paso 2: enviar a KIE.AI solo las fotos cuyo procesado no está cacheado
        regular_items = [
            (idx, url) for idx, (url, is_plan) in enumerate(selected)
            if not is_plan
            and not (processed_dir / f"img_{idx + 1}_{_url_hash(url)}_enhanced.jpg").exists()
        ]
        enhanced_urls: dict[int, Optional[str]] = {}

        if self.client and regular_items:
            logger.info("Enviando %d fotos a KIE.AI en paralelo...", len(regular_items))
            with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(regular_items))) as executor:
                futures = {
                    executor.submit(
                        self.client.enhance_photo,
                        url,
                        home_staging=ENABLE_HOME_STAGING,
                        room_type=_label_to_room_type(url_to_label.get(url, "")),
                    ): idx
                    for idx, url in regular_items
                }
                for future in as_completed(futures):
                    idx = futures[future]
                    try:
                        enhanced_urls[idx] = future.result()
                    except Exception as e:
                        logger.error("Error KIE.AI foto %d: %s", idx, e)
                        enhanced_urls[idx] = None

        # Paso 3: descargar mejoradas y armar resultados
        results: list[dict] = []
        for idx, (url, is_plan) in enumerate(selected):
            result: dict = {
                "original_url": url,
                "processed_url": None,
                "raw_path": raw_paths.get(idx),
                "local_path": raw_paths.get(idx),
                "is_floor_plan": is_plan,
                "skipped": is_plan,
            }

            if is_plan:
                logger.debug("  [PLAN] %s", raw_paths.get(idx))
            else:
                url_key = _url_hash(url)
                proc_dest = processed_dir / f"img_{idx + 1}_{url_key}_enhanced.jpg"
                if proc_dest.exists():
                    result["processed_url"] = url
                    result["local_path"] = str(proc_dest)
                    logger.debug("  [CACHE PROC] foto %d ya mejorada", idx)
                elif idx in enhanced_urls and enhanced_urls[idx]:
                    proc_url = enhanced_urls[idx]
                    ok = KieAiClient.download_image(proc_url, proc_dest)
                    result["processed_url"] = proc_url
                    result["local_path"] = str(proc_dest) if ok else raw_paths.get(idx)
                    logger.info("  [OK] foto %d mejorada", idx)
                else:
                    logger.warning("  [FALLBACK] foto %d — usando original", idx)

            results.append(result)

        enhanced = sum(1 for r in results if not r["skipped"] and r["processed_url"])
        fallbacks = sum(1 for r in results if not r["skipped"] and not r["processed_url"])
        plans = sum(1 for r in results if r["skipped"])
        logger.info(
            "Propiedad %s: %d mejoradas | %d fallback | %d planos",
            idealista_id, enhanced, fallbacks, plans,
        )
        return results
