from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass

import httpx


class LLMUnavailableError(RuntimeError):
    """Raised when the local Ollama LLM is unavailable or times out."""


@dataclass(slots=True)
class LLMConfig:
    base_url: str = os.getenv("OLLAMA_URL", "http://localhost:11434")
    model: str = os.getenv("OLLAMA_MODEL", "meditron")
    timeout_seconds: float = float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "8"))
    max_attempts: int = int(os.getenv("OLLAMA_MAX_ATTEMPTS", "2"))
    retry_backoff_seconds: float = float(os.getenv("OLLAMA_RETRY_BACKOFF_SECONDS", "1"))
    max_concurrent_requests: int = int(os.getenv("OLLAMA_MAX_CONCURRENT_REQUESTS", "2"))


class OllamaClient:
    def __init__(self, config: LLMConfig | None = None) -> None:
        self.config = config or LLMConfig()
        self._semaphore = asyncio.Semaphore(max(1, self.config.max_concurrent_requests))

    async def generate(self, prompt: str, system_prompt: str | None = None) -> str:
        payload = {
            "model": self.config.model,
            "prompt": prompt,
            "stream": False,
        }
        if system_prompt:
            payload["system"] = system_prompt

        last_exc: Exception | None = None

        async with self._semaphore:
            for attempt in range(1, self.config.max_attempts + 1):
                try:
                    async with httpx.AsyncClient(timeout=self.config.timeout_seconds) as client:
                        response = await client.post(f"{self.config.base_url}/api/generate", json=payload)
                        response.raise_for_status()
                        data = response.json()
                        content = data.get("response", "")
                        if not isinstance(content, str):
                            raise LLMUnavailableError("Invalid LLM response format")
                        return content
                except (httpx.TimeoutException, httpx.ConnectError, httpx.NetworkError, httpx.HTTPStatusError) as exc:
                    last_exc = exc
                    if attempt < self.config.max_attempts:
                        await asyncio.sleep(self.config.retry_backoff_seconds)
                        continue
                    raise LLMUnavailableError("Ollama meditron unavailable or timed out") from exc

        raise LLMUnavailableError("Ollama meditron unavailable") from last_exc

    async def warmup(self) -> bool:
        try:
            await self.generate("Return an empty JSON object: {}")
            return True
        except LLMUnavailableError:
            return False

    async def health(self) -> dict[str, str | bool]:
        ok = await self.warmup()
        return {
            "model": self.config.model,
            "base_url": self.config.base_url,
            "available": ok,
        }
