from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any


class AuditSink:
    def __init__(self, output_path: Path) -> None:
        self.output_path = output_path
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._worker())

    async def stop(self) -> None:
        await self._queue.put(None)
        if self._task is not None:
            await self._task
            self._task = None

    async def enqueue(self, payload: dict[str, Any]) -> None:
        await self._queue.put(payload)

    async def _worker(self) -> None:
        while True:
            item = await self._queue.get()
            if item is None:
                break
            line = json.dumps(item, ensure_ascii=False)
            await asyncio.to_thread(self._append_line, line)

    def _append_line(self, line: str) -> None:
        with open(self.output_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
