"""
Resumen de quién llama a la API Flask (puerto 8080), leyendo data/jacobo_bot.log.

Sirve para:
  - saber desde qué IP llama WordPress (CDmon) → para cerrar el 8080 al resto
  - ver si alguien que NO es WordPress está llamando a /api/search (secret filtrado)
  - contar por día búsquedas descartadas como spam y confirmaciones enviadas/omitidas

Solo lee el log: no toca la BD, ni WordPress, ni manda correos.

Uso:
    python -m tools.api_access_report               # últimos 30 días
    python -m tools.api_access_report --days 7
"""

import argparse
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

LOG_PATH = Path(__file__).resolve().parent.parent / "data" / "jacobo_bot.log"

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) \d{2}:\d{2}:\d{2} \[\w+\] (\S+) — (.*)$")
_ACCESS_RE = re.compile(r'^(\S+) - - \[[^\]]+\] "(\w+) (\S+)[^"]*" (\d{3})')

_EVENTS = {
    "spam": "Búsqueda descartada como spam",
    "confirmada": "Confirmación enviada al comprador",
    "omitida": "Confirmación omitida",
}


def parse(log_path: Path, since: str):
    by_ip = defaultdict(lambda: {"total": 0, "paths": Counter(), "codes": Counter(), "first": "", "last": ""})
    events = defaultdict(Counter)
    with open(log_path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            m = _LINE_RE.match(_ANSI_RE.sub("", raw.rstrip("\n")))
            if not m or m.group(1) < since:
                continue
            day, logger_name, msg = m.groups()
            for key, needle in _EVENTS.items():
                if needle in msg:
                    events[day][key] += 1
            if logger_name != "werkzeug":
                continue
            a = _ACCESS_RE.match(msg)
            if not a:
                continue
            ip, method, path, code = a.groups()
            d = by_ip[ip]
            d["total"] += 1
            d["paths"][f"{method} {path.split('?')[0]}"] += 1
            d["codes"][code] += 1
            d["first"] = d["first"] or day
            d["last"] = day
    return by_ip, events


def main():
    ap = argparse.ArgumentParser(description="Quién llama a la API del bot (lee el log)")
    ap.add_argument("--days", type=int, default=30, help="Días hacia atrás (por defecto 30)")
    args = ap.parse_args()

    if not LOG_PATH.exists():
        print(f"No existe el log: {LOG_PATH}")
        return
    since = (datetime.now() - timedelta(days=args.days)).strftime("%Y-%m-%d")
    by_ip, events = parse(LOG_PATH, since)

    print()
    print("=" * 78)
    print(f"  LLAMADAS A LA API POR IP (desde {since})")
    print("=" * 78)
    if not by_ip:
        print("  No hay líneas de acceso en el log para ese periodo.")
    for ip, d in sorted(by_ip.items(), key=lambda kv: -kv[1]["total"]):
        codes = ", ".join(f"{c}×{n}" for c, n in d["codes"].most_common())
        print(f"\n  {ip:<18} {d['total']:>6} peticiones   {d['first']} → {d['last']}")
        print(f"    códigos: {codes}")
        for p, n in d["paths"].most_common(5):
            print(f"    {n:>6}  {p}")

    print()
    print("  Pista: la IP con muchos POST /api/search con 200/202 es WordPress (CDmon).")
    print("  Cualquier otra IP con 200/202 en /api/search tiene el secret → rotarlo.")
    print("  Las de 401/404 son escáneres de internet: se cortan cerrando el 8080.")

    print()
    print("=" * 78)
    print("  BÚSQUEDAS Y CONFIRMACIONES POR DÍA")
    print("=" * 78)
    print(f"  {'Día':<12} {'Spam descartado':>16} {'Confirm. enviadas':>18} {'Confirm. omitidas':>18}")
    for day in sorted(events):
        e = events[day]
        print(f"  {day:<12} {e['spam']:>16} {e['confirmada']:>18} {e['omitida']:>18}")
    if not events:
        print("  (sin eventos en el periodo)")
    print()


if __name__ == "__main__":
    main()
