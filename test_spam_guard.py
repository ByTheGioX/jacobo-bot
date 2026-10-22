"""
Tests del filtro anti-spam de la búsqueda del Home (self-contained, sin red ni SMTP).

Cubre:
  - spam_guard.is_spam_search: búsquedas reales pasan, relés de spam no
  - Database.count_searches_*: conteos por email y globales
  - api.server._buyer_confirmation_allowed: 1 confirmación/email/24 h y tope por hora
  - api.server /api/search: el spam responde igual pero no se procesa

Uso:  python test_spam_guard.py
"""
import os
import sys
import tempfile
import time
from pathlib import Path

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


def _tmp_db_path(name: str) -> str:
    tmp = os.path.join(tempfile.gettempdir(), name)
    for ext in ("", "-wal", "-shm"):
        if os.path.exists(tmp + ext):
            os.remove(tmp + ext)
    return tmp


def test_spam_guard():
    print("[spam_guard.is_spam_search]")
    from search.spam_guard import is_spam_search as spam
    legit = [
        ("piso de 2 habitaciones en Málaga con terraza hasta 200.000 €", "María José", "mj@gmail.com"),
        ("chalet en Rincón de la Victoria", "", ""),
        ("ático en Pedregalejo, alquiler", "O'Neill-Pérez", "a.b@empresa.es"),
        ("casa con piscina zona Marbella", "Jean-Luc", ""),
    ]
    abuse = [
        ("casa", "Вам перевод 129437 руб. получить", "victima@mail.ru"),
        ("casa", "Hola! Get your bonus https://bit.ly/xyz", "x@y.com"),
        ("casa", "Ganador www.premio.top", "x@y.com"),
        ("visit https://spam.example/promo", "Ana", "ana@x.com"),
        ("casa", "A" * 61, "x@y.com"),
        ("casa", "Ana", "no-es-un-email"),
        ("x" * 1001, "", ""),
        ("crypto payout USDT", "", ""),
    ]
    for q, n, e in legit:
        check(spam(q, n, e) is False, f"legítima pasa: {n or '(sin nombre)'} / {q[:30]}")
    for q, n, e in abuse:
        check(spam(q, n, e) is True, f"spam bloqueado: {n[:30]} / {q[:30]}")


def test_db_counts():
    print("[Database.count_searches_*]")
    from database.db import Database
    tmp = _tmp_db_path("jacobo_test_spam_counts.db")
    db = Database(tmp)
    db.save_buyer_search("piso", {}, contact_email="A@x.com")
    db.save_buyer_search("casa", {}, contact_email="a@x.com")
    db.save_buyer_search("chalet", {}, contact_email="b@x.com")
    db.save_buyer_search("local", {}, contact_email="")
    check(db.count_searches_by_email("a@x.com", 24) == 2, "cuenta por email sin distinguir mayúsculas")
    check(db.count_searches_by_email("c@x.com", 24) == 0, "email nuevo → 0")
    check(db.count_searches_with_email(1) == 3, "global ignora búsquedas sin email")
    _tmp_db_path("jacobo_test_spam_counts.db")


def test_confirmation_limits():
    print("[api.server._buyer_confirmation_allowed]")
    try:
        import api.server as server
    except ImportError as e:
        print("  --  Flask no instalado, se omite:", e)
        return
    import config.settings as settings
    import database.db as dbmod

    tmp = _tmp_db_path("jacobo_test_spam_confirm.db")
    real_db = dbmod.Database
    dbmod.Database = lambda: real_db(tmp)
    old = (settings.BUYER_CONFIRMATION_ENABLED, settings.BUYER_CONFIRM_MAX_PER_HOUR)
    try:
        db = real_db(tmp)
        db.save_buyer_search("piso", {}, contact_email="a@x.com")
        check(server._buyer_confirmation_allowed("a@x.com") is True, "primera búsqueda del email → confirma")
        db.save_buyer_search("piso", {}, contact_email="a@x.com")
        check(server._buyer_confirmation_allowed("a@x.com") is False, "segunda en 24 h → no confirma")

        settings.BUYER_CONFIRM_MAX_PER_HOUR = 2
        db.save_buyer_search("piso", {}, contact_email="c@x.com")
        check(server._buyer_confirmation_allowed("c@x.com") is False, "tope global por hora → no confirma")

        settings.BUYER_CONFIRM_MAX_PER_HOUR = 100
        settings.BUYER_CONFIRMATION_ENABLED = False
        db.save_buyer_search("piso", {}, contact_email="d@x.com")
        check(server._buyer_confirmation_allowed("d@x.com") is False, "BUYER_CONFIRMATION_ENABLED=0 → no confirma")
    finally:
        settings.BUYER_CONFIRMATION_ENABLED, settings.BUYER_CONFIRM_MAX_PER_HOUR = old
        dbmod.Database = real_db
        _tmp_db_path("jacobo_test_spam_confirm.db")


def test_search_endpoint():
    print("[api.server /api/search]")
    try:
        import api.server as server
    except ImportError as e:
        print("  --  Flask no instalado, se omite:", e)
        return
    calls = []
    real_async = server._process_search_async
    server._process_search_async = lambda *a: calls.append(a)
    try:
        cli = server.create_app(secret="t").test_client()
        hdr = {"X-API-Secret": "t"}
        spam = {"query": "casa", "name": "Вам перевод 129437 руб.", "email": "v@mail.ru"}

        r = cli.post("/api/search", json=spam, headers=hdr)
        check(r.status_code == 202 and r.get_json() == {"status": "received"}, "spam async → 202 normal")
        r = cli.post("/api/search", json={**spam, "sync": True}, headers=hdr)
        check(r.status_code == 200 and r.get_json()["matches"] == [], "spam sync → 200 sin resultados")
        check(calls == [], "spam no se procesa (sin IA ni correos)")

        cli.post("/api/search", json={"query": "piso en Málaga", "name": "Ana", "email": "ana@x.com"}, headers=hdr)
        for _ in range(50):  # el proceso real va en un thread
            if calls:
                break
            time.sleep(0.02)
        check(len(calls) == 1, "búsqueda legítima sí se procesa")
        check(cli.post("/api/search", json={"query": "piso"}, headers={"X-API-Secret": "mal"}).status_code == 401,
              "secret incorrecto → 401")
    finally:
        server._process_search_async = real_async


def main():
    test_spam_guard()
    test_db_counts()
    test_confirmation_limits()
    test_search_endpoint()
    print()
    if FAILS:
        print(f"FALLARON {len(FAILS)} checks:")
        for f in FAILS:
            print("  -", f)
        sys.exit(1)
    print("=== TODOS LOS TESTS ANTI-SPAM OK ===")


if __name__ == "__main__":
    main()
