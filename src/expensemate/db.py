"""SQLite storage for expense records.

Amounts are stored as integer hundredths ("cents") so sums are exact.
"""
import os
import sqlite3
from datetime import datetime

SCHEMA = """
CREATE TABLE IF NOT EXISTS expenses (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
    currency     TEXT    NOT NULL,
    category     TEXT    NOT NULL,
    date         TEXT    NOT NULL,          -- ISO date, e.g. 2026-11-19
    note         TEXT    NOT NULL DEFAULT '',
    created_at   TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS rate_cache (
    base       TEXT PRIMARY KEY,
    rates_json TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);
"""


def connect(path: str) -> sqlite3.Connection:
    """Open (and create if needed) the database at `path`."""
    if path != ":memory:":
        folder = os.path.dirname(path)
        if folder:
            os.makedirs(folder, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def to_cents(amount: float) -> int:
    return int(round(float(amount) * 100))


def from_cents(cents: int):
    """Return an int when there are no decimals (120), else a float (3.5)."""
    return cents // 100 if cents % 100 == 0 else cents / 100


def row_to_record(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "amount": from_cents(row["amount_cents"]),
        "currency": row["currency"],
        "category": row["category"],
        "date": row["date"],
        "note": row["note"],
    }


def insert(conn, amount_cents, currency, category, date, note) -> int:
    cur = conn.execute(
        "INSERT INTO expenses (amount_cents, currency, category, date, note, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (amount_cents, currency, category, date, note,
         datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    return cur.lastrowid


def get(conn, record_id: int):
    return conn.execute("SELECT * FROM expenses WHERE id = ?", (record_id,)).fetchone()


def update(conn, record_id: int, fields: dict) -> None:
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE expenses SET {sets} WHERE id = ?", (*fields.values(), record_id))
    conn.commit()


def delete(conn, record_id: int) -> None:
    conn.execute("DELETE FROM expenses WHERE id = ?", (record_id,))
    conn.commit()


def search(conn, start=None, end=None, category=None, keyword=None):
    sql = "SELECT * FROM expenses WHERE 1=1"
    args = []
    if start:
        sql += " AND date >= ?"
        args.append(start)
    if end:
        sql += " AND date <= ?"
        args.append(end)
    if category:
        sql += " AND category = ?"
        args.append(category)
    if keyword:
        sql += " AND note LIKE ?"
        args.append(f"%{keyword}%")
    sql += " ORDER BY date, id"
    return conn.execute(sql, args).fetchall()
