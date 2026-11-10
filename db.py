import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.getenv("DATA_DIR", ".")) / "housing.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id            TEXT PRIMARY KEY,
    url           TEXT,
    platform      TEXT,
    title         TEXT,
    price         INTEGER,
    bedrooms      INTEGER,
    bathrooms     INTEGER,
    sq_ft         INTEGER,
    postcode      TEXT,
    address       TEXT,
    lat           REAL,
    lng           REAL,
    has_garden    INTEGER DEFAULT 0,
    has_parking   INTEGER DEFAULT 0,
    condition     TEXT DEFAULT 'unknown',
    dist_km       REAL,
    tenure        TEXT DEFAULT 'unknown',
    lease_years   INTEGER,
    score         REAL,
    score_json    TEXT,
    days_listed   INTEGER DEFAULT 0,
    first_seen    TEXT,
    last_seen     TEXT,
    image_url     TEXT,
    description   TEXT,
    enriched      INTEGER DEFAULT 0,
    alerted       INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS run_log (
    run_at        TEXT PRIMARY KEY,
    new_listings  INTEGER DEFAULT 0,
    errors        TEXT
);
"""


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _conn() as conn:
        conn.executescript(SCHEMA)


def upsert_listing(data: dict) -> bool:
    """Insert or update a listing. Returns True if it was a new insertion."""
    now = datetime.now(timezone.utc).isoformat()
    data = {**data, "last_seen": now}

    with _conn() as conn:
        existing = conn.execute(
            "SELECT id FROM listings WHERE id = ?", (data["id"],)
        ).fetchone()

        if existing:
            fields = [k for k in data if k not in ("id", "first_seen")]
            sql = "UPDATE listings SET " + ", ".join(f"{f} = ?" for f in fields)
            sql += " WHERE id = ?"
            conn.execute(sql, [data[f] for f in fields] + [data["id"]])
            return False
        else:
            data.setdefault("first_seen", now)
            cols = ", ".join(data.keys())
            placeholders = ", ".join("?" for _ in data)
            conn.execute(
                f"INSERT INTO listings ({cols}) VALUES ({placeholders})",
                list(data.values()),
            )
            return True


def get_listing(listing_id: str) -> dict | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM listings WHERE id = ?", (listing_id,)
        ).fetchone()
        if row:
            return dict(row)
        return None


def get_all_listings() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM listings ORDER BY score DESC NULLS LAST"
        ).fetchall()
        return [dict(r) for r in rows]


def get_unenriched() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM listings WHERE enriched = 0"
        ).fetchall()
        return [dict(r) for r in rows]


def mark_alerted(listing_id: str) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE listings SET alerted = 1 WHERE id = ?", (listing_id,)
        )


def log_run(new_count: int, errors: list[str]) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO run_log (run_at, new_listings, errors) VALUES (?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(),
                new_count,
                json.dumps(errors) if errors else None,
            ),
        )


def get_last_run_time() -> str | None:
    with _conn() as conn:
        row = conn.execute(
            "SELECT run_at FROM run_log ORDER BY run_at DESC LIMIT 1"
        ).fetchone()
        return row["run_at"] if row else None


def get_scored_listings() -> list[dict]:
    with _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM listings WHERE score IS NOT NULL ORDER BY score DESC"
        ).fetchall()
        return [dict(r) for r in rows]
