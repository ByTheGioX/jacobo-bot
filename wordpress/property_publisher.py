"""
Publica propiedades en WordPress con el tema Houzez.
Limpia menciones a inmobiliarias competidoras del título y descripción.
"""

import logging
import re
import requests
from typing import Optional

from scraper.idealista_scraper import Property
from wordpress.wp_client import WPClient
from photo_processor.photo_classifier import OpenRouterNoCreditsError
from config.settings import WP_PROPERTY_REST_BASE, COMPETITOR_BRANDS, OPENROUTER_API_KEY, OPENROUTER_MODEL

logger = logging.getLogger(__name__)

# Mapeo tipo de propiedad (Idealista) → etiqueta en Houzez
_TYPE_MAP = {
    "piso": "Piso",
    "apartamento": "Apartamento",
    "casa": "Casa",
    "chalet": "Chalet",
    "estudio": "Estudio",
    "ático": "Ático",
    "attico": "Ático",
    "local": "Local comercial",
    "oficina": "Oficina",
    "garaje": "Garaje",
    "terreno": "Terreno",
    "villa": "Villa",
}


def _clean_text(text: str) -> str:
    for brand in COMPETITOR_BRANDS:
        text = re.sub(rf"\b{re.escape(brand)}\b[\.\,\s]*", "", text, flags=re.IGNORECASE)
    return re.sub(r"  +", " ", text).strip()


class PropertyPublisher:
    def __init__(self):
        self.wp = WPClient()

    def publish(
        self,
        prop: Property,
        processed_photos: list[dict],
        existing_wp_id: Optional[int] = None,
        report=None,
    ) -> Optional[int]:
        """Crea o actualiza un post de propiedad en WordPress/Houzez."""
        media_ids = self._upload_photos(prop.idealista_id, processed_photos)
        if report is not None:
            report.photos_uploaded_wp = len(media_ids)
        featured_id = media_ids[0] if media_ids else None

        # Taxonomías Houzez (REST base usa guiones bajos, no guiones)
        # IMPORTANTE: el slug usado en fave_property_status DEBE coincidir con el slug
        # del término de la taxonomía property_status. Houzez/WPML lo crea en el idioma
        # del sitio (en este caso español): "en-venta" / "en-alquiler".
        is_rent = prop.operation_type == "rent"
        status_label = "En alquiler" if is_rent else "En venta"
        status_slug = "en-alquiler" if is_rent else "en-venta"
        status_id = self.wp.get_or_create_term("property_status", status_label)

        type_label = _TYPE_MAP.get(prop.property_type.lower(), prop.property_type.capitalize() or "Propiedad")
        type_id = self.wp.get_or_create_term("property_type", type_label)

        # Parsear ubicación: "Calle Foo, Barrio, Ciudad" → city = última parte
        location_parts = [p.strip() for p in (prop.location or "").split(",") if p.strip()]
        city = location_parts[-1] if location_parts else "Málaga"
        area = location_parts[1] if len(location_parts) > 2 else ""
        address = location_parts[0] if location_parts else ""

        city_id  = self.wp.get_or_create_term("property_city", city)
        area_id  = self.wp.get_or_create_term("property_area", area) if area else None
        feature_ids = self._get_feature_ids(prop)

        title   = _clean_text(prop.title)
        content = self._rewrite_description(_clean_text(prop.description), prop)
        excerpt = self._build_excerpt(prop)

        # Meta campos Houzez — se guardan vía XML-RPC (REST API no los persiste).
        # IMPORTANTE: el shortcode de Houzez (listing-v6-full-width) filtra por slug,
        # NO por label. fave_property_status DEBE ser el slug ("for-sale" / "for-rent").
        meta: dict = {
            "fave_property_price":        str(prop.price or 0),
            "fave_property_price_postfix": "EUR",
            "fave_property_id":           self._build_property_id(prop),
            "fave_property_status":       status_slug,
            "fave_property_type":         type_label,
            "fave_property_map_address":  prop.location or "",
            "fave_property_map_zoom":     "14",
            "fave_property_content":      excerpt,
            # Habilitar visibilidad en listings de Houzez por defecto
            "fave_featured":              "0",
            "fave_property_country":      "ES",
        }
        if prop.area_m2:
            meta["fave_property_size"]        = str(int(prop.area_m2))
            meta["fave_property_size_prefix"] = "m2"
        if prop.rooms:
            meta["fave_property_bedrooms"] = str(prop.rooms)
        if prop.bathrooms:
            meta["fave_property_bathrooms"] = str(prop.bathrooms)
        if prop.floor:
            meta["fave_property_floor"] = prop.floor
        if prop.has_parking:
            meta["fave_property_garage"] = "1"
        if media_ids:
            meta["fave_property_images"] = [str(i) for i in media_ids]

        # REST API: estructura del post + taxonomías (sin meta — se ponen vía XML-RPC)
        post_data: dict = {
            "title":   title,
            "content": content,
            "excerpt": excerpt,
            "status":  "publish",
        }
        if featured_id:
            post_data["featured_media"] = featured_id
        if status_id:
            post_data["property_status"] = [status_id]
        if type_id:
            post_data["property_type"] = [type_id]
        if city_id:
            post_data["property_city"] = [city_id]
        if area_id:
            post_data["property_area"] = [area_id]
        if feature_ids:
            post_data["property_feature"] = feature_ids

        try:
            if existing_wp_id:
                result = self.wp.update_post(WP_PROPERTY_REST_BASE, existing_wp_id, post_data)
                wp_id = existing_wp_id
                logger.info("Propiedad %s actualizada en WP (ID %s)", prop.idealista_id, wp_id)
            else:
                result = self.wp.create_post(WP_PROPERTY_REST_BASE, post_data)
                wp_id = result["id"]
                logger.info("Propiedad %s creada en WP (ID %s)", prop.idealista_id, wp_id)
            # Meta campos Houzez vía XML-RPC (REST API no los persiste correctamente)
            self.wp.set_post_meta(wp_id, meta)
            # Adjuntar todas las fotos al post para que Houzez las muestre en la galería
            if media_ids:
                self.wp.attach_media_to_post(media_ids, wp_id)
            return wp_id
        except Exception as e:
            logger.error("Error publicando propiedad %s en WP: %s", prop.idealista_id, e)
            return None

    def _rewrite_description(self, raw: str, prop: Property) -> str:
        """Reescribe la descripción con IA para que sea única. Fallback: texto original."""
        if not raw or not OPENROUTER_API_KEY:
            return raw
        context = f"Tipo: {prop.property_type}, {prop.rooms or '?'} hab., {prop.bathrooms or '?'} baños, {prop.area_m2 or '?'} m², {prop.location}."
        prompt = (
            "Eres un copywriter inmobiliario profesional en España. "
            "IMPORTANTE: Responde ÚNICAMENTE en español. Está terminantemente prohibido usar inglés. "
            "Reescribe la siguiente descripción de una propiedad para que sea atractiva, única y no sea una copia literal. "
            "Conserva TODOS los datos concretos (habitaciones, metros, ubicación, características). "
            "Tono profesional pero cercano, máximo 200 palabras. "
            "Devuelve SOLO el texto de la descripción en español, sin encabezados ni comentarios.\n\n"
            f"Contexto: {context}\n\n"
            f"Descripción original:\n{raw[:1500]}"
        )
        try:
            resp = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": OPENROUTER_MODEL,
                    "messages": [
                        {"role": "system", "content": "Eres un copywriter inmobiliario español. SIEMPRE respondes en español. Nunca uses inglés."},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 400,
                },
                timeout=30,
            )
            resp.raise_for_status()
            rewritten = resp.json()["choices"][0]["message"]["content"].strip()
            if rewritten:
                logger.info("Descripcion reescrita con IA (%d -> %d chars)", len(raw), len(rewritten))
                return rewritten
        except requests.HTTPError as e:
            status = e.response.status_code if e.response is not None else 0
            if status == 402:
                raise OpenRouterNoCreditsError(
                    "OpenRouter sin créditos (402) en reescritura de descripción"
                ) from e
            logger.warning("Error reescribiendo descripción con IA: %s — usando original", e)
        except Exception as e:
            logger.warning("Error reescribiendo descripción con IA: %s — usando original", e)
        return raw

    def _build_excerpt(self, prop: Property) -> str:
        """Genera un resumen corto con los datos clave de la propiedad."""
        parts = []
        if prop.property_type:
            parts.append(prop.property_type.capitalize())
        if prop.area_m2:
            parts.append(f"{int(prop.area_m2)} m²")
        if prop.rooms:
            parts.append(f"{prop.rooms} hab.")
        if prop.bathrooms:
            parts.append(f"{prop.bathrooms} baños")
        if prop.floor:
            parts.append(f"Planta {prop.floor}")
        if prop.location:
            parts.append(prop.location)
        return " · ".join(parts) if parts else ""

    @staticmethod
    def _build_property_id(prop) -> str:
        """Genera el ID interno de la propiedad usando el código de agencia extraído del URL.
        Ejemplo: https://...idealista.com/pro/inmobiliariavasanco/inmueble/111460478/
                 → 'inmobiliariavasanco-111460478'
        Si el URL no contiene /pro/<agencia>/, usa 'idealista' como fallback.
        """
        m = re.search(r'/pro/([^/]+)/', prop.url or "")
        agency = m.group(1) if m else "idealista"
        return f"{agency}-{prop.idealista_id}"

    def unpublish(self, wp_post_id: int):
        try:
            self.wp.delete_post(WP_PROPERTY_REST_BASE, wp_post_id)
            logger.info("Propiedad WP ID %s eliminada.", wp_post_id)
        except Exception as e:
            logger.error("Error eliminando post WP %s: %s", wp_post_id, e)

    def _upload_photos(self, idealista_id: str, processed_photos: list[dict]) -> list[int]:
        media_ids = []
        for idx, photo in enumerate(processed_photos):
            # Solo subir fotos procesadas por KIE.AI — nunca las originales descargadas
            if not photo.get("processed_url"):
                logger.debug("Foto %d sin procesar por KIE.AI — omitiendo subida", idx + 1)
                continue
            local_path = photo.get("local_path")
            if not local_path:
                continue
            title = f"Propiedad {idealista_id} foto {idx + 1}"
            media = self.wp.upload_media(local_path, title)
            if media:
                logger.info("  [WP] Foto %d subida (media ID %s)", idx + 1, media["id"])
                media_ids.append(media["id"])
        return media_ids

    def _get_feature_ids(self, prop: Property) -> list[int]:
        features = []
        if prop.has_parking:
            features.append("Garaje / Parking")
        if prop.has_pool:
            features.append("Piscina")
        if prop.has_terrace:
            features.append("Terraza")
        ids = []
        for name in features:
            fid = self.wp.get_or_create_term("property_feature", name)
            if fid:
                ids.append(fid)
        return ids
