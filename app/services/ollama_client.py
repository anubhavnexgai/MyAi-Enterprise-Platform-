"""Thin async client for a local Ollama daemon.

Only the bits the copilot endpoint needs:
- ``/api/chat`` for tool-less chat
- ``/api/tags`` for health/model discovery

The MyAi agent core has a richer client; this one is intentionally minimal so
the FastAPI app can degrade gracefully when the full agent isn't available.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, AsyncIterator, Dict, List, Optional

import aiohttp

from app.config import get_settings

logger = logging.getLogger(__name__)


class OllamaClient:
    def __init__(self, base_url: str, model: str, timeout: int = 120) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = aiohttp.ClientTimeout(total=timeout)

    async def health(self) -> bool:
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3)) as session:
                async with session.get(f"{self.base_url}/api/tags") as resp:
                    return resp.status == 200
        except Exception:
            return False

    async def chat(
        self,
        messages: List[Dict[str, str]],
        *,
        model: Optional[str] = None,
        options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload = {
            "model": model or self.model,
            "messages": messages,
            "stream": False,
        }
        if options:
            payload["options"] = options
        async with aiohttp.ClientSession(timeout=self.timeout) as session:
            async with session.post(f"{self.base_url}/api/chat", json=payload) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    raise RuntimeError(f"Ollama chat failed: HTTP {resp.status}: {text[:200]}")
                return await resp.json()

    async def chat_stream(
        self,
        messages: List[Dict[str, str]],
        *,
        model: Optional[str] = None,
    ) -> AsyncIterator[str]:
        payload = {
            "model": model or self.model,
            "messages": messages,
            "stream": True,
        }
        async with aiohttp.ClientSession(timeout=self.timeout) as session:
            async with session.post(f"{self.base_url}/api/chat", json=payload) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"Ollama stream failed: HTTP {resp.status}")
                async for raw in resp.content:
                    line = raw.decode("utf-8").strip()
                    if not line:
                        continue
                    yield line


@lru_cache
def get_ollama_client() -> OllamaClient:
    s = get_settings()
    return OllamaClient(s.ollama_base_url, s.ollama_model, s.ollama_timeout)
