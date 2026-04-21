from __future__ import annotations

import asyncio
import time


class FixedWindowRateLimiter:
    def __init__(self, limit: int = 30, window_seconds: int = 60) -> None:
        self.limit = max(1, limit)
        self.window_seconds = max(1, window_seconds)
        self._windows: dict[str, tuple[float, int]] = {}
        self._lock = asyncio.Lock()

    async def allow(self, key: str) -> tuple[bool, int]:
        now = time.time()
        async with self._lock:
            window_start, count = self._windows.get(key, (now, 0))
            if now - window_start >= self.window_seconds:
                window_start, count = now, 0

            count += 1
            self._windows[key] = (window_start, count)
            remaining = max(0, self.limit - count)
            return count <= self.limit, remaining
