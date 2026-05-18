"""
Scheduler principal con APScheduler.
Ejecuta el monitoreo cada SCRAPE_INTERVAL_HOURS horas (por defecto 72h).
"""

import logging
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from config.settings import SCRAPE_INTERVAL_HOURS
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
    Ejecuta el monitoreo cada SCRAPE_INTERVAL_HOURS horas (defecto 72h).
    Si run_now=True, ejecuta el primer ciclo inmediatamente.
    """
    scheduler = BlockingScheduler()

    scheduler.add_job(
        run_monitor_job,
        trigger=IntervalTrigger(hours=SCRAPE_INTERVAL_HOURS),
        id="property_monitor",
        name="Idealista property monitor",
        max_instances=1,
        coalesce=True,
    )

    if run_now:
        logger.info("[SCHEDULER] Ejecutando ciclo inicial...")
        run_monitor_job()

    logger.info("[SCHEDULER] Scheduler iniciado. Proxima ejecucion en %dh.", SCRAPE_INTERVAL_HOURS)
    scheduler.start()
