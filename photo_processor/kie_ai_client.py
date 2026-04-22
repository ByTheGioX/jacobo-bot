"""
Cliente para la API de Kie.ai — Seedream 5.0 Lite Image-to-Image.

Endpoints:
  POST https://api.kie.ai/api/v1/jobs/createTask  → devuelve taskId
  GET  https://api.kie.ai/api/v1/jobs/getTaskDetail?taskId=… → polling
"""

import logging
import time
from pathlib import Path
from typing import Optional

import requests

from config.settings import KIE_AI_API_KEY

logger = logging.getLogger(__name__)

_BASE = "https://api.kie.ai"
_CREATE = "/api/v1/jobs/createTask"
_STATUS = "/api/v1/jobs/getTaskDetail"
_MODEL = "seedream/5-lite-image-to-image"

_ENHANCE_PROMPT = (
    "Professional real estate photography. "
    "Remove any watermark, text overlay, logo, or brand name (including 'idealista'). "
    "Enhance lighting to look bright, natural and welcoming. "
    "Correct white balance, boost natural light, sharpen architectural details, "
    "HDR quality finish. Keep the exact same room layout and composition. "
    "No text or logos visible anywhere in the image."
)

_STAGING_PROMPT = (
    _ENHANCE_PROMPT
    + " The room is empty — add elegant modern furniture and decoration, "
    "Scandinavian style, warm atmosphere. Maintain exact room dimensions and proportions."
)


class KieAiError(Exception):
    pass


class KieAiClient:
    def __init__(self):
        if not KIE_AI_API_KEY:
            raise KieAiError("KIE_AI_API_KEY no configurada en .env")
        self._session = requests.Session()
        self._session.headers.update({
            "Authorization": f"Bearer {KIE_AI_API_KEY}",
            "Content-Type": "application/json",
        })

    def enhance_photo(self, image_url: str, home_staging: bool = False) -> Optional[str]:
        """
        Envía una foto a Kie.ai para quitar marca de agua y mejorar la iluminación.
        Si home_staging=True, añade decoración virtual a habitaciones vacías.
        Devuelve la URL de la imagen procesada, o None si falla.
        """
        prompt = _STAGING_PROMPT if home_staging else _ENHANCE_PROMPT
        payload = {
            "model": _MODEL,
            "input": {
                "prompt": prompt,
                "image_urls": [image_url],
                "aspect_ratio": "4:3",
                "quality": "basic",
                "nsfw_checker": False,
            },
        }
        try:
            resp = self._session.post(f"{_BASE}{_CREATE}", json=payload, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 200:
                logger.error("Kie.ai create error %s: %s", data.get("code"), data.get("msg"))
                return None
            task_id = data["data"]["taskId"]
            logger.debug("Kie.ai task created: %s", task_id)
            return self._poll(task_id)
        except requests.HTTPError as e:
            logger.error("Kie.ai HTTP %s: %s", e.response.status_code, e.response.text[:300])
            return None
        except Exception as e:
            logger.error("Kie.ai error inesperado: %s", e)
            return None

    def _poll(self, task_id: str, max_wait: int = 180) -> Optional[str]:
        """Espera hasta que la tarea esté lista y devuelve la URL del resultado."""
        elapsed, interval = 0, 5
        while elapsed < max_wait:
            try:
                resp = self._session.get(
                    f"{_BASE}{_STATUS}",
                    params={"taskId": task_id},
                    timeout=30,
                )
                resp.raise_for_status()
                data = resp.json()
                if data.get("code") != 200:
                    logger.error("Kie.ai poll error %s: %s", data.get("code"), data.get("msg"))
                    return None
                task = data.get("data", {})
                status = (task.get("status") or "").lower()
                if status in ("completed", "succeeded", "success"):
                    return self._extract_output_url(task)
                if status in ("failed", "error"):
                    logger.error("Tarea Kie.ai fallida: %s", task)
                    return None
                logger.debug("Tarea %s: '%s' — esperando %ds", task_id, status, interval)
                time.sleep(interval)
                elapsed += interval
                interval = min(interval * 1.5, 30)
            except Exception as e:
                logger.error("Error polling tarea %s: %s", task_id, e)
                return None
        logger.error("Timeout (%ds) esperando tarea Kie.ai %s", max_wait, task_id)
        return None

    @staticmethod
    def _extract_output_url(task: dict) -> Optional[str]:
        """Extrae la URL de la imagen del resultado de la tarea."""
        output = task.get("output") or task.get("result") or {}
        if isinstance(output, dict):
            return (
                output.get("image_url")
                or output.get("url")
                or output.get("output_url")
            )
        if isinstance(output, list) and output:
            item = output[0]
            return item if isinstance(item, str) else item.get("url")
        return (
            task.get("imageUrl")
            or task.get("image_url")
            or task.get("outputUrl")
        )

    @staticmethod
    def download_image(url: str, dest: Path) -> bool:
        """Descarga una imagen a disco."""
        try:
            resp = requests.get(url, timeout=60, stream=True)
            resp.raise_for_status()
            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as f:
                for chunk in resp.iter_content(8192):
                    f.write(chunk)
            return True
        except Exception as e:
            logger.error("Error descargando imagen %s: %s", url, e)
            return False
