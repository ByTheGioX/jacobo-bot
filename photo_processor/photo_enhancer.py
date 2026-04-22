"""
Orquestador de procesamiento de fotos.
- Descarga fotos de Idealista
- Decide si es plano/vídeo (los salta)
- Llama a Kie.ai para quitar marca de agua y mejorar
- Devuelve rutas locales de las fotos procesadas
"""

import logging
import re
from pathlib import Path
from typing import Optional

from photo_processor.kie_ai_client import KieAiClient

logger = logging.getLogger(__name__)

PROCESSED_DIR = Path("data/photos")

# Patrones que identifican imágenes de planos
FLOOR_PLAN_PATTERNS = [
    r"plano",
    r"floor.?plan",
    r"croquis",
    r"blueprint",
]


def _is_floor_plan_url(url: str) -> bool:
    lower = url.lower()
    return any(re.search(p, lower) for p in FLOOR_PLAN_PATTERNS)


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
            "skipped": bool,
          }
        """
        results = []
        prop_dir = PROCESSED_DIR / idealista_id
        prop_dir.mkdir(parents=True, exist_ok=True)

        for idx, (url, is_plan) in enumerate(zip(photo_urls, is_floor_plan_flags)):
            result = {
                "original_url": url,
                "processed_url": None,
                "local_path": None,
                "is_floor_plan": is_plan,
                "skipped": False,
            }

            # Saltar planos y vídeos
            if is_plan or _is_floor_plan_url(url) or self._is_video(url):
                logger.info(f"  [SKIP] Plano/vídeo: {url}")
                result["skipped"] = True
                # Descargar igual el plano sin modificar
                dest = prop_dir / f"plan_{idx:02d}{self._ext(url)}"
                self.client.download_image(url, dest)
                result["local_path"] = str(dest)
                results.append(result)
                continue

            logger.info(f"  [ENHANCE] Foto {idx}: {url}")
            processed_url = self.client.enhance_property_photo(url)

            if processed_url:
                dest = prop_dir / f"photo_{idx:02d}_enhanced{self._ext(processed_url)}"
                ok = self.client.download_image(processed_url, dest)
                result["processed_url"] = processed_url
                result["local_path"] = str(dest) if ok else None
            else:
                # Fallback: descargar original sin procesar
                logger.warning(f"  [FALLBACK] No se pudo procesar foto {idx}. Usando original.")
                dest = prop_dir / f"photo_{idx:02d}_original{self._ext(url)}"
                ok = self.client.download_image(url, dest)
                result["local_path"] = str(dest) if ok else None

            results.append(result)

        logger.info(
            f"Propiedad {idealista_id}: {len([r for r in results if not r['skipped']])} fotos procesadas, "
            f"{len([r for r in results if r['skipped']])} planos/vídeos sin tocar."
        )
        return results

    @staticmethod
    def _is_video(url: str) -> bool:
        return any(ext in url.lower() for ext in (".mp4", ".mov", ".avi", ".webm", "video"))

    @staticmethod
    def _ext(url: str) -> str:
        path = url.split("?")[0]
        suffix = Path(path).suffix
        return suffix if suffix in (".jpg", ".jpeg", ".png", ".webp") else ".jpg"
