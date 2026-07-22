"""SQLite storage: designs, settings, and the daily image-usage counter."""
import datetime as dt
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "designs.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS designs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phrase TEXT NOT NULL,
    filters TEXT NOT NULL DEFAULT '',
    file TEXT,
    print_file TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    error TEXT,
    test INTEGER NOT NULL DEFAULT 0,  -- 1 = scratch image from the Test tab, skips the pipeline
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS usage (day TEXT PRIMARY KEY, images INTEGER NOT NULL DEFAULT 0);
"""


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout = 5000")
    return con


MIGRATIONS = (
    ("tags", "ALTER TABLE designs ADD COLUMN tags TEXT NOT NULL DEFAULT ''"),
    ("rating", "ALTER TABLE designs ADD COLUMN rating INTEGER NOT NULL DEFAULT 0"),
    ("product_id", "ALTER TABLE designs ADD COLUMN product_id TEXT"),
    ("reviewed_at", "ALTER TABLE designs ADD COLUMN reviewed_at TEXT"),
    ("test", "ALTER TABLE designs ADD COLUMN test INTEGER NOT NULL DEFAULT 0"),
)


def init() -> None:
    with connect() as con:
        con.executescript(SCHEMA)
        cols = {r["name"] for r in con.execute("PRAGMA table_info(designs)")}
        for col, stmt in MIGRATIONS:
            if col not in cols:
                con.execute(stmt)


def get_setting(key: str, default=None):
    with connect() as con:
        row = con.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    if row and row["value"]:
        return row["value"]
    return os.environ.get(key.upper(), default)


def set_setting(key: str, value: str) -> None:
    with connect() as con:
        con.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def _today() -> str:
    return dt.date.today().isoformat()


def images_today() -> int:
    with connect() as con:
        row = con.execute("SELECT images FROM usage WHERE day = ?", (_today(),)).fetchone()
    return row["images"] if row else 0


def record_image() -> None:
    with connect() as con:
        con.execute(
            "INSERT INTO usage (day, images) VALUES (?, 1) "
            "ON CONFLICT(day) DO UPDATE SET images = images + 1",
            (_today(),),
        )
