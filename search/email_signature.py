"""
Bloque de firma (logo + datos de contacto) que se añade al final de los correos.

El diseño y los colores salen de la plantilla del cliente
("Firma correo/mailing_inmo4you_completo_yolanda_cuenca.html").

Dos reglas que no se pueden saltar en HTML de email:
  - Imágenes por URL pública, nunca en base64: Outlook no pinta los data: URI y
    la foto incrustada dejaba el correo en 2,5 MB (Gmail recorta desde 102 KB).
  - Maquetación con <table> y estilos inline: los clientes de correo ignoran
    las hojas de estilo y buena parte de flex/grid.
"""

from html import escape

from config.settings import (
    EMAIL_FROM, EMAIL_FROM_NAME,
    FIRMA_NOMBRE, FIRMA_CARGO, FIRMA_TELEFONO, FIRMA_WEB, FIRMA_ZONA,
    FIRMA_FOTO_URL, FIRMA_LOGO_URL,
)

_VERDE = "#183c3a"
_ORO = "#98733b"
_ORO_CLARO = "#b18a4a"
_TEXTO = "#555b57"
_SEPARADOR = "#e6e2da"
_GRIS = "#8a8781"
_FONDO_LOGO = "#FAD9C8"

_AVISO_LEGAL = (
    "Este mensaje se dirige exclusivamente a profesionales del sector inmobiliario "
    "con fines de colaboración comercial."
)


def _foto_html() -> str:
    if not FIRMA_FOTO_URL:
        return ""
    return (
        f'<td width="82" valign="top" style="width:82px;">'
        f'<img src="{escape(FIRMA_FOTO_URL, quote=True)}" width="66" height="66" '
        f'alt="{escape(FIRMA_NOMBRE or EMAIL_FROM_NAME)}" '
        f'style="width:66px;height:66px;border-radius:50%;object-fit:cover;'
        f'background:#e8e3d9;display:block;border:0;"></td>'
    )


def _logo_html() -> str:
    if not FIRMA_LOGO_URL:
        return ""
    return (
        f'<tr><td style="background:{_FONDO_LOGO};border-radius:10px;padding:16px 24px;'
        f'text-align:center;">'
        f'<img src="{escape(FIRMA_LOGO_URL, quote=True)}" width="150" '
        f'alt="{escape(EMAIL_FROM_NAME)}" '
        f'style="width:150px;max-width:100%;height:auto;border:0;display:inline-block;">'
        f'</td></tr>'
    )


def render(aviso_legal: bool = True) -> str:
    """Devuelve el HTML de la firma. Vacío si no hay ningún dato configurado."""
    nombre = FIRMA_NOMBRE or EMAIL_FROM_NAME
    if not nombre:
        return ""

    cargo = " · ".join(x for x in (FIRMA_CARGO, EMAIL_FROM_NAME) if x)

    contacto = []
    if FIRMA_TELEFONO:
        tel_link = FIRMA_TELEFONO.replace(" ", "")
        contacto.append(
            f'<a href="tel:{escape(tel_link, quote=True)}" '
            f'style="color:{_TEXTO};text-decoration:none;">{escape(FIRMA_TELEFONO)}</a>'
        )
    if EMAIL_FROM:
        contacto.append(
            f'<a href="mailto:{escape(EMAIL_FROM, quote=True)}" '
            f'style="color:{_TEXTO};text-decoration:none;">{escape(EMAIL_FROM)}</a>'
        )

    segunda_linea = []
    if FIRMA_WEB:
        web_texto = FIRMA_WEB.replace("https://", "").replace("http://", "").rstrip("/")
        segunda_linea.append(
            f'<a href="{escape(FIRMA_WEB, quote=True)}" '
            f'style="color:{_ORO_CLARO};text-decoration:none;font-weight:bold;">'
            f'{escape(web_texto)}</a>'
        )
    if FIRMA_ZONA:
        segunda_linea.append(escape(FIRMA_ZONA))

    lineas = " &nbsp;·&nbsp; ".join(contacto)
    if segunda_linea:
        lineas += "<br>" + " &nbsp;·&nbsp; ".join(segunda_linea)

    legal = ""
    if aviso_legal:
        legal = (
            f'<tr><td style="padding-top:18px;text-align:center;font-size:11px;'
            f'line-height:17px;color:{_GRIS};">{_AVISO_LEGAL}</td></tr>'
        )

    return f"""
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"
       style="max-width:620px;margin:32px auto 0;border-top:1px solid {_SEPARADOR};
              font-family:Arial,Helvetica,sans-serif;">
  <tr>
    <td style="padding-top:24px;">
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
        <tr>
          {_foto_html()}
          <td valign="top">
            <p style="margin:0 0 3px;font-size:17px;line-height:22px;color:{_VERDE};
                      font-weight:bold;">{escape(nombre)}</p>
            <p style="margin:0 0 10px;font-size:13px;line-height:19px;color:{_ORO};
                      font-weight:bold;">{escape(cargo)}</p>
            <p style="margin:0;font-size:13px;line-height:21px;color:{_TEXTO};">{lineas}</p>
          </td>
        </tr>
      </table>
    </td>
  </tr>
  {_logo_html()}
  {legal}
</table>"""
