"""
Cliente para la API de Kie.ai.
Soporta: image-to-image (Seedream), watermark removal, home staging.
"""

import logging
import time
from pathlib import Path
from typing import Optional

import requests

from config.settings import KIE_AI_API_KEY, KIE_AI_BASE_URL

logger = logging.getLogger(__name__)


class KieAiError(Exception):
    pass


class KieAiClient:
    def __init__(self):
        self.api_key = KIE_AI_API_KEY
        self.base_url = KIE_AI_BASE_URL.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        })

    # ------------------------------------------------------------------
    # Watermark removal + photo enhancement (Seedream image-to-image)
    # ------------------------------------------------------------------

    def enhance_property_photo(
        self,
        image_url: str,
        home_staging: bool = False,
    ) -> Optional[str]:
        """
        Quita la marca de agua de Idealista y mejora la foto como un
        fotógrafo profesional. Si home_staging=True y el piso está vacío,
        añade decoración virtual.

        Devuelve la URL de la imagen procesada, o None si falla.
        """
        prompt = self._build_prompt(home_staging)

        payload = {
            "model": "seedream-3-0",
            "image_url": image_url,
            "prompt": prompt,
            "negative_prompt": "watermark, text, logo, brand name, idealista, low quality, blurry",
            "strength": 0.45,        # conservar estructura, cambiar poco
            "guidance_scale": 7.5,
            "num_inference_steps": 30,
        }

        try:
            resp = self.session.post(
                f"{self.base_url}/image-to-image",
                json=payload,
                timeout=120,
            )
            resp.raise_for_status()
            data = resp.json()

            # Kie.ai puede devolver tarea asíncrona o resultado directo
            if "task_id" in data:
                return self._poll_task(data["task_id"])

            return data.get("output_url") or data.get("url") or data.get("image_url")

        except requests.HTTPError as e:
            logger.error(f"Kie.ai HTTP error: {e} — {e.response.text[:300]}")
            return None
        except Exception as e:
            logger.error(f"Kie.ai error inesperado: {e}")
            return None

    # ------------------------------------------------------------------
    # Home staging para pisos vacíos
    # ------------------------------------------------------------------

    def home_staging(self, image_url: str) -> Optional[str]:
        """
        Añade decoración virtual a un piso vacío manteniendo las medidas.
        """
        return self.enhance_property_photo(image_url, home_staging=True)

    # ------------------------------------------------------------------
    # Polling para tareas asíncronas
    # ------------------------------------------------------------------

    def _poll_task(self, task_id: str, max_wait: int = 180) -> Optional[str]:
        url = f"{self.base_url}/tasks/{task_id}"
        elapsed = 0
        interval = 5

        while elapsed < max_wait:
            try:
                resp = self.session.get(url, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                status = data.get("status", "")

                if status in ("completed", "succeeded", "success"):
                    return (
                        data.get("output_url")
                        or data.get("url")
                        or data.get("image_url")
                    )
                if status in ("failed", "error"):
                    logger.error(f"Tarea Kie.ai fallida: {data}")
                    return None

                logger.debug(f"Tarea {task_id} en estado '{status}'. Esperando {interval}s...")
                time.sleep(interval)
                elapsed += interval
                interval = min(interval * 1.5, 30)

            except Exception as e:
                logger.error(f"Error polling tarea {task_id}: {e}")
                return None

        logger.error(f"Timeout esperando tarea Kie.ai {task_id}")
        return None

    # ------------------------------------------------------------------
    # Prompt builder
    # ------------------------------------------------------------------

    @staticmethod
    def _build_prompt(home_staging: bool) -> str:
        base = (
            "Professional real estate photography, remove any watermark or text overlay, "
            "enhance lighting to look bright and welcoming, correct white balance, "
            "increase natural light, sharpen details, HDR quality, "
            "no brand logos or text visible"
        )
        if home_staging:
            base += (
                ", add elegant modern furniture and decoration to empty rooms, "
                "maintain exact room dimensions and layout, virtual home staging, "
                "warm and inviting atmosphere, Scandinavian style"
            )
        return base

    # ------------------------------------------------------------------
    # Descarga de imagen procesada a disco
    # ------------------------------------------------------------------

    @staticmethod
    def download_image(url: str, dest: Path) -> bool:
        try:
            resp = requests.get(url, timeout=60, stream=True)
            resp.raise_for_status()
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as f:
                for chunk in resp.iter_content(8192):
                    f.write(chunk)
            return True
        except Exception as e:
            logger.error(f"Error descargando imagen {url}: {e}")
            return False
