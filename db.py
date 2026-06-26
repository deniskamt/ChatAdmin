"""
Хранилище аукциона. Поддерживает два бэкенда автоматически:

  * PostgreSQL — если задан DATABASE_URL (Railway создаёт его при добавлении
    Postgres-плагина). Рекомендуется для продакшена: данные не теряются.
  * SQLite     — если DATABASE_URL нет; файл по пути DB_PATH (по умолчанию
    auction.db). Удобно для локального запуска.

Таблицы:
  lots — лоты (товары): заголовок, описание, активность.
  bids — ставки: к какому лоту, кто поставил, сумма, анонимность.
"""

import os
from contextlib import contextmanager

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
USE_PG = bool(DATABASE_URL)
DB_PATH = os.environ.get("DB_PATH", "auction.db")

if USE_PG:
    import psycopg2
    import psycopg2.extras
else:
    import sqlite3


@contextmanager
def _conn():
    if USE_PG:
        conn = psycopg2.connect(DATABASE_URL)
    else:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _cursor(conn):
    if USE_PG:
        return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    return conn.cursor()


def _q(sql: str) -> str:
    """Плейсхолдеры: SQLite использует '?', psycopg2 — '%s'."""
    return sql.replace("?", "%s") if USE_PG else sql


_SCHEMA_PG = """
CREATE TABLE IF NOT EXISTS lots (
    id          SERIAL PRIMARY KEY,
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS bids (
    id         SERIAL PRIMARY KEY,
    lot_id     INTEGER NOT NULL REFERENCES lots(id) ON DELETE CASCADE,
    user_id    BIGINT NOT NULL,
    username   TEXT,
    amount     BIGINT NOT NULL,
    anonymous  INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
"""

_SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS lots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS bids (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    lot_id     INTEGER NOT NULL REFERENCES lots(id) ON DELETE CASCADE,
    user_id    INTEGER NOT NULL,
    username   TEXT,
    amount     INTEGER NOT NULL,
    anonymous  INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def init_db() -> None:
    with _conn() as conn:
        cur = _cursor(conn)
        if USE_PG:
            cur.execute(_SCHEMA_PG)
        else:
            conn.executescript(_SCHEMA_SQLITE)


def _insert_returning_id(conn, sql: str, params) -> int:
    cur = _cursor(conn)
    if USE_PG:
        cur.execute(_q(sql + " RETURNING id"), params)
        return cur.fetchone()["id"]
    cur.execute(_q(sql), params)
    return cur.lastrowid


# ---------- Лоты ----------

def add_lot(title: str, description: str) -> int:
    with _conn() as conn:
        return _insert_returning_id(
            conn, "INSERT INTO lots (title, description) VALUES (?, ?)",
            (title, description),
        )


def deactivate_lot(lot_id: int) -> bool:
    with _conn() as conn:
        cur = _cursor(conn)
        cur.execute(_q("UPDATE lots SET active = 0 WHERE id = ?"), (lot_id,))
        return cur.rowcount > 0


def get_lot(lot_id: int):
    with _conn() as conn:
        cur = _cursor(conn)
        cur.execute(_q("SELECT * FROM lots WHERE id = ?"), (lot_id,))
        return cur.fetchone()


def count_active_lots() -> int:
    with _conn() as conn:
        cur = _cursor(conn)
        cur.execute("SELECT COUNT(*) AS n FROM lots WHERE active = 1")
        return cur.fetchone()["n"]


def get_active_lots(limit: int, offset: int):
    with _conn() as conn:
        cur = _cursor(conn)
        cur.execute(
            _q("SELECT * FROM lots WHERE active = 1 ORDER BY id DESC LIMIT ? OFFSET ?"),
            (limit, offset),
        )
        return cur.fetchall()


def list_all_lots():
    with _conn() as conn:
        cur = _cursor(conn)
        cur.execute("SELECT * FROM lots ORDER BY id DESC")
        return cur.fetchall()


# ---------- Ставки ----------

def add_bid(lot_id: int, user_id: int, username: str, amount: int, anonymous: bool) -> int:
    with _conn() as conn:
        return _insert_returning_id(
            conn,
            "INSERT INTO bids (lot_id, user_id, username, amount, anonymous) "
            "VALUES (?, ?, ?, ?, ?)",
            (lot_id, user_id, username, amount, int(anonymous)),
        )


def get_bids(lot_id: int):
    """Ставки по лоту, по убыванию суммы."""
    with _conn() as conn:
        cur = _cursor(conn)
        cur.execute(
            _q("SELECT * FROM bids WHERE lot_id = ? ORDER BY amount DESC, id ASC"),
            (lot_id,),
        )
        return cur.fetchall()


def get_max_bid(lot_id: int):
    with _conn() as conn:
        cur = _cursor(conn)
        cur.execute(_q("SELECT MAX(amount) AS m FROM bids WHERE lot_id = ?"), (lot_id,))
        row = cur.fetchone()
        return row["m"] if row and row["m"] is not None else None
