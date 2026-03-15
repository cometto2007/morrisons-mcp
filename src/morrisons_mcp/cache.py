import asyncio
import json
import logging
import os
import time
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS cache (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    expires_at REAL NOT NULL
);
"""

CREATE_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_cache_expires ON cache(expires_at);
"""


class ProductCache:
    def __init__(self, db_path: str = "/data/cache.db") -> None:
        self.db_path = db_path
        db_dir = os.path.dirname(db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        self._db: aiosqlite.Connection | None = None
        self._init_lock = asyncio.Lock()

    async def _ensure_db(self) -> aiosqlite.Connection:
        """Return the persistent DB connection, initialising it on first use."""
        if self._db is not None:
            return self._db
        async with self._init_lock:
            # Double-check after acquiring lock
            if self._db is None:
                db = await aiosqlite.connect(self.db_path)
                await db.execute(CREATE_TABLE_SQL)
                await db.execute(CREATE_INDEX_SQL)
                await db.commit()
                self._db = db
        return self._db

    async def get(self, key: str) -> Any | None:
        """Return parsed JSON value or None if expired/missing."""
        db = await self._ensure_db()
        now = time.time()

        # Delete expired entry on access
        await db.execute(
            "DELETE FROM cache WHERE key = ? AND expires_at <= ?", (key, now)
        )
        await db.commit()

        async with db.execute(
            "SELECT value FROM cache WHERE key = ? AND expires_at > ?", (key, now)
        ) as cursor:
            row = await cursor.fetchone()

        if row is None:
            logger.debug(f"Cache miss: {key}")
            return None

        logger.debug(f"Cache hit: {key}")
        return json.loads(row[0])

    async def set(self, key: str, value: Any, ttl: int = 3600) -> None:
        """Store JSON-serialised value with TTL seconds."""
        db = await self._ensure_db()
        expires_at = time.time() + ttl
        serialised = json.dumps(value)
        await db.execute(
            "INSERT OR REPLACE INTO cache (key, value, expires_at) VALUES (?, ?, ?)",
            (key, serialised, expires_at),
        )
        await db.commit()
        logger.debug(f"Cache set: {key} (ttl={ttl}s)")

    async def clear(self) -> None:
        """Delete all cache entries."""
        db = await self._ensure_db()
        await db.execute("DELETE FROM cache")
        await db.commit()
        logger.info("Cache cleared")

    async def cleanup(self) -> None:
        """Delete all expired entries."""
        db = await self._ensure_db()
        now = time.time()
        async with db.execute(
            "DELETE FROM cache WHERE expires_at <= ?", (now,)
        ) as cursor:
            count = cursor.rowcount
        await db.commit()
        logger.info(f"Cache cleanup: removed {count} expired entries")

    async def close(self) -> None:
        """Close the persistent database connection."""
        if self._db is not None:
            await self._db.close()
            self._db = None
