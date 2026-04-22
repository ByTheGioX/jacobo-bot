"""
Monitor de propiedades: ejecuta el ciclo completo cada 24h.
1. Scrapea Idealista
2. Compara con la DB
3. Procesa fotos nuevas/actualizadas con Kie.ai
4. Publica/actualiza/elimina en WordPress
"""

import logging
from dataclasses import dataclass

from scraper.idealista_scraper import IdealistaScraper, Property
from photo_processor.photo_enhancer import PhotoEnhancer
from wordpress.property_publisher import PropertyPublisher
from database.db import Database

logger = logging.getLogger(__name__)


@dataclass
class RunStats:
    found: int = 0
    new: int = 0
    updated: int = 0
    removed: int = 0
    errors: int = 0


class PropertyMonitor:
    def __init__(self):
        self.scraper = IdealistaScraper()
        self.enhancer = PhotoEnhancer()
        self.publisher = PropertyPublisher()
        self.db = Database()

    def run(self) -> RunStats:
        """Ejecuta un ciclo completo de monitoreo."""
        stats = RunStats()
        run_id = self.db.start_scrape_run()

        try:
            # 1. Scrapear todas las propiedades actuales de Idealista
            logger.info("=== Iniciando ciclo de scraping ===")
            scraped_props = self.scraper.scrape_all_profiles()
            stats.found = len(scraped_props)
            logger.info(f"Propiedades encontradas en Idealista: {stats.found}")

            # 2. IDs activos en nuestra DB
            known_ids = self.db.get_active_ids()
            scraped_ids = {p.idealista_id for p in scraped_props}

            # 3. Propiedades eliminadas de Idealista → eliminar de WP
            removed_ids = known_ids - scraped_ids
            for rid in removed_ids:
                self._handle_removed(rid)
                stats.removed += 1

            # 4. Procesar propiedades nuevas y actualizadas
            for prop in scraped_props:
                try:
                    is_new, was_updated = self.db.upsert_property(self._prop_to_dict(prop))

                    if is_new:
                        logger.info(f"[NUEVA] {prop.idealista_id} — {prop.title[:50]}")
                        self._process_and_publish(prop)
                        stats.new += 1
                    elif was_updated:
                        logger.info(f"[ACTUALIZADA] {prop.idealista_id}")
                        self._process_and_publish(prop, update=True)
                        stats.updated += 1
                    else:
                        logger.debug(f"[SIN CAMBIOS] {prop.idealista_id}")

                except Exception as e:
                    logger.error(f"Error procesando propiedad {prop.idealista_id}: {e}")
                    stats.errors += 1

            logger.info(
                f"=== Ciclo finalizado === "
                f"Nuevas: {stats.new} | Actualizadas: {stats.updated} | "
                f"Eliminadas: {stats.removed} | Errores: {stats.errors}"
            )
            self.db.finish_scrape_run(run_id, {
                "found": stats.found,
                "new": stats.new,
                "updated": stats.updated,
                "removed": stats.removed,
            })

        except Exception as e:
            logger.error(f"Error crítico en ciclo de monitoreo: {e}")
            self.db.finish_scrape_run(run_id, {}, error=str(e))

        return stats

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    def _process_and_publish(self, prop: Property, update: bool = False):
        # Mejorar fotos con Kie.ai
        processed_photos = self.enhancer.process_property_photos(
            prop.idealista_id,
            prop.photo_urls,
            prop.is_floor_plan,
        )
        self.db.set_processed_photos(prop.idealista_id, processed_photos)

        # Obtener WP ID si es actualización
        wp_post_id = None
        if update:
            db_prop = self.db.get_property(prop.idealista_id)
            wp_post_id = db_prop.get("wp_post_id") if db_prop else None

        # Publicar en WordPress
        new_wp_id = self.publisher.publish(prop, processed_photos, wp_post_id)
        if new_wp_id:
            self.db.set_wp_post_id(prop.idealista_id, new_wp_id)

    def _handle_removed(self, idealista_id: str):
        db_prop = self.db.get_property(idealista_id)
        if db_prop and db_prop.get("wp_post_id"):
            self.publisher.unpublish(db_prop["wp_post_id"])
        self.db.mark_removed(idealista_id)
        logger.info(f"[ELIMINADA] {idealista_id} — borrada de WP y marcada como inactiva")

    @staticmethod
    def _prop_to_dict(prop: Property) -> dict:
        return {
            "idealista_id": prop.idealista_id,
            "url": prop.url,
            "title": prop.title,
            "price": prop.price,
            "price_text": prop.price_text,
            "location": prop.location,
            "area_m2": prop.area_m2,
            "rooms": prop.rooms,
            "bathrooms": prop.bathrooms,
            "floor": prop.floor,
            "description": prop.description,
            "property_type": prop.property_type,
            "operation_type": prop.operation_type,
            "has_parking": prop.has_parking,
            "has_pool": prop.has_pool,
            "has_terrace": prop.has_terrace,
            "photo_urls": prop.photo_urls,
        }
