"""
Alta manual de agencia — para el cliente, sin pasar por WordPress.

Uso (doble clic en agregar_agencia.bat, o desde cmd):
    python -m tools.add_agency

Pide el nombre y el link del perfil de Idealista, genera el codigo corto
unico (estilo 3VCO, no revela la agencia — incidente 8), lo guarda en
configuracion/perfiles.txt y opcionalmente scrapea+publica los pisos de
esa agencia al momento (mismo camino que la aprobacion del plugin:
KIE -> optimizar -> Houzez, respetando la politica anti-copyright).
"""

import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("data/jacobo_bot.log", encoding="utf-8"),
    ],
)

from onboarding.registry import register_agency_profile, validate_idealista_profile_url


def main() -> int:
    print("=" * 60)
    print("  ALTA DE AGENCIA NUEVA")
    print("=" * 60)
    print()
    print("Deja un campo vacio y pulsa Enter para cancelar.")
    print()

    name = input("Nombre de la agencia (solo referencia interna): ").strip()
    if not name:
        print("Cancelado. No se guardo nada.")
        return 0

    while True:
        url = input("Link del perfil de Idealista: ").strip()
        if not url:
            print("Cancelado. No se guardo nada.")
            return 0
        if validate_idealista_profile_url(url):
            break
        print()
        print("  Ese link no es valido. Tiene que ser el perfil profesional")
        print("  de Idealista, por ejemplo:")
        print("      https://www.idealista.com/pro/nombre-de-la-agencia/")
        print()

    code = register_agency_profile(name, url)
    print()
    print(f"Agencia guardada con codigo interno: {code}")
    print("(Es lo que se ve en la web como ID de propiedad; no revela la agencia.)")
    print()

    answer = input("Publicar sus pisos AHORA? Tarda varios minutos y gasta creditos de fotos (s/n): ").strip().lower()
    if answer not in ("s", "si", "y", "yes"):
        print()
        print("OK. Los pisos de esta agencia saldran en el proximo ciclo del bot.")
        return 0

    print()
    print("Publicando... no cierres esta ventana hasta que termine.")
    print()
    from monitor.property_monitor import PropertyMonitor

    stats = PropertyMonitor().run_single_profile(url)
    print()
    print("=" * 60)
    print(f"  Terminado: {stats.found} propiedades encontradas, "
          f"{stats.new} publicadas, {stats.errors} errores.")
    if stats.errors:
        print("  Hubo errores: revisa data/jacobo_bot.log o vuelve a intentarlo.")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
