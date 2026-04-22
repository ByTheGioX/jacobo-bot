"""
Dashboard de estadísticas del bot.
Se puede lanzar con: python -m dashboard.stats
"""

import json
from database.db import Database


def print_dashboard():
    db = Database()
    stats = db.get_dashboard_stats()
    searches = db.get_recent_searches(limit=5)
    props = db.get_active_properties()

    print("\n" + "=" * 55)
    print("  JACOBO-BOT — Dashboard")
    print("=" * 55)
    print(f"  Propiedades activas:       {stats['active_properties']}")
    print(f"  Búsquedas de compradores:  {stats['total_buyer_searches']}")

    last = stats.get("last_run")
    if last:
        print(f"\n  Última ejecución: {last['started_at']}")
        print(f"    → Nuevas:       {last['properties_new']}")
        print(f"    → Actualizadas: {last['properties_updated']}")
        print(f"    → Eliminadas:   {last['properties_removed']}")
        print(f"    → Estado:       {last['status']}")

    if props:
        print("\n  Últimas 5 propiedades activas:")
        for p in props[-5:]:
            price = f"{p['price']:,} €" if p.get("price") else "—"
            print(f"    [{p['idealista_id']}] {p['title'][:40]:<40} {price}")

    if searches:
        print("\n  Últimas búsquedas de compradores:")
        for s in searches:
            parsed = json.loads(s["parsed"] or "{}")
            loc = parsed.get("location", "—")
            rooms = parsed.get("rooms_min", "—")
            print(f"    #{s['id']} — {loc} | {rooms} hab | emails: {s['emails_sent']}")

    print("=" * 55 + "\n")


if __name__ == "__main__":
    print_dashboard()
