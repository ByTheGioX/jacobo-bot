"""
Conexión SMTP compartida por todos los envíos del bot.

El modo de cifrado depende del puerto y equivocarse no da un error claro
(da timeout o "wrong version number"), así que se decide aquí una sola vez:
  - 465 → SSL directo desde la conexión (es el que usa CDmon)
  - 587 → conexión en claro + STARTTLS
"""

import smtplib
import ssl

from config.settings import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD


def connect(host: str = "", port: int = 0, timeout: int = 30) -> smtplib.SMTP:
    """Devuelve una conexión SMTP ya autenticada (el llamante hace sendmail/quit)."""
    host = host or SMTP_HOST
    port = int(port or SMTP_PORT)
    context = ssl.create_default_context()

    if port == 465:
        server = smtplib.SMTP_SSL(host, port, timeout=timeout, context=context)
    else:
        server = smtplib.SMTP(host, port, timeout=timeout)
        server.ehlo()
        server.starttls(context=context)
        server.ehlo()

    server.login(SMTP_USER, SMTP_PASSWORD)
    return server
