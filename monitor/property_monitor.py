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
from photo_processor.kie_ai_client import KieAiNoCreditsError
from photo_processor.photo_classifier import OpenRouterNoCreditsError
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

            # 3. Propiedades que ya no aparecen en Idealista → PAUSAR (no borrar) en WP.
            #    SEGURIDAD: si algún perfil falló al scrapear (rate-limit, ban, error de red),
            #    sus propiedades faltarían en all_seen_ids y se pausarían por error. En ese caso
            #    NO tocamos nada — un scrape incompleto jamás debe dar de baja propiedades.
            if self.scraper.failed_profiles:
                logger.warning(
                    "[SEGURIDAD] %d perfil(es) fallaron al scrapear (%s). "
                    "Se OMITE la detección de bajas para no pausar propiedades por error.",
                    len(self.scraper.failed_profiles),
                    ", ".join(self.scraper.failed_profiles),
                )
            else:
                gone_ids = known_ids - all_seen_ids
                # SEGURIDAD 2 (red de protección): un perfil puede devolver 200 + HTML
                # válido pero con listing incompleto (soft-block, paginación rota) sin
                # marcarse como failed_profile. Si eso pasara, se pausarían en masa
                # propiedades reales. Una agencia casi nunca da de baja >50% de su stock
                # en un solo ciclo (72h): si gone_ids supera ese umbral, es casi seguro
                # un fallo de scrape, no la realidad → OMITIMOS las bajas este ciclo.
                if known_ids and len(gone_ids) > len(known_ids) * 0.5:
                    logger.warning(
                        "[SEGURIDAD] El scrape marcaría %d/%d propiedades como bajas (>50%%). "
                        "Esto es casi seguro un fallo de scrape, no bajas reales. "
                        "Se OMITE la detección de bajas este ciclo.",
                        len(gone_ids), len(known_ids),
                    )
                else:
                    for rid in gone_ids:
                        self._handle_paused(rid)
                        stats.removed += 1

            # 3b. Propiedades pausadas que VUELVEN a aparecer → reactivar (borrador → publicada)
            reappeared = self.db.get_paused_ids() & all_seen_ids
            for rid in reappeared:
                self._handle_reappeared(rid)

            # 4. Reintentar propiedades en BD sin wp_post_id (fotos ya procesadas)
            self._retry_unpublished(stats)

            # 5. Procesar propiedades nuevas y actualizadas
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

                except (KieAiNoCreditsError, OpenRouterNoCreditsError) as e:
                    logger.critical(
                        "=== CICLO DETENIDO: API SIN CRÉDITOS ===\n"
                        "  Error: %s\n"
                        "  Ninguna propiedad adicional será procesada en este ciclo.\n"
                        "  Recarga saldo y vuelve a ejecutar.",
                        e,
                    )
                    stats.errors += 1
                    raise
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

            # Purga el cache si hubo cambios en WP — necesario para que el listing
            # (que CDmon cachea 48h) refleje las nuevas/actualizadas/eliminadas sin demora.
            if stats.new or stats.updated or stats.removed:
                try:
                    self.publisher.wp.purge_all_cache()
                except Exception as e:
                    logger.warning("Purge de cache falló (no crítico): %s", e)

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

    def _retry_unpublished(self, stats: RunStats):
        """Reprocesa y republica propiedades activas sin wp_post_id (flujo completo: KIE + WP).

        El enhancer reutiliza fotos cacheadas en data/photos/<folder>/processed/ si existen,
        así que esto no gasta créditos KIE para fotos ya procesadas en ciclos anteriores.
        """
        pending = [p for p in self.db.get_active_properties() if not p.get("wp_post_id")]
        if not pending:
            return
        logger.info("[REINTENTO] %d propiedades sin wp_post_id — ejecutando flujo completo (KIE + WP)...", len(pending))
        for db_prop in pending:
            try:
                prop = self._db_prop_to_property(db_prop)
                if not prop.photo_urls:
                    logger.warning("[REINTENTO SKIP] %s — sin photo_urls en BD", prop.idealista_id)
                    continue
                rep = self.reporter.new_property(prop.idealista_id, prop.title, prop.url)
                rep.photos_scraped = len(prop.photo_urls)
                rep.note("Reintento: propiedad sin wp_post_id")
                self._process_and_publish(prop, rep)
                if rep.wp_action == "created":
                    stats.new += 1
                    logger.info("[REINTENTO OK] %s → wp_post_id=%s", prop.idealista_id, rep.wp_post_id)
                elif rep.wp_action == "aborted":
                    logger.warning("[REINTENTO ABORTADO] %s — ver reporte", prop.idealista_id)
            except (KieAiNoCreditsError, OpenRouterNoCreditsError):
                raise
            except Exception as e:
                logger.error("[REINTENTO ERROR] %s: %s", db_prop.get("idealista_id"), e)
                stats.errors += 1

    @staticmethod
    def _db_prop_to_property(db_prop: dict) -> "Property":
        """Reconstruye un objeto Property desde datos de la BD."""
        import json
        from scraper.idealista_scraper import Property
        photo_urls = db_prop.get("photo_urls", [])
        if isinstance(photo_urls, str):
            photo_urls = json.loads(photo_urls)
        n = len(photo_urls)
        return Property(
            idealista_id=db_prop["idealista_id"],
            url=db_prop.get("url", ""),
            title=db_prop.get("title", ""),
            price=db_prop.get("price"),
            price_text="",
            location=db_prop.get("location", ""),
            area_m2=db_prop.get("area_m2"),
            rooms=db_prop.get("rooms"),
            bathrooms=db_prop.get("bathrooms"),
            floor=db_prop.get("floor") or "",
            description=db_prop.get("description", "") or "",
            property_type=db_prop.get("property_type", "") or "",
            operation_type=db_prop.get("operation_type", "sale") or "sale",
            has_parking=bool(db_prop.get("has_parking")),
            has_pool=bool(db_prop.get("has_pool")),
            has_terrace=bool(db_prop.get("has_terrace")),
            photo_urls=photo_urls,
            photo_labels=[""] * n,
            is_floor_plan=[False] * n,
        )

    def _handle_paused(self, idealista_id: str):
        """Ya no aparece en Idealista → pausa la publicación en WP (borrador). NUNCA la borra."""
        db_prop = self.db.get_property(idealista_id)
        if db_prop and db_prop.get("wp_post_id"):
            self.publisher.pause(db_prop["wp_post_id"])
        self.db.mark_paused(idealista_id)
        logger.info(f"[PAUSADA] {idealista_id} — ya no está en Idealista, puesta en borrador en WP")

    def _handle_reappeared(self, idealista_id: str):
        """Volvió a aparecer en Idealista → reactiva la publicación (borrador → publicada)."""
        db_prop = self.db.get_property(idealista_id)
        if db_prop and db_prop.get("wp_post_id"):
            self.publisher.unpause(db_prop["wp_post_id"])
        self.db.mark_active(idealista_id)
        logger.info(f"[REACTIVADA] {idealista_id} — volvió a Idealista, republicada en WP")

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
