import json
import os
from datetime import datetime, timezone
from contextlib import contextmanager

import psycopg2
import psycopg2.extras

DB_URL = os.environ["SUPABASE_DB_URL"]


@contextmanager
def _conn():
    conn = psycopg2.connect(DB_URL)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _row(cursor) -> dict | None:
    row = cursor.fetchone()
    return dict(row) if row else None


def _rows(cursor) -> list[dict]:
    return [dict(r) for r in cursor.fetchall()]


def init_db() -> None:
    with _conn() as conn:
        conn.cursor().execute("SELECT 1")


def upsert_listing(data: dict) -> bool:
    """Insert or update a listing. Returns True if it was a new insertion."""
    now = datetime.now(timezone.utc).isoformat()
    data = {**data, "last_seen": now}

    with _conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        existing = cur.execute(
            "SELECT price FROM listings WHERE id = %s", (data["id"],)
        ) or cur.fetchone()

        if existing:
            old_price = existing["price"]
            new_price = data.get("price")
            if new_price and old_price and new_price != old_price:
                data["previous_price"] = old_price
                data["price_changed_at"] = now

            fields = [k for k in data if k not in ("id", "first_seen")]
            sql = "UPDATE listings SET " + ", ".join(f"{f} = %s" for f in fields)
            sql += " WHERE id = %s"
            cur.execute(sql, [data[f] for f in fields] + [data["id"]])
            return False
        else:
            data.setdefault("first_seen", now)
            cols = ", ".join(data.keys())
            placeholders = ", ".join("%s" for _ in data)
            cur.execute(
                f"INSERT INTO listings ({cols}) VALUES ({placeholders})",
                list(data.values()),
            )
            return True


def get_listing(listing_id: str) -> dict | None:
    with _conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM listings WHERE id = %s", (listing_id,))
        return _row(cur)


def get_all_listings() -> list[dict]:
    with _conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM listings ORDER BY score DESC NULLS LAST")
        return _rows(cur)


def get_unenriched() -> list[dict]:
    with _conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT * FROM listings WHERE enriched = 0")
        return _rows(cur)


def mark_alerted(listing_id: str) -> None:
    with _conn() as conn:
        conn.cursor().execute(
            "UPDATE listings SET alerted = 1 WHERE id = %s", (listing_id,)
        )


def log_run(new_count: int, errors: list[str]) -> None:
    with _conn() as conn:
        conn.cursor().execute(
            "INSERT INTO run_log (run_at, new_listings, errors) VALUES (%s, %s, %s)"
            " ON CONFLICT (run_at) DO UPDATE SET new_listings = EXCLUDED.new_listings, errors = EXCLUDED.errors",
            (
                datetime.now(timezone.utc).isoformat(),
                new_count,
                json.dumps(errors) if errors else None,
            ),
        )


def get_last_run_time() -> str | None:
    with _conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT run_at FROM run_log ORDER BY run_at DESC LIMIT 1")
        row = _row(cur)
        return row["run_at"] if row else None


def get_scored_listings() -> list[dict]:
    with _conn() as conn:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute(
            "SELECT * FROM listings WHERE score IS NOT NULL ORDER BY score DESC"
        )
        return _rows(cur)
