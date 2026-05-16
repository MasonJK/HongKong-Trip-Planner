"""검색 결과 캐시. 같은 일자 재조회 시 API/스크래핑 비용 절감."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional


class Cache:
    def __init__(self, path: str = "cache.db", ttl_hours: int = 12):
        self.path = Path(path)
        self.ttl = timedelta(hours=ttl_hours)
        with sqlite3.connect(self.path) as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
            """)

    def get(self, key: str) -> Optional[Any]:
        with sqlite3.connect(self.path) as c:
            row = c.execute(
                "SELECT value, created_at FROM cache WHERE key = ?", (key,)
            ).fetchone()
        if not row:
            return None
        created = datetime.fromisoformat(row[1])
        if datetime.now() - created > self.ttl:
            return None
        return json.loads(row[0])

    def set(self, key: str, value: Any) -> None:
        with sqlite3.connect(self.path) as c:
            c.execute(
                "INSERT OR REPLACE INTO cache (key, value, created_at) VALUES (?, ?, ?)",
                (key, json.dumps(value, default=str), datetime.now().isoformat()),
            )

    def clear(self) -> None:
        with sqlite3.connect(self.path) as c:
            c.execute("DELETE FROM cache")
