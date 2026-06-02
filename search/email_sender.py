"""
Envío automático de emails a agencias colaboradoras cuando un comprador
no encuentra lo que busca en el listing actual.

Usa SMTP del hosting (sin dependencias de SendGrid/Mailgun, etc.)
"""

import json as _json
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from config.settings import (
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD,
    EMAIL_FROM, EMAIL_FROM_NAME, COLLABORATING_AGENCIES,
)
from database.db import Database
from search.smart_search import SearchCriteria

logger = logging.getLogger(__name__)


def _format_criteria_text(criteria: SearchCriteria) -> str:
    lines = []
    if criteria.location:
        lines.append(f"  - Zona/ciudad: {criteria.location}")
    if criteria.property_type:
        lines.append(f"  - Tipo: {criteria.property_type}")
    if criteria.rooms_min:
        lines.append(f"  - Dormitorios: mínimo {criteria.rooms_min}")
    if criteria.price_max:
        lines.append(f"  - Precio máximo: {criteria.price_max:,} €")
    if criteria.area_min:
        lines.append(f"  - Superficie mínima: {criteria.area_min} m²")
    if criteria.has_parking:
        lines.append("  - Con plaza de parking")
    if criteria.has_pool:
        lines.append("  - Con piscina")
    if criteria.has_terrace:
        lines.append("  - Con terraza/balcón")
    op = "alquiler" if criteria.operation == "rent" else "compra"
    lines.insert(0, f"  - Operación: {op}")
    return "\n".join(lines) if lines else "  (Sin criterios específicos)"


def _build_email_body(
    agency_name: str,
    criteria: SearchCriteria,
    our_agency_name: str,
) -> tuple[str, str]:
    """Devuelve (subject, body_html)."""
    op = "alquiler" if criteria.operation == "rent" else "compra"
    subject = f"Cliente busca propiedad en {criteria.location or 'tu zona'} — {our_agency_name}"

    criteria_text = _format_criteria_text(criteria)
    contact_info = ""
    if criteria.contact_name or criteria.contact_email:
        contact_info = f"""
<p><strong>Datos de contacto del cliente:</strong><br>
{"Nombre: " + criteria.contact_name + "<br>" if criteria.contact_name else ""}
{"Email: " + criteria.contact_email if criteria.contact_email else ""}
</p>"""

    body = f"""
<!DOCTYPE html>
<html lang="es">
<head><meta charset="UTF-8"></head>
<body style="font-family: Arial, sans-serif; color: #333; max-width: 600px; margin: 0 auto;">

<h2 style="color: #2c5282;">Oportunidad de colaboración — {our_agency_name}</h2>

<p>Estimado equipo de <strong>{agency_name}</strong>,</p>

<p>Nos ponemos en contacto desde <strong>{our_agency_name}</strong> porque tenemos un cliente
interesado en la <strong>{op}</strong> de una propiedad con las siguientes características:</p>

<div style="background: #f7fafc; border-left: 4px solid #4299e1; padding: 12px 16px; margin: 16px 0;">
<pre style="font-family: inherit; margin: 0; white-space: pre-wrap;">{criteria_text}</pre>
</div>

<p>Si tenéis en cartera alguna propiedad que encaje con estos parámetros,
os agradeceríamos que nos enviaseis la información a <a href="mailto:{EMAIL_FROM}">{EMAIL_FROM}</a>.</p>

{contact_info}

<p>Quedamos a vuestra disposición para cualquier consulta.</p>

<p style="margin-top: 24px;">
Un cordial saludo,<br>
<strong>{our_agency_name}</strong><br>
<a href="mailto:{EMAIL_FROM}">{EMAIL_FROM}</a>
</p>

<hr style="border: none; border-top: 1px solid #e2e8f0; margin-top: 32px;">
<p style="font-size: 11px; color: #718096;">
Este email se ha generado automáticamente. Si no desea recibir más comunicaciones,
por favor responda indicándolo.
</p>

</body>
</html>
"""
    return subject, body


def _agency_matches_zone(agency: dict, location: str) -> bool:
    """
    True si la agencia debe recibir la búsqueda según su zona.
    Si la agencia no tiene zonas configuradas → recibe siempre.
    Si tiene zonas → solo si la location del comprador coincide (substring bidireccional).
    """
    zones_raw = agency.get("zones") or ""
    if isinstance(zones_raw, list):
        zones = [z.strip() for z in zones_raw if z.strip()]
    elif zones_raw.strip().startswith("["):
        try:
            zones = _json.loads(zones_raw)
        except Exception:
            zones = []
    else:
        zones = [z.strip() for z in zones_raw.split(",") if z.strip()]

    if not zones:
        return True

    loc = location.lower()
    return any(z.lower() in loc or loc in z.lower() for z in zones)


class AgencyEmailSender:
    def __init__(self):
        self.db = Database()
        self.db.sync_agencies(COLLABORATING_AGENCIES)

    def send_to_agencies(
        self,
        criteria: SearchCriteria,
        search_id: int,
        our_agency_name: str = EMAIL_FROM_NAME,
    ) -> int:
        """
        Envía email a todas las agencias colaboradoras activas.
        Retorna el número de emails enviados correctamente.
        """
        agencies = self.db.get_active_agencies()
        if not agencies:
            logger.warning("No hay agencias colaboradoras configuradas.")
            return 0

        sent = 0
        buyer_location = criteria.location or ""
        for agency in agencies:
            if not _agency_matches_zone(agency, buyer_location):
                logger.info(
                    "Agencia '%s' omitida (zona no coincide con '%s')",
                    agency["name"], buyer_location,
                )
                continue
            ok = self._send_email(
                to_email=agency["email"],
                to_name=agency["name"],
                criteria=criteria,
                our_agency_name=our_agency_name,
            )
            if ok:
                sent += 1

        self.db.mark_emails_sent(search_id, sent)
        logger.info(f"Emails enviados a {sent}/{len(agencies)} agencias para búsqueda #{search_id}")
        return sent

    def _send_email(
        self,
        to_email: str,
        to_name: str,
        criteria: SearchCriteria,
        our_agency_name: str,
    ) -> bool:
        subject, body_html = _build_email_body(to_name, criteria, our_agency_name)

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"{our_agency_name} <{EMAIL_FROM}>"
        msg["To"] = f"{to_name} <{to_email}>"
        msg["Reply-To"] = EMAIL_FROM

        msg.attach(MIMEText(body_html, "html", "utf-8"))

        try:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
                server.ehlo()
                server.starttls()
                server.login(SMTP_USER, SMTP_PASSWORD)
                server.sendmail(EMAIL_FROM, [to_email], msg.as_string())
            logger.info(f"Email enviado a {to_email} ({to_name})")
            return True
        except Exception as e:
            logger.error(f"Error enviando email a {to_email}: {e}")
            return False
