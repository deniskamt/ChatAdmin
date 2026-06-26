"""
Хранилище аукциона на SQLite (без внешних зависимостей).

Таблицы:
  lots — лоты (товары): заголовок, описание, активность.
  bids — ставки: к какому лоту, кто поставил, сумма, анонимность.

Путь к файлу БД задаётся переменной DB_PATH (по умолчанию auction.db).
На Railway файловая система временная — чтобы данные не терялись при
передеплое, смонтируйте Volume и укажите DB_PATH внутри него.
"""

import os
import sqlite3
from contextlib import contextmanager

DB_PATH = os.environ.get("DB_PATH", "auction.db")


@contextmanager
def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _conn() as conn:
        conn.executescript(
            """
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
        )


# ---------- Лоты ----------

def add_lot(title: str, description: str) -> int:
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO lots (title, description) VALUES (?, ?)",
            (title, description),
        )
        return cur.lastrowid


def deactivate_lot(lot_id: int) -> bool:
    with _conn() as conn:
        cur = conn.execute("UPDATE lots SET active = 0 WHERE id = ?", (lot_id,))
        return cur.rowcount > 0


def get_lot(lot_id: int):
    with _conn() as conn:
        return conn.execute("SELECT * FROM lots WHERE id = ?", (lot_id,)).fetchone()


def count_active_lots() -> int:
    with _conn() as conn:
        return conn.execute("SELECT COUNT(*) FROM lots WHERE active = 1").fetchone()[0]


def get_active_lots(limit: int, offset: int):
    with _conn() as conn:
        return conn.execute(
            "SELECT * FROM lots WHERE active = 1 ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()


def list_all_lots():
    with _conn() as conn:
        return conn.execute("SELECT * FROM lots ORDER BY id DESC").fetchall()


# ---------- Ставки ----------

def add_bid(lot_id: int, user_id: int, username: str, amount: int, anonymous: bool) -> int:
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO bids (lot_id, user_id, username, amount, anonymous) "
            "VALUES (?, ?, ?, ?, ?)",
            (lot_id, user_id, username, amount, int(anonymous)),
        )
        return cur.lastrowid


def get_bids(lot_id: int):
    """Ставки по лоту, по убыванию суммы."""
    with _conn() as conn:
        return conn.execute(
            "SELECT * FROM bids WHERE lot_id = ? ORDER BY amount DESC, id ASC",
            (lot_id,),
        ).fetchall()


def get_max_bid(lot_id: int):
    with _conn() as conn:
        row = conn.execute(
            "SELECT MAX(amount) AS m FROM bids WHERE lot_id = ?", (lot_id,)
        ).fetchone()
        return row["m"] if row and row["m"] is not None else None
