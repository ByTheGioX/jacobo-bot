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
    ) -> Optional[int]:
        """Crea o actualiza un post de propiedad en WordPress/Houzez."""
        media_ids = self._upload_photos(prop.idealista_id, processed_photos)
        featured_id = media_ids[0] if media_ids else None

        # Taxonomías Houzez
        status_label = "En alquiler" if prop.operation_type == "rent" else "En venta"
        status_id = self.wp.get_or_create_term("property-status", status_label)

        type_label = _TYPE_MAP.get(prop.property_type.lower(), prop.property_type.capitalize() or "Propiedad")
        type_id = self.wp.get_or_create_term("property-type", type_label)

        city = (prop.location.split(",")[0].strip() if prop.location else "") or "Málaga"
        city_id = self.wp.get_or_create_term("property-city", city)

        feature_ids = self._get_feature_ids(prop)

        # Meta campos Houzez
        meta: dict = {
            "FAVE_PROPERTY_PRICE": str(prop.price or 0),
            "FAVE_PROPERTY_ID": f"idealista-{prop.idealista_id}",
            "FAVE_PROPERTY_MAP_ADDRESS": prop.location or "",
        }
        if prop.area_m2:
            meta["FAVE_PROPERTY_SIZE"] = str(prop.area_m2)
        if prop.rooms:
            meta["FAVE_PROPERTY_BEDROOMS"] = str(prop.rooms)
        if prop.bathrooms:
            meta["FAVE_PROPERTY_BATHROOMS"] = str(prop.bathrooms)
        if prop.has_parking:
            meta["FAVE_PROPERTY_GARAGE"] = "1"
        if len(media_ids) > 1:
            meta["fave_property_images"] = ",".join(str(i) for i in media_ids[1:])

        title = _clean_text(prop.title)
        content = self._rewrite_description(_clean_text(prop.description), prop)

        post_data: dict = {
            "title": title,
            "content": content,
            "status": "publish",
            "meta": meta,
        }
        if featured_id:
            post_data["featured_media"] = featured_id
        if status_id:
            post_data["property-status"] = [status_id]
        if type_id:
            post_data["property-type"] = [type_id]
        if city_id:
            post_data["property-city"] = [city_id]
        if feature_ids:
            post_data["property-feature"] = feature_ids

        try:
            if existing_wp_id:
                result = self.wp.update_post(WP_PROPERTY_REST_BASE, existing_wp_id, post_data)
                logger.info("Propiedad %s actualizada en WP (ID %s)", prop.idealista_id, existing_wp_id)
            else:
                result = self.wp.create_post(WP_PROPERTY_REST_BASE, post_data)
                logger.info("Propiedad %s creada en WP (ID %s)", prop.idealista_id, result["id"])
            return result["id"]
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
                json={"model": OPENROUTER_MODEL, "messages": [{"role": "user", "content": prompt}], "max_tokens": 400},
                timeout=30,
            )
            resp.raise_for_status()
            rewritten = resp.json()["choices"][0]["message"]["content"].strip()
            if rewritten:
                logger.info("Descripción reescrita con IA (%d → %d chars)", len(raw), len(rewritten))
                return rewritten
        except Exception as e:
            logger.warning("Error reescribiendo descripción con IA: %s — usando original", e)
        return raw

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
            fid = self.wp.get_or_create_term("property-feature", name)
            if fid:
                ids.append(fid)
        return ids
