"""
Conexión SMTP compartida por todos los envíos del bot.

El modo de cifrado depende del puerto y equivocarse no da un error claro
(da timeout o "wrong version number"), así que se decide aquí una sola vez:
  - 465 → SSL directo desde la conexión (es el que usa CDmon)
  - 587 → conexión en claro + STARTTLS
"""

import smtplib
import ssl

from config.settings import (
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_VERIFY_CERT,
)


def connect(host: str = "", port: int = 0, timeout: int = 30,
            verify: bool | None = None) -> smtplib.SMTP:
    """Devuelve una conexión SMTP ya autenticada (el llamante hace sendmail/quit).

    verify=False sigue cifrando la conexión, solo se salta la comprobación de
    que el nombre del certificado coincida con el host (ver SMTP_VERIFY_CERT).
    """
    host = host or SMTP_HOST
    port = int(port or SMTP_PORT)
    context = ssl.create_default_context()
    if not (SMTP_VERIFY_CERT if verify is None else verify):
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

    if port == 465:
        server = smtplib.SMTP_SSL(host, port, timeout=timeout, context=context)
    else:
        server = smtplib.SMTP(host, port, timeout=timeout)
        server.ehlo()
        server.starttls(context=context)
        server.ehlo()

    server.login(SMTP_USER, SMTP_PASSWORD)
    return server
