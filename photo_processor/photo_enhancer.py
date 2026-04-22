"""
Orquestador de procesamiento de fotos.
- Selecciona las mejores N fotos (configurable, por defecto 15)
- Descarga fotos de Idealista
- Salta planos y vídeos (los descarga sin modificar)
- Llama a Kie.ai para quitar marca de agua y mejorar cada foto
- Devuelve lista de dicts con rutas locales y URLs procesadas
"""

import logging
import re
from pathlib import Path
from typing import Optional

from photo_processor.kie_ai_client import KieAiClient
from config.settings import MAX_PHOTOS_PER_PROPERTY

logger = logging.getLogger(__name__)

PROCESSED_DIR = Path("data/photos")

_FLOOR_PLAN_PATTERNS = [
    r"plano",
    r"floor.?plan",
    r"croquis",
    r"blueprint",
]

_VIDEO_EXTENSIONS = (".mp4", ".mov", ".avi", ".webm")


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
    """
    Separa fotos normales de planos/vídeos.
    Limita las fotos normales a max_regular (Idealista las ordena por calidad).
    Devuelve lista de (url, is_plan_or_video).
    """
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
        logger.info("Limitando a %d fotos (%d descartadas). Planos/vídeos: %d",
                    max_regular, skipped, len(plans))
    return selected_regular + plans


class PhotoEnhancer:
    def __init__(self):
        self.client = KieAiClient()

    def process_property_photos(
        self,
        idealista_id: str,
        photo_urls: list[str],
        is_floor_plan_flags: list[bool],
    ) -> list[dict]:
        """
        Procesa todas las fotos de una propiedad.

        Retorna lista de dicts:
          {
            "original_url": str,
            "processed_url": str | None,
            "local_path": str | None,
            "is_floor_plan": bool,
            "skipped": bool,        # True = plano/vídeo, no se mejoró
          }
        """
        prop_dir = PROCESSED_DIR / idealista_id
        prop_dir.mkdir(parents=True, exist_ok=True)

        # Selección: máx. MAX_PHOTOS fotos normales + todos los planos
        selected = _select_photos(photo_urls, is_floor_plan_flags, MAX_PHOTOS_PER_PROPERTY)

        results: list[dict] = []
        regular_count = 0

        for idx, (url, is_plan) in enumerate(selected):
            result: dict = {
                "original_url": url,
                "processed_url": None,
                "local_path": None,
                "is_floor_plan": is_plan,
                "skipped": False,
            }

            if is_plan:
                # Plano/vídeo: descargar sin modificar
                logger.info("  [PLAN] %s", url)
                result["skipped"] = True
                dest = prop_dir / f"plan_{idx:02d}{_file_ext(url)}"
                self.client.download_image(url, dest)
                result["local_path"] = str(dest)
                results.append(result)
                continue

            regular_count += 1
            logger.info("  [ENHANCE %d/%d] %s", regular_count, len(selected) - len([x for x in selected if x[1]]), url)
            processed_url = self.client.enhance_photo(url)

            if processed_url:
                dest = prop_dir / f"photo_{idx:02d}_enhanced{_file_ext(processed_url)}"
                ok = self.client.download_image(processed_url, dest)
                result["processed_url"] = processed_url
                result["local_path"] = str(dest) if ok else None
            else:
                # Fallback: guardar original sin procesar
                logger.warning("  [FALLBACK] Kie.ai falló en foto %d. Guardando original.", idx)
                dest = prop_dir / f"photo_{idx:02d}_original{_file_ext(url)}"
                ok = self.client.download_image(url, dest)
                result["local_path"] = str(dest) if ok else None

            results.append(result)

        enhanced = sum(1 for r in results if not r["skipped"] and r["processed_url"])
        fallbacks = sum(1 for r in results if not r["skipped"] and not r["processed_url"])
        plans = sum(1 for r in results if r["skipped"])
        logger.info(
            "Propiedad %s: %d mejoradas | %d fallback | %d planos/vídeos",
            idealista_id, enhanced, fallbacks, plans,
        )
        return results
