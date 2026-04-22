"""
Cliente para la API REST de WordPress.
Gestiona posts, medios y metadatos de propiedades.
"""

import logging
import mimetypes
from pathlib import Path
from typing import Optional, Any

import requests
from requests.auth import HTTPBasicAuth

from config.settings import WP_URL, WP_USER, WP_APP_PASSWORD

logger = logging.getLogger(__name__)


class WPClient:
    def __init__(self):
        self.base = f"{WP_URL}/wp-json/wp/v2"
        self.auth = HTTPBasicAuth(WP_USER, WP_APP_PASSWORD)
        self.session = requests.Session()

    def _get(self, endpoint: str, params: dict = None) -> Any:
        resp = self.session.get(
            f"{self.base}/{endpoint}",
            auth=self.auth,
            params=params or {},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def _post(self, endpoint: str, data: dict) -> Any:
        resp = self.session.post(
            f"{self.base}/{endpoint}",
            auth=self.auth,
            json=data,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def _put(self, endpoint: str, data: dict) -> Any:
        resp = self.session.put(
            f"{self.base}/{endpoint}",
            auth=self.auth,
            json=data,
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def _delete(self, endpoint: str) -> Any:
        resp = self.session.delete(
            f"{self.base}/{endpoint}",
            auth=self.auth,
            params={"force": True},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Posts
    # ------------------------------------------------------------------

    def create_post(self, post_type: str, data: dict) -> dict:
        return self._post(f"{post_type}s", data)

    def update_post(self, post_type: str, post_id: int, data: dict) -> dict:
        return self._put(f"{post_type}s/{post_id}", data)

    def delete_post(self, post_type: str, post_id: int):
        return self._delete(f"{post_type}s/{post_id}")

    def get_posts_by_meta(self, post_type: str, meta_key: str, meta_value: str) -> list[dict]:
        """
        Busca posts por metadato. Requiere que el campo meta sea público
        o que el usuario tenga permisos de editor.
        """
        try:
            return self._get(f"{post_type}s", {
                "meta_key": meta_key,
                "meta_value": meta_value,
                "per_page": 1,
            })
        except Exception as e:
            logger.warning(f"No se pudo buscar por meta ({meta_key}={meta_value}): {e}")
            return []

    # ------------------------------------------------------------------
    # Media
    # ------------------------------------------------------------------

    def upload_media(self, file_path: str, title: str = "") -> Optional[dict]:
        """Sube una imagen a la biblioteca de medios de WordPress."""
        path = Path(file_path)
        if not path.exists():
            logger.error(f"Archivo no encontrado: {file_path}")
            return None

        mime, _ = mimetypes.guess_type(str(path))
        mime = mime or "image/jpeg"

        try:
            with open(path, "rb") as f:
                resp = self.session.post(
                    f"{self.base}/media",
                    auth=self.auth,
                    headers={
                        "Content-Disposition": f'attachment; filename="{path.name}"',
                        "Content-Type": mime,
                    },
                    data=f,
                    timeout=60,
                )
                resp.raise_for_status()
                media = resp.json()
                if title:
                    self._put(f"media/{media['id']}", {"title": title, "alt_text": title})
                return media
        except Exception as e:
            logger.error(f"Error subiendo media {file_path}: {e}")
            return None

    # ------------------------------------------------------------------
    # Taxonomy (categorías, tags)
    # ------------------------------------------------------------------

    def get_or_create_term(self, taxonomy: str, name: str) -> Optional[int]:
        try:
            terms = self._get(taxonomy, {"search": name})
            for term in terms:
                if term["name"].lower() == name.lower():
                    return term["id"]
            created = self._post(taxonomy, {"name": name})
            return created["id"]
        except Exception as e:
            logger.error(f"Error con término '{name}' en '{taxonomy}': {e}")
            return None
