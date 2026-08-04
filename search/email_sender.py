"""
Envío automático de emails a agencias colaboradoras cuando un comprador
no encuentra lo que busca en el listing actual.

Usa SMTP del hosting (sin dependencias de SendGrid/Mailgun, etc.)
"""

import json as _json
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape
from typing import Optional

from config.settings import (
    EMAIL_FROM, EMAIL_FROM_NAME, COLLABORATING_AGENCIES,
)
from database.db import Database
from search.email_signature import render as render_signature
from search.smtp_client import connect as smtp_connect
from search.smart_search import SearchCriteria, _norm

logger = logging.getLogger(__name__)

# Mismos colores de marca que search/email_signature.py (extraídos de la
# plantilla del cliente), para que estos correos no desentonen con la firma.
_VERDE = "#183c3a"
_ORO = "#98733b"


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
    # Sin saltos de línea: la location viaja a un header de email (Subject),
    # un \r\n ahí podría inyectar headers/destinatarios extra.
    location_line = (criteria.location or "tu zona").replace("\r", " ").replace("\n", " ")
    subject = f"Cliente busca propiedad en {location_line} — {our_agency_name}"

    criteria_text = escape(_format_criteria_text(criteria))
    contact_info = ""
    if criteria.contact_name or criteria.contact_email:
        contact_info = f"""
<p><strong>Datos de contacto del cliente:</strong><br>
{"Nombre: " + escape(criteria.contact_name) + "<br>" if criteria.contact_name else ""}
{"Email: " + escape(criteria.contact_email) if criteria.contact_email else ""}
</p>"""

    body = f"""
<!DOCTYPE html>
<html lang="es">
<head><meta charset="UTF-8"></head>
<body style="font-family: Arial, sans-serif; color: #333; max-width: 600px; margin: 0 auto;">

<h2 style="color: {_VERDE};">Oportunidad de colaboración — {escape(our_agency_name)}</h2>

<p>Estimado equipo de <strong>{escape(agency_name)}</strong>,</p>

<p>Nos ponemos en contacto desde <strong>{escape(our_agency_name)}</strong> porque tenemos un cliente
interesado en la <strong>{op}</strong> de una propiedad con las siguientes características:</p>

<div style="background: #f7fafc; border-left: 4px solid {_ORO}; padding: 12px 16px; margin: 16px 0;">
<pre style="font-family: inherit; margin: 0; white-space: pre-wrap;">{criteria_text}</pre>
</div>

<p>Si tenéis en cartera alguna propiedad que encaje con estos parámetros,
os agradeceríamos que nos enviaseis la información a <a href="mailto:{EMAIL_FROM}">{EMAIL_FROM}</a>.</p>

{contact_info}

<p>Quedamos a vuestra disposición para cualquier consulta.</p>

<p style="margin-top: 24px;">Un cordial saludo,</p>

{render_signature()}

</body>
</html>
"""
    return subject, body


def _build_buyer_confirmation(criteria: SearchCriteria, our_agency_name: str) -> tuple[str, str]:
    """Devuelve (subject, body_html) del email de confirmación al comprador."""
    op = "alquiler" if criteria.operation == "rent" else "compra"
    subject = f"Hemos recibido tu búsqueda — {our_agency_name}"
    criteria_text = escape(_format_criteria_text(criteria))
    saludo = f"Hola {escape(criteria.contact_name)}," if criteria.contact_name else "Hola,"

    body = f"""
<!DOCTYPE html>
<html lang="es">
<head><meta charset="UTF-8"></head>
<body style="font-family: Arial, sans-serif; color: #333; max-width: 600px; margin: 0 auto;">

<h2 style="color: {_VERDE};">Tu búsqueda está en proceso</h2>

<p>{saludo}</p>

<p>Hemos recibido tu solicitud de <strong>{op}</strong> con estas características:</p>

<div style="background: #f7fafc; border-left: 4px solid {_ORO}; padding: 12px 16px; margin: 16px 0;">
<pre style="font-family: inherit; margin: 0; white-space: pre-wrap;">{criteria_text}</pre>
</div>

<p>La estamos comparando con nuestro inventario y con nuestras agencias colaboradoras.
En cuanto tengamos novedades, te contactaremos a este mismo correo.</p>

<p style="margin-top: 24px;">Un cordial saludo,</p>

{render_signature()}

</body>
</html>
"""
    return subject, body


def _send_with_copy(to_email: str, to_display: str, subject: str, body_html: str, from_name: str) -> bool:
    """Manda un email HTML a to_email, dejando copia en EMAIL_FROM.

    La copia va como destinatario extra del sobre SMTP, no como header Bcc,
    para que to_email no la vea en el correo. Un rechazo puntual de to_email
    (ej. 554 de su servidor) cuenta como fallo aunque la copia sí haya
    entrado — sendmail() no lanza excepción si solo falla uno de los dos.
    """
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{EMAIL_FROM}>"
    msg["To"] = to_display
    msg["Reply-To"] = EMAIL_FROM
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    try:
        with smtp_connect() as server:
            refused = server.sendmail(EMAIL_FROM, [to_email, EMAIL_FROM], msg.as_string())
    except Exception as e:
        logger.error(f"Error enviando email a {to_email}: {e}")
        return False

    if to_email in refused:
        logger.error(f"Error enviando email a {to_email}: {refused[to_email]}")
        return False
    logger.info(f"Email enviado a {to_email} — copia a {EMAIL_FROM}")
    return True


def send_buyer_confirmation(criteria: SearchCriteria, our_agency_name: str = EMAIL_FROM_NAME) -> bool:
    """Avisa al comprador que su búsqueda quedó registrada. Se llama siempre que
    deja un email en el formulario, haya o no coincidencias en el inventario."""
    if not criteria.contact_email:
        return False
    subject, body_html = _build_buyer_confirmation(criteria, our_agency_name)
    return _send_with_copy(criteria.contact_email, criteria.contact_email, subject, body_html, our_agency_name)


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

    loc = _norm(location)
    return any(_norm(z) in loc or loc in _norm(z) for z in zones)


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
        return _send_with_copy(to_email, f"{to_name} <{to_email}>", subject, body_html, our_agency_name)
