"""Helpers for Telethon SQLite session reliability on shared Docker volumes."""

from __future__ import annotations

import logging
import sqlite3

from telethon import TelegramClient

logger = logging.getLogger(__name__)


def configure_telethon_session_sqlite(client: TelegramClient) -> None:
    """Reduce 'database is locked' errors when the session file is on a volume."""
    session = client.session
    conn = getattr(session, "_conn", None)
    if conn is None:
        return
    try:
        conn.execute("PRAGMA busy_timeout=120000")
        conn.execute("PRAGMA journal_mode=WAL")
    except sqlite3.Error as exc:
        logger.warning("Could not tune Telethon session SQLite pragmas: %s", exc)
