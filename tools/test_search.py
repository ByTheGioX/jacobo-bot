"""
Prueba el buscador contra la BD local e imprime el diagnóstico completo:
qué criterios se parsearon, qué hay en la BD y qué propiedades encajan.

No envía emails ni guarda la búsqueda en el historial — es solo diagnóstico.

Uso (en el VPS):
    python -m tools.test_search "duplex"
    python -m tools.test_search "casa en rancho domingo"
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

from database.db import Database
from search.smart_search import SmartSearch


def main():
    if len(sys.argv) < 2:
        print('Uso: python -m tools.test_search "lo que buscaría el visitante"')
        sys.exit(1)
    query = " ".join(sys.argv[1:])

    db = Database()
    with db._conn() as conn:
        by_status = {
            r["status"]: r["c"]
            for r in conn.execute("SELECT status, COUNT(*) c FROM properties GROUP BY status")
        }
        with_wp = conn.execute(
            "SELECT COUNT(*) c FROM properties WHERE status='active' AND wp_post_id IS NOT NULL"
        ).fetchone()["c"]
        ops = {
            (r["operation_type"] or "(vacío)"): r["c"]
            for r in conn.execute(
                "SELECT operation_type, COUNT(*) c FROM properties WHERE status='active' GROUP BY operation_type"
            )
        }

    print(f"BD: {db.path}")
    print(f"Propiedades por estado: {by_status or '(BD vacía)'}")
    print(f"Activas con wp_post_id (publicadas): {with_wp}")
    print(f"operation_type de las activas: {ops}")
    print()

    searcher = SmartSearch()
    criteria = searcher._parse_query(query)
    print(f"Consulta: '{query}'")
    print(f"Criterios parseados: {criteria.to_dict()}")
    print()

    matches = searcher._search_db(criteria)
    visibles = [m for m in matches if m.get("wp_post_id")]
    print(f"Coincidencias: {len(matches)} (con wp_post_id → visibles al público: {len(visibles)})")
    for m in matches[:10]:
        print(f"  - [{m['idealista_id']}] {(m['title'] or '')[:60]} | {m['price'] or '—'} € | wp={m['wp_post_id']}")
    if len(matches) > 10:
        print(f"  ... y {len(matches) - 10} más")


if __name__ == "__main__":
    main()
