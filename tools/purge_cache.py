"""
Purga el cache del WordPress de inmo4you.

Intenta los plugins de cache más comunes (LiteSpeed, WP Rocket, W3 Total Cache,
WP Super Cache, WP Fastest Cache, Cache Enabler). Best-effort: imprime cuáles
respondieron OK.

Uso:
    python -m tools.purge_cache

Si ningún plugin responde, el cache es probablemente del servidor (CDmon).
En ese caso, hay que limpiarlo manualmente desde:
    Panel CDmon -> Hostings -> Gestionar -> Servidor -> Gestionar Cache
"""

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from wordpress.wp_client import WPClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def main():
    wp = WPClient()
    logger.info("Intentando purgar cache vía plugins comunes...")
    purged = wp.purge_all_cache()
    print()
    if purged:
        print("Cache purgado vía:")
        for name in purged:
            print(f"  - {name}")
        print()
        print("Recarga https://www.inmo4you.com/listing-v6-full-width/ y verifica que aparecen las propiedades.")
    else:
        print("Ningún plugin de cache de WP respondió.")
        print()
        print("Probablemente el cache es del servidor CDmon. Limpiá manualmente:")
        print("  Panel CDmon -> Hostings -> Gestionar -> Servidor -> Gestionar Cache -> Limpiar")


if __name__ == "__main__":
    main()
