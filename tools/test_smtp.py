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
import re
import smtplib
import socket
import ssl
import sys
from email.mime.text import MIMEText
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import (
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD,
    EMAIL_FROM, EMAIL_FROM_NAME,
)
from search.smtp_client import connect as _connect


def _peer_cert(host: str, port: int, context: ssl.SSLContext, binary: bool):
    """Certificado que presenta el servidor (465 = SSL directo, resto = STARTTLS)."""
    if port == 465:
        with socket.create_connection((host, port), timeout=15) as sock:
            with context.wrap_socket(sock, server_hostname=host) as ssock:
                return ssock.getpeercert(binary_form=binary)
    server = smtplib.SMTP(host, port, timeout=15)
    try:
        server.ehlo()
        server.starttls(context=context)
        return server.sock.getpeercert(binary_form=binary)
    finally:
        try:
            server.close()
        except Exception:
            pass


def _cert_names(host: str, port: int) -> list[str]:
    """Nombres de dominio para los que ese certificado sí es válido."""
    context = ssl.create_default_context()
    context.check_hostname = False  # valida la cadena pero no el nombre: así se puede leer
    try:
        cert = _peer_cert(host, port, context, binary=False) or {}
        names = [v for typ, v in cert.get("subjectAltName", ()) if typ == "DNS"]
        names += [v for rdn in cert.get("subject", ()) for k, v in rdn if k == "commonName"]
        if names:
            return list(dict.fromkeys(names))
    except Exception:
        pass

    # La cadena tampoco valida: sacar los nombres del certificado en crudo
    try:
        context.verify_mode = ssl.CERT_NONE
        der = _peer_cert(host, port, context, binary=True) or b""
        found = re.findall(rb"[a-z0-9*][a-z0-9.*-]{4,}\.[a-z]{2,}", der.lower())
        return list(dict.fromkeys(n.decode() for n in found))
    except Exception:
        return []


def _try(host: str, port: int) -> tuple[str, list[str]]:
    """Devuelve (estado, nombres_del_certificado). Estado: ok | inseguro | fallo.

    Siempre empieza validando el nombre del certificado (verify=True explícito,
    sin mirar SMTP_VERIFY_CERT): así el estado refleja lo que de verdad hace
    falta y no lo que ya hubiera configurado el usuario.
    """
    label = f"{host}:{port}"
    try:
        server = _connect(host, port, timeout=25, verify=True)
        server.quit()
        print(f"  OK    {label}  -> login correcto (certificado validado)")
        return "ok", []
    except ssl.SSLCertVerificationError:
        print(f"  AVISO {label}  -> conecta, pero el certificado no es para este nombre")
        names = _cert_names(host, port)
        if names:
            print(f"        certificado valido para: {', '.join(names[:8])}")
        try:
            server = _connect(host, port, timeout=25, verify=False)
            server.quit()
            print(f"  OK    {label}  -> login correcto SIN verificar el certificado")
            return "inseguro", names
        except Exception as e:
            print(f"  FALLO {label}  -> sin verificar tampoco: {type(e).__name__}: {str(e)[:130]}")
            return "fallo", names
    except Exception as e:
        print(f"  FALLO {label}  -> {type(e).__name__}: {str(e)[:130]}")
        return "fallo", []


def main():
    ap = argparse.ArgumentParser(description="Test de configuración SMTP")
    ap.add_argument("--probe", action="store_true", help="Prueba varias combinaciones host/puerto")
    ap.add_argument("--to", help="Envía un email de prueba a esta dirección")
    ap.add_argument("--firma", action="store_true",
                    help="Con --to: manda el correo real a agencias (con la firma) en vez del texto suelto")
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
    ok_combo = None       # (host, port, verifica_certificado)
    cert_names: list[str] = []
    for host, port in candidates:
        estado, names = _try(host, port)
        cert_names += [n for n in names if n not in cert_names]
        if estado == "ok":
            ok_combo = (host, port, True)
            break
        if estado == "inseguro" and not ok_combo:
            ok_combo = (host, port, False)  # plan B: seguimos buscando uno limpio

    # Plan A: si el certificado es de otro nombre, probar ESE nombre con validación
    if ok_combo and not ok_combo[2] and cert_names:
        print("\nProbando los nombres del certificado (para no desactivar la validación):")
        probados = {(h, p) for h, p in candidates}
        for name in cert_names[:5]:
            if "*" in name:
                continue
            for port in (465, 587):
                if (name, port) in probados:
                    continue
                estado, _ = _try(name, port)
                if estado == "ok":
                    ok_combo = (name, port, True)
                    break
            if ok_combo[2]:
                break

    if not ok_combo:
        print("\nNinguna combinación funcionó. Posibles causas:")
        print("  - La red local bloquea los puertos SMTP de salida (probar desde el VPS)")
        print("  - Host o puerto distintos: mirar panel CDmon > Correo > Configurar cliente")
        print("  - Contraseña incorrecta (el login daría 535, no timeout)")
        return

    host, port, verified = ok_combo
    print("\n--- CONFIGURACION QUE FUNCIONA ---")
    print(f"SMTP_HOST={host}")
    print(f"SMTP_PORT={port}")
    if not verified:
        print("SMTP_VERIFY_CERT=0")
        print("  (el servidor presenta el certificado del cluster del hosting, no uno")
        print("   a nombre de este host; se sigue cifrando y validando la cadena, solo")
        print("   se omite la coincidencia del nombre)")
    if (host, port) != (SMTP_HOST, SMTP_PORT) or not verified:
        print("Copia estas líneas en configuracion/04_email.txt")

    if not args.to:
        print("\nLogin correcto. Para enviar un email de prueba: --to tucorreo@gmail.com")
        return

    if args.firma:
        # Correo real que reciben las agencias, con datos de ejemplo
        from search.smart_search import SearchCriteria
        from search.email_sender import _build_email_body

        criteria = SearchCriteria(
            raw_query="Piso de 3 dormitorios en Rincón de la Victoria hasta 450.000",
            location="Málaga Este, Rincón de la Victoria",
            property_type="piso", operation="sale", rooms_min=3, price_max=450000,
            contact_name="Cliente de ejemplo", contact_email="cliente@ejemplo.com",
        )
        asunto, cuerpo = _build_email_body("Inmobiliaria Colaboradora", criteria, EMAIL_FROM_NAME)
        msg = MIMEText(cuerpo, "html", "utf-8")
        msg["Subject"] = f"[PRUEBA] {asunto}"
        print(f"\nEnviando el correo real a agencias ({len(cuerpo.encode()) / 1024:.1f} KB)")
        if "<img" not in cuerpo:
            print("Aviso: la firma va sin imágenes. Corre antes: python -m tools.upload_email_assets --apply")
    else:
        msg = MIMEText("Prueba de configuración SMTP de Jacobo-Bot. Si lees esto, funciona.", "plain", "utf-8")
        msg["Subject"] = "Test SMTP — Jacobo-Bot"
    msg["From"] = f"{EMAIL_FROM_NAME} <{EMAIL_FROM}>"
    msg["To"] = args.to
    try:
        server = _connect(host, port, verify=verified)
        server.sendmail(EMAIL_FROM, [args.to], msg.as_string())
        server.quit()
        print(f"\nEmail de prueba enviado a {args.to}. Revisa también la carpeta de spam.")
    except Exception as e:
        print(f"\nError enviando: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
