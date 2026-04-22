"""
Publica propiedades en WordPress y limpia menciones a inmobiliarias competidoras.
"""

import logging
import re
from typing import Optional

from scraper.idealista_scraper import Property
from wordpress.wp_client import WPClient
from config.settings import WP_PROPERTY_POST_TYPE, COMPETITOR_BRANDS

logger = logging.getLogger(__name__)


def _clean_competitor_text(text: str) -> str:
    """Elimina menciones a marcas competidoras (case-insensitive)."""
    for brand in COMPETITOR_BRANDS:
        # Eliminar la marca sola o con contexto inmediato
        text = re.sub(
            rf"\b{re.escape(brand)}\b[\.\,\s]*",
            "",
            text,
            flags=re.IGNORECASE,
        )
    # Limpiar espacios dobles resultantes
    text = re.sub(r"  +", " ", text).strip()
    return text


def _build_post_content(prop: Property) -> str:
    """Genera el HTML del post de WordPress para una propiedad."""
    features = []
    if prop.area_m2:
        features.append(f"<li>📐 <strong>Superficie:</strong> {prop.area_m2} m²</li>")
    if prop.rooms:
        features.append(f"<li>🛏 <strong>Habitaciones:</strong> {prop.rooms}</li>")
    if prop.bathrooms:
        features.append(f"<li>🚿 <strong>Baños:</strong> {prop.bathrooms}</li>")
    if prop.floor:
        features.append(f"<li>🏢 <strong>Planta:</strong> {prop.floor}</li>")
    if prop.has_parking:
        features.append("<li>🚗 Plaza de parking incluida</li>")
    if prop.has_pool:
        features.append("<li>🏊 Piscina</li>")
    if prop.has_terrace:
        features.append("<li>🌿 Terraza/Balcón</li>")

    description = _clean_competitor_text(prop.description)

    html = f"""
<div class="property-details">
    <div class="property-price">
        <span class="price">{prop.price_text}</span>
    </div>
    <div class="property-location">
        <p>📍 {prop.location}</p>
    </div>
    <ul class="property-features">
        {''.join(features)}
    </ul>
    <div class="property-description">
        <h3>Descripción</h3>
        <p>{description}</p>
    </div>
</div>
"""
    return html.strip()


def _build_post_meta(prop: Property) -> dict:
    return {
        "idealista_id": prop.idealista_id,
        "idealista_url": prop.url,
        "property_price": prop.price or 0,
        "property_type": prop.property_type,
        "operation_type": prop.operation_type,
        "area_m2": prop.area_m2 or 0,
        "rooms": prop.rooms or 0,
        "bathrooms": prop.bathrooms or 0,
        "has_parking": prop.has_parking,
        "has_pool": prop.has_pool,
        "has_terrace": prop.has_terrace,
        "location": prop.location,
    }


class PropertyPublisher:
    def __init__(self):
        self.wp = WPClient()

    def publish(
        self,
        prop: Property,
        processed_photos: list[dict],
        existing_wp_id: Optional[int] = None,
    ) -> Optional[int]:
        """
        Crea o actualiza un post de propiedad en WordPress.
        Devuelve el ID del post de WordPress.
        """
        # Subir fotos y recoger IDs de media
        media_ids = self._upload_photos(prop.idealista_id, processed_photos)
        featured_id = media_ids[0] if media_ids else None

        title = _clean_competitor_text(prop.title)
        content = _build_post_content(prop)
        meta = _build_post_meta(prop)

        post_data = {
            "title": title,
            "content": content,
            "status": "publish",
            "meta": meta,
        }

        if featured_id:
            post_data["featured_media"] = featured_id

        # Añadir categoría según tipo de operación
        cat_name = "Venta" if prop.operation_type == "sale" else "Alquiler"
        cat_id = self.wp.get_or_create_term("categories", cat_name)
        if cat_id:
            post_data["categories"] = [cat_id]

        # Añadir tag de tipo de propiedad
        tag_id = self.wp.get_or_create_term("tags", prop.property_type.capitalize())
        if tag_id:
            post_data["tags"] = [tag_id]

        try:
            if existing_wp_id:
                result = self.wp.update_post(WP_PROPERTY_POST_TYPE, existing_wp_id, post_data)
                logger.info(f"Propiedad {prop.idealista_id} actualizada en WP (ID {existing_wp_id})")
            else:
                result = self.wp.create_post(WP_PROPERTY_POST_TYPE, post_data)
                logger.info(f"Propiedad {prop.idealista_id} creada en WP (ID {result['id']})")

            # Adjuntar imágenes adicionales como galería (meta campo)
            if len(media_ids) > 1:
                self.wp.update_post(WP_PROPERTY_POST_TYPE, result["id"], {
                    "meta": {"gallery_ids": ",".join(str(i) for i in media_ids[1:])}
                })

            return result["id"]

        except Exception as e:
            logger.error(f"Error publicando propiedad {prop.idealista_id} en WP: {e}")
            return None

    def unpublish(self, wp_post_id: int):
        """Borra o despublica una propiedad que ya no existe en Idealista."""
        try:
            self.wp.delete_post(WP_PROPERTY_POST_TYPE, wp_post_id)
            logger.info(f"Propiedad WP ID {wp_post_id} eliminada.")
        except Exception as e:
            logger.error(f"Error eliminando post WP {wp_post_id}: {e}")

    def _upload_photos(self, idealista_id: str, processed_photos: list[dict]) -> list[int]:
        media_ids = []
        for idx, photo in enumerate(processed_photos):
            local_path = photo.get("local_path")
            if not local_path:
                continue
            title = f"Propiedad {idealista_id} - Foto {idx + 1}"
            media = self.wp.upload_media(local_path, title)
            if media:
                media_ids.append(media["id"])
        return media_ids
