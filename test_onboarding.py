"""
Tests del alta automática de agencias (self-contained, sin red ni WordPress).

Cubre:
  - validate_idealista_profile_url: casos válidos + bypasses anti-SSRF
  - generate_unique_code: formato y unicidad frente a AGENCY_CODES
  - Database.agency_signups: alta / listar / aprobar / rechazar / dedupe
  - api.server: /api/onboard, /api/signups, approve/reject (auth + validación)

NO toca configuracion/perfiles.txt ni scrapea (no llama a register_agency_profile
ni a approve sobre una solicitud real con URL fetcheables).

Uso:  python test_onboarding.py
"""
import os
import re
import sys
import tempfile
from pathlib import Path

# Consola de Windows en cp1252 no codifica → / acentos; forzamos UTF-8 como en main.py
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent))

FAILS = []


def check(cond, label):
    if cond:
        print(f"  ok  {label}")
    else:
        print(f"  XX  {label}")
        FAILS.append(label)


def test_url_validation():
    print("[validate_idealista_profile_url]")
    from onboarding.registry import validate_idealista_profile_url as v
    valid = [
        "https://www.idealista.com/pro/inmo-test/",
        "https://www.idealista.com/pro/x",
        "https://idealista.com/pro/vasanco/",
    ]
    invalid = [
        "https://idealista.com.evil.com/pro/x/",   # host look-alike
        "https://evil.com/idealista./pro/x/",        # substring
        "http://idealista.com@evil.com/pro/x/",      # userinfo
        "https://www.idealista.com:8080/pro/x/",     # puerto
        "ftp://www.idealista.com/pro/x/",            # esquema
        "file:///etc/passwd",
        "https://www.idealista.com/inmueble/123/",   # path no /pro/
        "https://malingidealista.com/pro/x/",
        "",
    ]
    for u in valid:
        check(v(u) is True, f"válida: {u}")
    for u in invalid:
        check(v(u) is False, f"rechazada: {u}")


def test_code_gen():
    print("[generate_unique_code]")
    import config.settings as settings
    from onboarding.registry import generate_unique_code
    codes = {generate_unique_code() for _ in range(50)}
    check(all(re.fullmatch(r"[1-9][A-Z]{3}", c) for c in codes), "formato [1-9][A-Z]{3}")
    # Debe evitar colisiones con AGENCY_CODES existentes
    settings.AGENCY_CODES["__test_slug__"] = "1ZZZ"
    try:
        got = {generate_unique_code() for _ in range(50)}
        check("1ZZZ" not in got, "no colisiona con un código ya en uso")
    finally:
        settings.AGENCY_CODES.pop("__test_slug__", None)


def test_db_signups():
    print("[Database.agency_signups]")
    from database.db import Database
    tmp = os.path.join(tempfile.gettempdir(), "jacobo_test_onboard.db")
    for ext in ("", "-wal", "-shm"):
        if os.path.exists(tmp + ext):
            os.remove(tmp + ext)
    db = Database(tmp)
    sid = db.add_signup("Inmo X", "https://www.idealista.com/pro/inmo-x/",
                        contact_email="x@x.com", phone="600", zones=["malaga"])
    check(db.signup_url_exists("https://www.idealista.com/pro/inmo-x/") is True, "dedupe detecta URL existente")
    check(db.signup_url_exists("https://www.idealista.com/pro/otra/") is False, "dedupe ignora URL nueva")
    pend = db.list_signups("pending")
    check(len(pend) == 1 and pend[0]["name"] == "Inmo X", "list_signups(pending)")
    db.set_signup_status(sid, "approved", agency_code="3ABC")
    got = db.get_signup(sid)
    check(got["status"] == "approved" and got["agency_code"] == "3ABC", "approve fija status+code")
    check(db.list_signups("pending") == [], "aprobada sale de pendientes")
    db.set_signup_status(sid, "rejected")
    check(db.signup_url_exists("https://www.idealista.com/pro/inmo-x/") is False, "rechazada libera la URL")
    for ext in ("", "-wal", "-shm"):
        if os.path.exists(tmp + ext):
            os.remove(tmp + ext)


def test_flask():
    print("[api.server endpoints]")
    try:
        from api.server import create_app
    except ImportError as e:
        print("  --  Flask no instalado, se omite:", e)
        return
    cli = create_app(secret="t").test_client()
    check(cli.post("/api/onboard", json={"secret": "t", "name": "A", "idealista_url": "bad"}).status_code == 400,
          "onboard con URL inválida → 400")
    check(cli.post("/api/onboard", json={"name": "A", "idealista_url": "https://www.idealista.com/pro/a/"}).status_code == 401,
          "onboard sin secret → 401")
    check(cli.get("/api/signups?secret=t").status_code == 200, "list_signups con secret → 200")
    check(cli.post("/api/signups/99999999/approve?secret=t").status_code == 404, "approve inexistente → 404")


def main():
    test_url_validation()
    test_code_gen()
    test_db_signups()
    test_flask()
    print()
    if FAILS:
        print(f"FALLARON {len(FAILS)} checks:")
        for f in FAILS:
            print("  -", f)
        sys.exit(1)
    print("=== TODOS LOS TESTS DE ONBOARDING OK ===")


if __name__ == "__main__":
    main()
