"""
Cliente para la API REST de WordPress.
Gestiona posts, medios y metadatos de propiedades.
"""

import logging
import mimetypes
import time
import xmlrpc.client
from pathlib import Path
from typing import Optional, Any

import requests
from requests.auth import HTTPBasicAuth


class _RequestsTransport(xmlrpc.client.Transport):
    """XML-RPC transport que usa requests para seguir redirects (302)."""
    def __init__(self):
        super().__init__()
        self.verbose = False

    def request(self, host, handler, request_body, verbose=False):
        import io
        # Forzar HTTPS independientemente del WP_URL configurado
        url = f"https://{host}{handler}"
        resp = requests.post(url, data=request_body,
                             headers={"Content-Type": "text/xml"},
                             timeout=30, allow_redirects=True)
        resp.raise_for_status()
        return self.parse_response(io.BytesIO(resp.content))

from config.settings import WP_URL, WP_USER, WP_APP_PASSWORD

logger = logging.getLogger(__name__)


class WPClient:
    def __init__(self):
        _url = WP_URL.replace("http://", "https://")
        self.base = f"{_url}/wp-json/wp/v2"
        self.auth = HTTPBasicAuth(WP_USER, WP_APP_PASSWORD)
        self.session = requests.Session()
        self._waf_bypassed = False
        # Intentar bypass del WAF de Hostinger al iniciar
        self._bypass_waf_if_needed()

    def _bypass_waf_if_needed(self):
        """Si el WAF bloquea con 403 HTML, usa Playwright para ejecutar el challenge JS y obtener la cookie."""
        try:
            r = self.session.get(f"{self.base.split('/wp-json')[0]}/wp-json/", timeout=10)
            if r.status_code == 200 and "json" in r.headers.get("Content-Type", ""):
                return  # Sin WAF, no hace falta nada
            if r.status_code != 403 and "refresh" not in r.text[:500]:
                return
        except Exception:
            return

        logger.info("WAF detectado — usando Playwright para obtener cookies de sesión...")
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as pw:
                browser = pw.chromium.launch(headless=True)
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                )
                page = context.new_page()
                page.goto(f"{WP_URL}/wp-json/wp/v2/", wait_until="networkidle", timeout=30000)
                time.sleep(4)  # dejar que el JS del challenge se ejecute
                for cookie in context.cookies():
                    self.session.cookies.set(
                        cookie["name"], cookie["value"],
                        domain=cookie.get("domain", "").lstrip("."),
                    )
                browser.close()
            # Verificar si el bypass funcionó
            r2 = self.session.get(f"{WP_URL}/wp-json/", timeout=10)
            if r2.status_code == 200 and "json" in r2.headers.get("Content-Type", ""):
                self._waf_bypassed = True
                logger.info("WAF bypass exitoso — cookies de sesión activas")
            else:
                logger.warning("WAF bypass intentado pero sigue bloqueando (status %s)", r2.status_code)
        except Exception as e:
            logger.warning("Playwright WAF bypass falló: %s", e)

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
    # Cache purge (best-effort, prueba varios plugins comunes)
    # ------------------------------------------------------------------

    def purge_all_cache(self) -> list[str]:
        """Intenta purgar el cache via plugins/proveedores comunes.

        Best-effort: no falla si nada existe. Devuelve la lista de proveedores
        que respondieron OK (útil para log).

        Se invoca después de publicar/actualizar/eliminar propiedades para
        que las listas (incluyendo /listing-v6-full-width/) reflejen cambios
        sin esperar al TTL de cache del servidor (CDmon usa 48h).
        """
        site = self.base.split("/wp-json")[0]
        attempts = [
            # LiteSpeed Cache (común en hostings LiteSpeed como CDmon)
            ("LiteSpeed (PURGEALL)", "GET",  f"{site}/?LSCWP_CTRL=PURGEALL"),
            ("LiteSpeed REST",       "POST", f"{site}/wp-json/litespeed/v1/purge/all"),
            # WP Rocket
            ("WP Rocket",            "GET",  f"{site}/wp-admin/admin-ajax.php?action=rocket_clean_domain"),
            # W3 Total Cache
            ("W3 Total Cache",       "POST", f"{site}/wp-admin/admin-ajax.php?action=w3tc_flush_all"),
            # WP Super Cache
            ("WP Super Cache",       "GET",  f"{site}/wp-admin/admin-ajax.php?action=wpsc_purge_cache_now"),
            # WP Fastest Cache
            ("WP Fastest Cache",     "GET",  f"{site}/wp-admin/admin-ajax.php?action=wpfc_clear_cache_co"),
            # Cache Enabler
            ("Cache Enabler",        "POST", f"{site}/wp-json/cache-enabler/v1/clear"),
        ]
        purged: list[str] = []
        for name, method, url in attempts:
            try:
                if method == "GET":
                    r = self.session.get(url, auth=self.auth, timeout=15, allow_redirects=False)
                else:
                    r = self.session.post(url, auth=self.auth, timeout=15, allow_redirects=False)
                # 200/204 = OK; 302 a admin login (no autenticado vía cookie) = no aplicable
                if r.status_code in (200, 204):
                    body = (r.text or "")[:200].lower()
                    # Algunos plugins responden 200 con HTML de error/login — filtrar
                    if "login" not in body and "wp-login" not in body and "<!doctype" not in body[:50]:
                        purged.append(name)
                        logger.info("Cache purgado vía %s (HTTP %d)", name, r.status_code)
            except Exception as e:
                logger.debug("Purge %s falló: %s", name, e)

        if not purged:
            logger.info("Purge de cache: ningún plugin respondió — probablemente cache de servidor (CDmon). Limpiar manual desde panel.")
        return purged

    # ------------------------------------------------------------------
    # Posts
    # ------------------------------------------------------------------

    def create_post(self, rest_base: str, data: dict) -> dict:
        return self._post(rest_base, data)

    def update_post(self, rest_base: str, post_id: int, data: dict) -> dict:
        return self._put(f"{rest_base}/{post_id}", data)

    def delete_post(self, rest_base: str, post_id: int):
        return self._delete(f"{rest_base}/{post_id}")

    def get_posts_by_meta(self, rest_base: str, meta_key: str, meta_value: str) -> list[dict]:
        try:
            return self._get(rest_base, {
                "meta_key": meta_key,
                "meta_value": meta_value,
                "per_page": 1,
            })
        except Exception as e:
            logger.warning("No se pudo buscar por meta (%s=%s): %s", meta_key, meta_value, e)
            return []

    # ------------------------------------------------------------------
    # Media
    # ------------------------------------------------------------------

    def upload_media(self, file_path: str, title: str = "", post_id: int = 0) -> Optional[dict]:
        """Sube una imagen a la biblioteca de medios de WordPress."""
        path = Path(file_path)
        if not path.exists():
            logger.error(f"Archivo no encontrado: {file_path}")
            return None

        mime, _ = mimetypes.guess_type(str(path))
        mime = mime or "image/jpeg"

        _RETRYABLE_STATUS = {500, 502, 503, 504}
        for attempt in range(1, 4):
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
                if isinstance(media, list):
                    msg = f"WP media devolvió lista (error del servidor): {str(media)[:200]}"
                    if attempt < 3:
                        logger.warning("upload_media intento %d: %s — reintentando en 10s...", attempt, msg)
                        time.sleep(10)
                        continue
                    raise ValueError(msg)
                update = {"title": title, "alt_text": title} if title else {}
                if post_id:
                    update["post"] = post_id
                if update:
                    self._put(f"media/{media['id']}", update)
                return media
            except requests.HTTPError as e:
                status = e.response.status_code if e.response is not None else 0
                if status in _RETRYABLE_STATUS and attempt < 3:
                    logger.warning("upload_media HTTP %s intento %d — reintentando en 10s...", status, attempt)
                    time.sleep(10)
                    continue
                logger.error("Error subiendo media %s: HTTP %s", file_path, status)
                return None
            except ValueError:
                raise
            except Exception as e:
                logger.error("Error subiendo media %s: %s", file_path, e)
                return None
        return None

    def attach_media_to_post(self, media_ids: list[int], post_id: int) -> None:
        """Adjunta imágenes al post para que Houzez las muestre en la galería."""
        for mid in media_ids:
            try:
                self._put(f"media/{mid}", {"post": post_id})
            except Exception as e:
                logger.debug("No se pudo adjuntar media %s a post %s: %s", mid, post_id, e)

    # ------------------------------------------------------------------
    # Taxonomy (categorías, tags)
    # ------------------------------------------------------------------

    def set_post_meta(self, post_id: int, meta: dict) -> bool:
        """Set post custom fields via XML-RPC (REST API cannot save Houzez fave_property_* meta)."""
        proxy = xmlrpc.client.ServerProxy(f"{WP_URL}/xmlrpc.php", allow_none=True,
                                          transport=_RequestsTransport())
        # Get existing custom fields to update by ID (avoids duplicates on re-publish)
        existing: dict[str, list[str]] = {}
        try:
            post = proxy.wp.getPost(1, WP_USER, WP_APP_PASSWORD, post_id, ["custom_fields"])
            for cf in post.get("custom_fields", []):
                existing.setdefault(cf["key"], []).append(cf["id"])
        except Exception as e:
            logger.debug("XML-RPC getPost failed (non-critical): %s", e)

        custom_fields = []
        for key, value in meta.items():
            values = value if isinstance(value, list) else [value]
            existing_ids = existing.get(key, [])
            for i, v in enumerate(values):
                entry = {"key": key, "value": str(v)}
                if i < len(existing_ids):
                    entry["id"] = existing_ids[i]
                custom_fields.append(entry)

        if not custom_fields:
            return True
        try:
            proxy.wp.editPost(1, WP_USER, WP_APP_PASSWORD, post_id, {"custom_fields": custom_fields})
            logger.debug("Meta XML-RPC set para post %s: %d campos", post_id, len(custom_fields))
            return True
        except Exception as e:
            logger.error("XML-RPC set_post_meta falló para post %s: %s", post_id, e)
            return False

    def get_or_create_term(self, taxonomy: str, name: str) -> Optional[int]:
        try:
            terms = self._get(taxonomy, {"search": name})
            for term in terms:
                if term["name"].lower() == name.lower():
                    return term["id"]
            created = self._post(taxonomy, {"name": name})
            return created["id"]
        except Exception as e:
            logger.debug("Taxonomía '%s' no encontrada en '%s' (normal sin Houzez): %s", name, taxonomy, e)
            return None
