"""SQLite storage for tracking seen article URLs per brief."""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone


class NewsStorage:
    def __init__(self, db_path: str):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS seen_articles (
                url       TEXT NOT NULL,
                brief     TEXT NOT NULL,
                seen_at   TEXT NOT NULL,
                qualified INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (url, brief)
            )
        """)
        self.conn.commit()

    def is_seen(self, url: str, brief: str) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM seen_articles WHERE url = ? AND brief = ?", (url, brief)
        )
        return cur.fetchone() is not None

    def mark_seen(self, url: str, brief: str, qualified: bool = False) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO seen_articles (url, brief, seen_at, qualified) VALUES (?, ?, ?, ?)",
            (url, brief, datetime.now(timezone.utc).isoformat(), 1 if qualified else 0),
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
