from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import Any


class IdempotencyStore:
    def __init__(self, ttl_seconds: int = 3600) -> None:
        self.ttl_seconds = ttl_seconds
        self._store: dict[str, tuple[float, str, dict[str, Any]]] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def payload_hash(payload: dict[str, Any]) -> str:
        normalized = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    async def get(self, key: str) -> tuple[str, dict[str, Any]] | None:
        async with self._lock:
            item = self._store.get(key)
            if item is None:
                return None
            expires_at, payload_hash, response = item
            if expires_at < time.time():
                self._store.pop(key, None)
                return None
            return payload_hash, response

    async def set(self, key: str, payload_hash: str, response: dict[str, Any]) -> None:
        async with self._lock:
            self._store[key] = (time.time() + self.ttl_seconds, payload_hash, response)
