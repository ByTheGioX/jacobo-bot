"""
Comprueba la configuración SMTP: prueba host/puerto, hace login y (opcional)
envía un email de prueba.

Uso:
    python -m tools.test_smtp                          # solo login con la config actual
    python -m tools.test_smtp --probe                  # prueba varias combinaciones host/puerto
    python -m tools.test_smtp --to tucorreo@gmail.com  # envía un email de prueba real

Nota: muchas conexiones domésticas bloquean los puertos 25/465/587 de salida.
Si desde el PC da timeout pero desde el VPS funciona, el problema es la red local.
"""

import argparse
import sys
from email.mime.text import MIMEText
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import (
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD,
    EMAIL_FROM, EMAIL_FROM_NAME,
)
from search.smtp_client import connect as _connect


def _try(host: str, port: int) -> bool:
    label = f"{host}:{port}"
    try:
        server = _connect(host, port, timeout=25)
        server.quit()
        print(f"  OK    {label}  -> login correcto")
        return True
    except Exception as e:
        print(f"  FALLO {label}  -> {type(e).__name__}: {str(e)[:110]}")
        return False


def main():
    ap = argparse.ArgumentParser(description="Test de configuración SMTP")
    ap.add_argument("--probe", action="store_true", help="Prueba varias combinaciones host/puerto")
    ap.add_argument("--to", help="Envía un email de prueba a esta dirección")
    args = ap.parse_args()

    domain = SMTP_USER.split("@")[-1] if "@" in SMTP_USER else ""
    print(f"Config actual: {SMTP_HOST}:{SMTP_PORT} | usuario: {SMTP_USER} | from: {EMAIL_FROM_NAME} <{EMAIL_FROM}>")
    if not SMTP_PASSWORD or SMTP_PASSWORD.startswith("tu_"):
        print("SMTP_PASSWORD sin configurar (revisa configuracion/04_email.txt — tiene prioridad sobre .env)")
        return

    candidates = [(SMTP_HOST, SMTP_PORT)]
    if args.probe:
        # 465 primero: es lo que documenta CDmon (el 25 lo tienen deshabilitado)
        for host in (SMTP_HOST, f"smtp.{domain}", f"mail.{domain}"):
            for port in (465, 587):
                if host and (host, port) not in candidates:
                    candidates.append((host, port))

    print("\nProbando conexión:")
    ok_combo = None
    for host, port in candidates:
        if _try(host, port):
            ok_combo = (host, port)
            break

    if not ok_combo:
        print("\nNinguna combinación funcionó. Posibles causas:")
        print("  - La red local bloquea los puertos SMTP de salida (probar desde el VPS)")
        print("  - Host o puerto distintos: mirar panel CDmon > Correo > Configurar cliente")
        print("  - Contraseña incorrecta (el login daría 535, no timeout)")
        return

    host, port = ok_combo
    if (host, port) != (SMTP_HOST, SMTP_PORT):
        print(f"\nActualiza configuracion/04_email.txt con SMTP_HOST={host} y SMTP_PORT={port}")

    if not args.to:
        print("\nLogin correcto. Para enviar un email de prueba: --to tucorreo@gmail.com")
        return

    msg = MIMEText("Prueba de configuración SMTP de Jacobo-Bot. Si lees esto, funciona.", "plain", "utf-8")
    msg["Subject"] = "Test SMTP — Jacobo-Bot"
    msg["From"] = f"{EMAIL_FROM_NAME} <{EMAIL_FROM}>"
    msg["To"] = args.to
    try:
        server = _connect(host, port)
        server.sendmail(EMAIL_FROM, [args.to], msg.as_string())
        server.quit()
        print(f"\nEmail de prueba enviado a {args.to}. Revisa también la carpeta de spam.")
    except Exception as e:
        print(f"\nError enviando: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
