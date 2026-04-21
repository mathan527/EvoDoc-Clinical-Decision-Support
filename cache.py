from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any


class TTLCache:
    """
    In-memory TTL cache.

    Redis upgrade path:
    Replace the in-memory dict operations in get/set/invalidate
    with equivalent async Redis commands (e.g., aioredis GET/SETEX/DEL).
    """

    def __init__(self, ttl_seconds: int = 3600) -> None:
        self.ttl_seconds = ttl_seconds
        self._store: dict[str, tuple[float, Any]] = {}
        self._lock = asyncio.Lock()
        self.hits = 0
        self.misses = 0

    @staticmethod
    def _normalize(values: list[str]) -> list[str]:
        normalized = [" ".join(v.strip().lower().split()) for v in values if v and v.strip()]
        return sorted(set(normalized))

    @staticmethod
    def generate_key(proposed_medicines: list[str], current_medications: list[str]) -> str:
        proposed = TTLCache._normalize(proposed_medicines)
        current = TTLCache._normalize(current_medications)
        # EvoDoc deterministic key format:
        # raw = sorted(proposed_medicines_lower) + "|" + sorted(current_medications_lower)
        raw = f"{','.join(proposed)}|{','.join(current)}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    async def get(self, key: str) -> Any | None:
        async with self._lock:
            item = self._store.get(key)
            if item is None:
                self.misses += 1
                return None

            expires_at, value = item
            if expires_at < time.time():
                self._store.pop(key, None)
                self.misses += 1
                return None

            self.hits += 1
            return value

    async def set(self, key: str, value: Any) -> None:
        async with self._lock:
            self._store[key] = (time.time() + self.ttl_seconds, value)

    async def invalidate(self, key: str) -> None:
        async with self._lock:
            self._store.pop(key, None)

    async def stats(self) -> dict[str, int | float]:
        async with self._lock:
            total = self.hits + self.misses
            hit_ratio = (self.hits / total) if total else 0.0
            oldest_expiry = min((expiry for expiry, _ in self._store.values()), default=time.time())
            oldest_age_seconds = max(0, int((time.time() + self.ttl_seconds) - oldest_expiry)) if self._store else 0
            return {
                "items": len(self._store),
                "hits": self.hits,
                "misses": self.misses,
                "ttl_seconds": self.ttl_seconds,
                "hit_ratio": round(hit_ratio, 3),
                "oldest_entry_age_seconds": oldest_age_seconds,
            }
