"""
Scheduler principal con APScheduler.
Ejecuta el monitoreo de propiedades cada N horas (por defecto 24).
"""

import logging
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from config.settings import CHECK_INTERVAL_HOURS
from monitor.property_monitor import PropertyMonitor

logger = logging.getLogger(__name__)


def run_monitor_job():
    logger.info(f"[SCHEDULER] Lanzando ciclo de monitoreo")
    monitor = PropertyMonitor()
    stats = monitor.run()
    logger.info(f"[SCHEDULER] Ciclo completado: {stats}")


def start_scheduler(run_now: bool = True):
    """
    Inicia el scheduler en modo bloqueante.
    Si run_now=True, ejecuta el primer ciclo inmediatamente.
    """
    scheduler = BlockingScheduler()

    scheduler.add_job(
        run_monitor_job,
        trigger=IntervalTrigger(hours=CHECK_INTERVAL_HOURS),
        id="property_monitor",
        name="Idealista property monitor",
        max_instances=1,
        coalesce=True,
    )

    if run_now:
        logger.info("[SCHEDULER] Ejecutando ciclo inicial...")
        run_monitor_job()

    logger.info(f"[SCHEDULER] Scheduler iniciado. Próxima ejecución en {CHECK_INTERVAL_HOURS}h.")
    scheduler.start()
