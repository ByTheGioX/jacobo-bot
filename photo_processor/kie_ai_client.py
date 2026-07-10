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
_STATUS = "/api/v1/jobs/recordInfo"
_MODEL = "seedream/5-lite-image-to-image"

_ENHANCE_PROMPT = (
    "Professional real estate photograph enhancement. "
    "MANDATORY FIRST STEP — REMOVE WITHOUT EXCEPTION every watermark, including those that are: "
    "semi-transparent, low-opacity, blended with the background, camouflaged on walls/furniture/sky/floor, "
    "stylized as 'iiii', 'idealista', 'fotocasa', 'pisos.com', or any letters/dots arranged horizontally. "
    "Look especially in image CENTERS and behind objects (beds, chairs, walls) for subtle text patterns. "
    "Watermarks may appear as faint repeating characters that look like decoration but ARE NOT — REMOVE them. "
    "Reconstruct the wall/surface behind any removed watermark to look completely natural. "
    "The output image MUST contain ZERO text, ZERO logos, ZERO watermarks — this is non-negotiable. "
    "THEN ENHANCE: natural bright light, correct white balance, HDR-quality contrast and sharpness. "
    "PRESERVE: exact room layout, all existing furniture, architectural details, "
    "original composition, and the original 16:9 aspect ratio — do NOT crop, stretch, or pad. "
    "Output: clean, magazine-quality real estate photo with absolutely no text or logos of any kind."
)

_STAGING_PROMPT = (
    "Virtual home staging on an empty real estate room. "
    "REMOVE: every watermark, text overlay, logo, and brand name. "
    "ADD: elegant Scandinavian-modern furniture and decor — warm neutral palette, "
    "natural textures (linen, wood, stone), soft ambient lighting, tasteful art. "
    "PRESERVE: exact wall positions, ceiling height, floor material, window and door positions, "
    "and the original 16:9 aspect ratio — do NOT crop, stretch, or alter architecture. "
    "Output: photorealistic, magazine-quality staged interior."
)

_ROOM_STAGING_HINTS = {
    "salon": "living room: sofa, coffee table, rug, floor lamp, bookshelf or TV unit",
    "cocina": "kitchen: clean countertops, bar stools if space allows, potted herb",
    "dormitorio": "bedroom: double bed with headboard, two bedside tables, pendant lights",
    "dormitorio_principal": "master bedroom: king bed, matching bedside tables, wardrobe, reading chair",
    "bano": "bathroom — enhance only, do not add furniture, clean and bright",
    "terraza": "terrace: outdoor lounge chairs, side table, potted plants, string lights",
    "jardin": "garden: lawn furniture, manicured plants, ambient outdoor lighting",
    "garaje": "garage — enhance lighting only, no furniture",
}


class KieAiError(Exception):
    pass


class KieAiNoCreditsError(KieAiError):
    """Se levanta cuando la cuenta KIE.AI no tiene saldo (HTTP 402). Aborta el ciclo completo."""
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

    def enhance_photo(
        self,
        image_url: str,
        home_staging: bool = False,
        room_type: Optional[str] = None,
    ) -> Optional[str]:
        """
        Envía una foto a Kie.ai para quitar marca de agua y mejorar la iluminación.
        home_staging=True: añade decoración virtual (solo para habitaciones vacías).
        room_type: clave de _ROOM_STAGING_HINTS para prompt de staging específico.
        Devuelve la URL de la imagen procesada, o None si falla.
        """
        if home_staging:
            hint = _ROOM_STAGING_HINTS.get(room_type or "", "") if room_type else ""
            prompt = _STAGING_PROMPT + (f" Room type: {hint}." if hint else "")
        else:
            prompt = _ENHANCE_PROMPT
        payload = {
            "model": _MODEL,
            "input": {
                "prompt": prompt,
                "image_urls": [image_url],
                "aspect_ratio": "16:9",
                "quality": "high",
                "nsfw_checker": False,
            },
        }
        # Reintento automático para errores transitorios (422 validación, 500/501 servidor)
        _RETRYABLE = {422, 500, 501}
        for attempt in range(1, 3):
            try:
                resp = self._session.post(f"{_BASE}{_CREATE}", json=payload, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                code = data.get("code")
                if code == 200:
                    task_id = data["data"]["taskId"]
                    logger.debug("Kie.ai task created: %s", task_id)
                    result = self._poll(task_id)
                    if result:
                        return result
                    # tarea fallida — reintentar si quedan intentos
                    if attempt < 2:
                        logger.warning("Kie.ai tarea sin resultado, reintentando (intento %d)...", attempt)
                        time.sleep(5)
                        continue
                    return None
                if code == 402:
                    raise KieAiNoCreditsError(
                        f"KIE.AI sin créditos (402): {data.get('msg')} — recarga saldo en https://kie.ai"
                    )
                if code in _RETRYABLE and attempt < 2:
                    logger.warning("Kie.ai error %s: %s — reintentando en 5s...", code, data.get("msg"))
                    time.sleep(5)
                    continue
                logger.error("Kie.ai create error %s: %s", code, data.get("msg"))
                return None
            except requests.HTTPError as e:
                status = e.response.status_code if e.response is not None else 0
                if status == 402:
                    raise KieAiNoCreditsError(
                        "KIE.AI sin créditos (402) — recarga saldo en https://kie.ai"
                    ) from e
                if status in _RETRYABLE and attempt < 2:
                    logger.warning("Kie.ai HTTP %s — reintentando en 5s...", status)
                    time.sleep(5)
                    continue
                logger.error("Kie.ai HTTP %s: %s", status, e.response.text[:300] if e.response else "")
                return None
            except KieAiNoCreditsError:
                raise
            except Exception as e:
                logger.error("Kie.ai error inesperado: %s", e)
                return None
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
                state = (task.get("state") or "").lower()
                if state == "success":
                    return self._extract_output_url(task)
                if state in ("fail", "failed", "error"):
                    logger.error("Tarea Kie.ai fallida: %s — %s", task.get("failCode"), task.get("failMsg"))
                    return None
                logger.debug("Tarea %s: '%s' — esperando %ds", task_id, state, interval)
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
        """Extrae la URL de la imagen procesada del campo resultJson."""
        import json as _json
        result_json = task.get("resultJson")
        if result_json:
            try:
                parsed = _json.loads(result_json)
                urls = parsed.get("resultUrls") or parsed.get("urls") or []
                if urls:
                    return urls[0]
            except Exception:
                pass
        return None

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
