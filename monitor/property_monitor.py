"""
Monitor de propiedades: ejecuta el ciclo completo cada 24h.
1. Scrapea Idealista
2. Compara con la DB
3. Procesa fotos nuevas/actualizadas con Kie.ai
4. Publica/actualiza/elimina en WordPress
"""

import logging
import re
from dataclasses import dataclass

from scraper.idealista_scraper import IdealistaScraper, Property
from photo_processor.photo_enhancer import PhotoEnhancer
from wordpress.property_publisher import PropertyPublisher
from database.db import Database
from monitor.processing_report import CycleReporter, PropertyReport
from config.settings import SCRAPE_DELAY_MIN, SCRAPE_DELAY_MAX, SKIP_WORDPRESS

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
        self.scraper = IdealistaScraper(delay_range=(SCRAPE_DELAY_MIN, SCRAPE_DELAY_MAX), headless=False)
        self.enhancer = PhotoEnhancer()
        self.publisher = PropertyPublisher()
        self.db = Database()

    def run(self) -> RunStats:
        """Ejecuta un ciclo completo de monitoreo."""
        stats = RunStats()
        run_id = self.db.start_scrape_run()
        self.reporter = CycleReporter()

        try:
            # 1. IDs ya conocidos en BD (para no re-scrapear detalles innecesariamente)
            known_ids = self.db.get_active_ids()
            if known_ids:
                logger.info("Propiedades ya en BD: %d — solo se scrapearan las nuevas", len(known_ids))

            # 2. Scrapear Idealista (saltando detalles de propiedades conocidas)
            logger.info("=== Iniciando ciclo de scraping ===")
            scraped_props = self.scraper.scrape_all_profiles(known_ids=known_ids)
            # seen_ids: todos los IDs vistos en listings (nuevos + conocidos sin eliminar)
            all_seen_ids = self.scraper.seen_ids | {p.idealista_id for p in scraped_props}
            stats.found = len(all_seen_ids)
            logger.info("Propiedades en Idealista: %d total (%d nuevas)", stats.found, len(scraped_props))

            # 3. Propiedades eliminadas de Idealista → eliminar de WP
            removed_ids = known_ids - all_seen_ids
            for rid in removed_ids:
                self._handle_removed(rid)
                stats.removed += 1

            # 4. Procesar propiedades nuevas y actualizadas
            for prop in scraped_props:
                try:
                    is_new, was_updated = self.db.upsert_property(self._prop_to_dict(prop))

                    if is_new:
                        logger.info(f"[NUEVA] {prop.idealista_id} — {prop.title[:50]}")
                        rep = self.reporter.new_property(prop.idealista_id, prop.title, prop.url)
                        rep.photos_scraped = len(prop.photo_urls)
                        self._process_and_publish(prop, rep)
                        stats.new += 1
                    elif was_updated:
                        logger.info(f"[ACTUALIZADA] {prop.idealista_id}")
                        rep = self.reporter.new_property(prop.idealista_id, prop.title, prop.url)
                        rep.photos_scraped = len(prop.photo_urls)
                        self._process_and_publish(prop, rep, update=True)
                        stats.updated += 1
                    else:
                        # Reintentar publicación si falló antes (no tiene wp_post_id)
                        db_prop = self.db.get_property(prop.idealista_id)
                        if db_prop and not db_prop.get("wp_post_id"):
                            logger.info(f"[REINTENTO] {prop.idealista_id} — sin wp_post_id, reintentando publicación")
                            rep = self.reporter.new_property(prop.idealista_id, prop.title, prop.url)
                            rep.photos_scraped = len(prop.photo_urls)
                            rep.note("Reintento: propiedad sin wp_post_id de ciclo anterior")
                            self._process_and_publish(prop, rep)
                            stats.new += 1
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
        finally:
            # Volcar reporte estructurado (siempre, incluso si hubo error)
            try:
                self.reporter.flush()
            except Exception as e:
                logger.error("Error volcando reporte de ciclo: %s", e)

        return stats

    # ------------------------------------------------------------------
    # Internos
    # ------------------------------------------------------------------

    @staticmethod
    def _photo_folder_name(prop: Property) -> str:
        m = re.search(r'/pro/([^/]+)/', prop.url)
        profile = m.group(1) if m else prop.idealista_id
        title = re.sub(r'[<>:"/\\|?*]', '', prop.title)[:60].strip()
        return f"{title} - {profile}"

    def _process_and_publish(self, prop: Property, rep: PropertyReport, update: bool = False):
        # Mejorar fotos con Kie.ai
        processed_photos = self.enhancer.process_property_photos(
            prop.idealista_id,
            prop.photo_urls,
            prop.is_floor_plan,
            prop.photo_labels,
            folder_name=self._photo_folder_name(prop),
            report=rep,
        )

        # Si el enhancer devuelve lista vacía = fallo de KIE → NO subir a WP (copyright)
        if not processed_photos:
            logger.warning(
                "[SKIP-WP] Propiedad %s no se publica en WP — fotos no procesadas (se reintentará próximo ciclo)",
                prop.idealista_id,
            )
            rep.wp_action = "aborted"
            return

        self.db.set_processed_photos(prop.idealista_id, processed_photos)

        if SKIP_WORDPRESS:
            logger.info("SKIP_WORDPRESS=true — publicación en WP omitida para %s", prop.idealista_id)
            rep.wp_action = "skipped"
            rep.note("SKIP_WORDPRESS=true")
            return

        # Obtener WP ID si es actualización
        wp_post_id = None
        if update:
            db_prop = self.db.get_property(prop.idealista_id)
            wp_post_id = db_prop.get("wp_post_id") if db_prop else None

        # Publicar en WordPress
        new_wp_id = self.publisher.publish(prop, processed_photos, wp_post_id, report=rep)
        if new_wp_id:
            self.db.set_wp_post_id(prop.idealista_id, new_wp_id)
            rep.wp_post_id = new_wp_id
            rep.wp_action = "updated" if wp_post_id else "created"
        else:
            rep.wp_action = "aborted"
            rep.note("WP publish devolvió None")

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
            "photo_labels": prop.photo_labels,
        }
