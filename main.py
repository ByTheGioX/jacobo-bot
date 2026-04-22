"""
Jacobo-Bot — Punto de entrada principal.

Uso:
  python main.py                    # Ciclo completo + scheduler 24h
  python main.py --once             # Un ciclo y salir
  python main.py --scrape-only      # Solo scrapear (sin WP ni fotos)
  python main.py --search "..."     # Procesar una búsqueda de comprador
  python main.py --dashboard        # Mostrar estadísticas
"""

import argparse
import logging
import sys
from pathlib import Path

# Asegurar que los módulos del proyecto están en el path
sys.path.insert(0, str(Path(__file__).parent))

# Crear directorio de datos antes de configurar el log
Path("data").mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("data/jacobo_bot.log", encoding="utf-8"),
    ],
)

logger = logging.getLogger("main")


def main():
    Path("data").mkdir(exist_ok=True)

    parser = argparse.ArgumentParser(description="Jacobo-Bot — Marketplace inmobiliario automatizado")
    parser.add_argument("--once", action="store_true", help="Ejecutar un solo ciclo y salir")
    parser.add_argument("--scrape-only", action="store_true", help="Solo scrapear propiedades")
    parser.add_argument("--search", metavar="QUERY", help="Procesar búsqueda de comprador")
    parser.add_argument("--email", metavar="EMAIL", default="", help="Email del comprador para --search")
    parser.add_argument("--name", metavar="NAME", default="", help="Nombre del comprador para --search")
    parser.add_argument("--dashboard", action="store_true", help="Mostrar estadísticas")
    args = parser.parse_args()

    if args.dashboard:
        from dashboard.stats import print_dashboard
        print_dashboard()
        return

    if args.search:
        _handle_search(args.search, args.email, args.name)
        return

    if args.scrape_only:
        _run_scrape_only()
        return

    if args.once:
        from monitor.property_monitor import PropertyMonitor
        logger.info("Ejecutando ciclo único...")
        monitor = PropertyMonitor()
        stats = monitor.run()
        logger.info(f"Finalizado: {stats}")
        return

    # Modo por defecto: scheduler continuo
    from scheduler.main_scheduler import start_scheduler
    start_scheduler(run_now=True)


def _handle_search(query: str, email: str, name: str):
    from search.smart_search import SmartSearch
    from search.email_sender import AgencyEmailSender

    logger.info(f"Procesando búsqueda: '{query}'")
    searcher = SmartSearch()
    result = searcher.process_query(query, contact_email=email, contact_name=name)

    if result["matches"]:
        logger.info(f"Se encontraron {len(result['matches'])} propiedades:")
        for p in result["matches"]:
            logger.info(f"  → {p['title']} | {p.get('price', '—')} €")
    else:
        logger.info("No se encontraron propiedades. Enviando email a agencias colaboradoras...")

        from search.smart_search import SearchCriteria
        criteria = SearchCriteria(
            raw_query=query,
            contact_email=email,
            contact_name=name,
            **{k: v for k, v in result["criteria"].items()
               if k not in ("raw_query", "contact_email", "contact_name")},
        )

        sender = AgencyEmailSender()
        sent = sender.send_to_agencies(criteria, result["search_id"])
        logger.info(f"Emails enviados: {sent}")

    return result


def _run_scrape_only():
    from scraper.idealista_scraper import IdealistaScraper
    scraper = IdealistaScraper()
    props = scraper.scrape_all_profiles()
    logger.info(f"Propiedades scrapeadas: {len(props)}")
    for p in props:
        logger.info(f"  [{p.idealista_id}] {p.title} — {p.price_text} — {p.location}")


if __name__ == "__main__":
    main()
