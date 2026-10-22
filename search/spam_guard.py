"""
Filtro anti-spam para la búsqueda pública del Home.

Los bots usan el formulario como relé de spam: ponen el email de la víctima en
"email" y el texto basura (enlaces, cirílico, "Вам перевод 129437 руб...") en
"name". El bot respondía con la confirmación "Hola <name>," desde
info@inmo4you.com, así que el spam salía firmado por la inmobiliaria.

Este módulo decide si una búsqueda es spam (se descarta en silencio) y si se
puede mandar la confirmación al comprador (límite por email y global por hora).
"""

import re

MAX_QUERY_LEN = 1000
MAX_NAME_LEN = 60
MAX_EMAIL_LEN = 200

_URL_RE = re.compile(
    r"(https?://|www\.|t\.me/|\b[\w-]+\.(ru|su|xyz|top|click|link|online|site|io|ly|cc|me|info|biz)\b)",
    re.IGNORECASE,
)
# Cirílico, CJK, árabe… — el formulario es para compradores en España.
_FOREIGN_SCRIPT_RE = re.compile(r"[Ѐ-ӿ؀-ۿ぀-ヿ一-鿿]")
_SPAM_WORDS_RE = re.compile(
    r"(руб|перевод|bitcoin|btc|crypto|casino|viagra|telegram|loan|"
    r"transfer|payout|withdraw|bonus|prize|ganador|premio|USDT)",
    re.IGNORECASE,
)
# Un nombre de persona: letras (con tildes), espacios, punto, guion, apóstrofo.
_NAME_RE = re.compile(r"^[A-Za-zÀ-ÖØ-öø-ÿ' .\-]*$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


def _has_junk(text: str) -> bool:
    return bool(
        _URL_RE.search(text)
        or _FOREIGN_SCRIPT_RE.search(text)
        or _SPAM_WORDS_RE.search(text)
    )


def is_valid_name(name: str) -> bool:
    name = name.strip()
    return len(name) <= MAX_NAME_LEN and bool(_NAME_RE.match(name)) and not _has_junk(name)


def is_spam_search(query: str, name: str, email: str) -> bool:
    """True si la búsqueda debe descartarse sin procesar ni enviar correos."""
    if len(query) > MAX_QUERY_LEN or len(email) > MAX_EMAIL_LEN:
        return True
    if name and not is_valid_name(name):
        return True
    if email and not _EMAIL_RE.match(email):
        return True
    return _has_junk(query)
