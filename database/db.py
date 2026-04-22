"""
Capa de base de datos SQLite.
Tablas:
  - properties         : registro de propiedades scrapeadas
  - buyer_searches     : búsquedas de compradores
  - collaborating_agencies: agencias colaboradoras
  - scrape_runs        : historial de ejecuciones del scraper
"""

import sqlite3
import json
import logging
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from config.settings import DB_PATH

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS properties (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    idealista_id        TEXT    UNIQUE NOT NULL,
    wp_post_id          INTEGER,
    url                 TEXT,
    title               TEXT,
    price               INTEGER,
    location            TEXT,
    area_m2             REAL,
    rooms               INTEGER,
    bathrooms           INTEGER,
    property_type       TEXT,
    operation_type      TEXT,
    has_parking         BOOLEAN DEFAULT 0,
    has_pool            BOOLEAN DEFAULT 0,
    has_terrace         BOOLEAN DEFAULT 0,
    description         TEXT,
    photo_urls          TEXT,           -- JSON array
    processed_photos    TEXT,           -- JSON array de dicts
    status              TEXT DEFAULT 'active',  -- active | removed
    first_seen_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_published_at   TIMESTAMP,
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS buyer_searches (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_query       TEXT NOT NULL,
    parsed          TEXT,              -- JSON: {location, rooms, price_max, ...}
    contact_email   TEXT,
    contact_name    TEXT,
    emails_sent     INTEGER DEFAULT 0,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS collaborating_agencies (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    email       TEXT NOT NULL UNIQUE,
    zones       TEXT,                  -- JSON array de zonas
    active      BOOLEAN DEFAULT 1,
    added_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS scrape_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at     TIMESTAMP,
    properties_found    INTEGER DEFAULT 0,
    properties_new      INTEGER DEFAULT 0,
    properties_updated  INTEGER DEFAULT 0,
    properties_removed  INTEGER DEFAULT 0,
    status          TEXT DEFAULT 'running',  -- running | success | error
    error_msg       TEXT
);
"""


class Database:
    def __init__(self, path: str = DB_PATH):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self):
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    def upsert_property(self, prop_data: dict) -> tuple[bool, bool]:
        """
        Inserta o actualiza una propiedad.
        Retorna (is_new, was_updated).
        """
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT * FROM properties WHERE idealista_id = ?",
                (prop_data["idealista_id"],),
            ).fetchone()

            now = datetime.utcnow().isoformat()

            if existing is None:
                conn.execute(
                    """INSERT INTO properties
                       (idealista_id, url, title, price, location, area_m2, rooms, bathrooms,
                        property_type, operation_type, has_parking, has_pool, has_terrace,
                        description, photo_urls, status, last_seen_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        prop_data["idealista_id"],
                        prop_data.get("url"),
                        prop_data.get("title"),
                        prop_data.get("price"),
                        prop_data.get("location"),
                        prop_data.get("area_m2"),
                        prop_data.get("rooms"),
                        prop_data.get("bathrooms"),
                        prop_data.get("property_type"),
                        prop_data.get("operation_type"),
                        prop_data.get("has_parking", False),
                        prop_data.get("has_pool", False),
                        prop_data.get("has_terrace", False),
                        prop_data.get("description"),
                        json.dumps(prop_data.get("photo_urls", [])),
                        "active",
                        now,
                    ),
                )
                return True, False

            # Detectar cambios relevantes
            changed = (
                existing["price"] != prop_data.get("price")
                or existing["title"] != prop_data.get("title")
                or existing["description"] != prop_data.get("description")
            )

            conn.execute(
                """UPDATE properties SET
                   title=?, price=?, description=?, location=?, area_m2=?,
                   rooms=?, bathrooms=?, has_parking=?, has_pool=?, has_terrace=?,
                   status='active', last_seen_at=?, updated_at=?
                   WHERE idealista_id=?""",
                (
                    prop_data.get("title"),
                    prop_data.get("price"),
                    prop_data.get("description"),
                    prop_data.get("location"),
                    prop_data.get("area_m2"),
                    prop_data.get("rooms"),
                    prop_data.get("bathrooms"),
                    prop_data.get("has_parking", False),
                    prop_data.get("has_pool", False),
                    prop_data.get("has_terrace", False),
                    now,
                    now,
                    prop_data["idealista_id"],
                ),
            )
            return False, changed

    def set_wp_post_id(self, idealista_id: str, wp_post_id: int):
        with self._conn() as conn:
            conn.execute(
                "UPDATE properties SET wp_post_id=?, last_published_at=? WHERE idealista_id=?",
                (wp_post_id, datetime.utcnow().isoformat(), idealista_id),
            )

    def set_processed_photos(self, idealista_id: str, photos: list[dict]):
        with self._conn() as conn:
            conn.execute(
                "UPDATE properties SET processed_photos=? WHERE idealista_id=?",
                (json.dumps(photos), idealista_id),
            )

    def mark_removed(self, idealista_id: str):
        with self._conn() as conn:
            conn.execute(
                "UPDATE properties SET status='removed', updated_at=? WHERE idealista_id=?",
                (datetime.utcnow().isoformat(), idealista_id),
            )

    def get_active_properties(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM properties WHERE status='active'"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_property(self, idealista_id: str) -> Optional[dict]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM properties WHERE idealista_id=?", (idealista_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_active_ids(self) -> set[str]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT idealista_id FROM properties WHERE status='active'"
            ).fetchall()
            return {r["idealista_id"] for r in rows}

    # ------------------------------------------------------------------
    # Buyer searches
    # ------------------------------------------------------------------

    def save_buyer_search(
        self,
        raw_query: str,
        parsed: dict,
        contact_email: str = "",
        contact_name: str = "",
    ) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO buyer_searches (raw_query, parsed, contact_email, contact_name)
                   VALUES (?,?,?,?)""",
                (raw_query, json.dumps(parsed), contact_email, contact_name),
            )
            return cur.lastrowid

    def mark_emails_sent(self, search_id: int, count: int):
        with self._conn() as conn:
            conn.execute(
                "UPDATE buyer_searches SET emails_sent=? WHERE id=?",
                (count, search_id),
            )

    def get_recent_searches(self, limit: int = 20) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM buyer_searches ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Collaborating agencies
    # ------------------------------------------------------------------

    def sync_agencies(self, agencies: list[dict]):
        """Sincroniza la lista de agencias desde la configuración."""
        with self._conn() as conn:
            for ag in agencies:
                conn.execute(
                    """INSERT INTO collaborating_agencies (name, email)
                       VALUES (?,?)
                       ON CONFLICT(email) DO UPDATE SET name=excluded.name""",
                    (ag["name"], ag["email"]),
                )

    def get_active_agencies(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM collaborating_agencies WHERE active=1"
            ).fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Scrape runs
    # ------------------------------------------------------------------

    def start_scrape_run(self) -> int:
        with self._conn() as conn:
            cur = conn.execute("INSERT INTO scrape_runs DEFAULT VALUES")
            return cur.lastrowid

    def finish_scrape_run(self, run_id: int, stats: dict, error: str = ""):
        with self._conn() as conn:
            conn.execute(
                """UPDATE scrape_runs SET
                   finished_at=?, properties_found=?, properties_new=?,
                   properties_updated=?, properties_removed=?,
                   status=?, error_msg=?
                   WHERE id=?""",
                (
                    datetime.utcnow().isoformat(),
                    stats.get("found", 0),
                    stats.get("new", 0),
                    stats.get("updated", 0),
                    stats.get("removed", 0),
                    "error" if error else "success",
                    error,
                    run_id,
                ),
            )

    def get_dashboard_stats(self) -> dict:
        with self._conn() as conn:
            total = conn.execute(
                "SELECT COUNT(*) as c FROM properties WHERE status='active'"
            ).fetchone()["c"]

            last_run = conn.execute(
                "SELECT * FROM scrape_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()

            searches_total = conn.execute(
                "SELECT COUNT(*) as c FROM buyer_searches"
            ).fetchone()["c"]

            return {
                "active_properties": total,
                "total_buyer_searches": searches_total,
                "last_run": dict(last_run) if last_run else None,
            }
