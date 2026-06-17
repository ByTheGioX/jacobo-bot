"""
Envía por email el link de alta ("Únete a la red") a un listado de agencias.

Cada agencia abre el link, rellena sus datos + su perfil de Idealista, y el alta
queda pendiente de tu aprobación en Ajustes → Agencias Colaboradoras.

Fuentes de destinatarios (elige una):
  - Por defecto: las agencias colaboradoras ya en la BD (collaborating_agencies).
  - --csv ruta.csv : un CSV con columnas 'name,email' (una por fila, con o sin cabecera).

Uso:
    python -m tools.send_onboarding_invites --link "https://tuweb.com/unete"            # dry-run
    python -m tools.send_onboarding_invites --link "https://tuweb.com/unete" --apply     # envía
    python -m tools.send_onboarding_invites --link "..." --apply --limit 10              # por tandas
    python -m tools.send_onboarding_invites --link "..." --csv agencias.csv --apply

Reutiliza la config SMTP de config.settings (la misma que usa search/email_sender.py).
SIEMPRE correr primero sin --apply (dry-run) para revisar destinatarios y el link.
"""

import argparse
import csv
import logging
import smtplib
import sys
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config.settings import (
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD,
    EMAIL_FROM, EMAIL_FROM_NAME,
)
from database.db import Database

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def _build_body(agency_name: str, link: str) -> tuple[str, str]:
    subject = f"Únete a nuestra red de inmobiliarias — {EMAIL_FROM_NAME}"
    safe_name = agency_name or "equipo"
    body = f"""\
<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;color:#333;max-width:600px;margin:0 auto">
<h2 style="color:#2c5282">Únete a nuestra red de inmobiliarias</h2>
<p>Hola <strong>{safe_name}</strong>,</p>
<p>Estamos ampliando nuestra red de inmobiliarias colaboradoras. Al unirte, tus
propiedades se publican automáticamente en nuestra web y recibes las búsquedas de
compradores de tu zona.</p>
<p>Solo tienes que rellenar tus datos y tu perfil de Idealista aquí:</p>
<p style="text-align:center;margin:28px 0">
  <a href="{link}" style="background:#2c5282;color:#fff;text-decoration:none;
     padding:14px 32px;border-radius:6px;font-weight:600">Unirme a la red</a>
</p>
<p style="font-size:13px;color:#718096">Si el botón no funciona, copia este enlace:
<br>{link}</p>
<hr style="border:none;border-top:1px solid #e2e8f0;margin-top:28px">
<p style="font-size:11px;color:#718096">Un cordial saludo,<br>{EMAIL_FROM_NAME}</p>
</body></html>"""
    return subject, body


def _send(to_email: str, to_name: str, link: str) -> bool:
    subject, body_html = _build_body(to_name, link)
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{EMAIL_FROM_NAME} <{EMAIL_FROM}>"
    msg["To"] = f"{to_name} <{to_email}>" if to_name else to_email
    msg["Reply-To"] = EMAIL_FROM
    msg.attach(MIMEText(body_html, "html", "utf-8"))
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.sendmail(EMAIL_FROM, [to_email], msg.as_string())
        logger.info("Enviado a %s (%s)", to_email, to_name or "—")
        return True
    except Exception as e:
        logger.error("Error enviando a %s: %s", to_email, e)
        return False


def _load_from_csv(path: str) -> list[dict]:
    out = []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        for row in reader:
            if not row:
                continue
            cells = [c.strip() for c in row]
            # Detecta cabecera
            if cells[0].lower() in ("name", "nombre") or "@" not in " ".join(cells):
                if cells[0].lower() in ("name", "nombre"):
                    continue
            # name,email  ó  email solo
            if len(cells) >= 2 and "@" in cells[1]:
                out.append({"name": cells[0], "email": cells[1]})
            elif "@" in cells[0]:
                out.append({"name": "", "email": cells[0]})
    return out


def main():
    ap = argparse.ArgumentParser(description="Envía el link de alta a un listado de agencias")
    ap.add_argument("--link", required=True, help="URL de la página de alta (con [jacobo_onboarding_form])")
    ap.add_argument("--csv", help="CSV de destinatarios (name,email). Si se omite, usa las agencias de la BD")
    ap.add_argument("--apply", action="store_true", help="Envía de verdad (sin esto: dry-run)")
    ap.add_argument("--limit", type=int, default=0, help="Limita a N destinatarios (0 = todos)")
    ap.add_argument("--sleep", type=float, default=2.0, help="Segundos entre envíos (def: 2)")
    args = ap.parse_args()

    if args.csv:
        recipients = _load_from_csv(args.csv)
    else:
        recipients = [
            {"name": a.get("name", ""), "email": a.get("email", "")}
            for a in Database().get_all_agencies()
            if a.get("email")
        ]

    if args.limit:
        recipients = recipients[: args.limit]

    if not recipients:
        logger.warning("No hay destinatarios. Usa --csv o añade agencias a la BD.")
        return

    logger.info("Destinatarios: %d | Link: %s | Modo: %s",
                len(recipients), args.link, "ENVÍO REAL" if args.apply else "DRY-RUN")

    if not args.apply:
        for r in recipients:
            logger.info("  [dry-run] %s <%s>", r["name"] or "—", r["email"])
        logger.info("Dry-run completado. Repite con --apply para enviar.")
        return

    sent = 0
    for i, r in enumerate(recipients):
        if _send(r["email"], r["name"], args.link):
            sent += 1
        if i < len(recipients) - 1:
            time.sleep(args.sleep)
    logger.info("Enviados %d/%d emails de invitación.", sent, len(recipients))


if __name__ == "__main__":
    main()
