"""
Простое персистентное хранилище соответствий:
  ID сообщения, отправленного в MAX-чат  ->  кто из Telegram его прислал.

Нужно, чтобы при ответе (Reply) на конкретное сообщение в MAX-чате бот знал,
какому именно человеку в Telegram отправить ответ.

Используется обычный SQLite-файл, переживает перезапуск бота.
"""

import asyncio
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "bridge.db"

_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
_conn.execute(
    """
    CREATE TABLE IF NOT EXISTS mid_map (
        max_mid     TEXT PRIMARY KEY,
        tg_user_id  INTEGER NOT NULL,
        tg_name     TEXT,
        created_at  TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """
)
_conn.commit()
_lock = asyncio.Lock()


async def save_mapping(max_mid: str, tg_user_id: int, tg_name: str) -> None:
    async with _lock:
        _conn.execute(
            "INSERT OR REPLACE INTO mid_map (max_mid, tg_user_id, tg_name) VALUES (?, ?, ?)",
            (str(max_mid), tg_user_id, tg_name),
        )
        _conn.commit()


async def get_tg_user(max_mid: str) -> tuple[int, str] | None:
    async with _lock:
        row = _conn.execute(
            "SELECT tg_user_id, tg_name FROM mid_map WHERE max_mid = ?",
            (str(max_mid),),
        ).fetchone()
    return tuple(row) if row else None
