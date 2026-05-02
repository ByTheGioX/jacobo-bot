"""
Scheduler principal con APScheduler.
Ejecuta el monitoreo de propiedades cada dia a las SCRAPE_HOUR (por defecto 3am).
"""

import logging
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from config.settings import SCRAPE_HOUR, SCRAPE_MINUTE
from monitor.property_monitor import PropertyMonitor

logger = logging.getLogger(__name__)


def run_monitor_job():
    logger.info("[SCHEDULER] Lanzando ciclo de monitoreo")
    monitor = PropertyMonitor()
    stats = monitor.run()
    logger.info("[SCHEDULER] Ciclo completado: %s", stats)


def start_scheduler(run_now: bool = True):
    """
    Inicia el scheduler en modo bloqueante.
    Ejecuta el monitoreo diariamente a las SCRAPE_HOUR:SCRAPE_MINUTE (defecto 03:00).
    Si run_now=True, ejecuta el primer ciclo inmediatamente.
    """
    scheduler = BlockingScheduler()

    scheduler.add_job(
        run_monitor_job,
        trigger=CronTrigger(hour=SCRAPE_HOUR, minute=SCRAPE_MINUTE),
        id="property_monitor",
        name="Idealista property monitor",
        max_instances=1,
        coalesce=True,
    )

    if run_now:
        logger.info("[SCHEDULER] Ejecutando ciclo inicial...")
        run_monitor_job()

    logger.info("[SCHEDULER] Scheduler iniciado. Proxima ejecucion diaria a las %02d:%02d.", SCRAPE_HOUR, SCRAPE_MINUTE)
    scheduler.start()
