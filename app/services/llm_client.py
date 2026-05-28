"""Unified LLM client. Switches between Ollama (local) and OpenAI-compatible
endpoints (vLLM / Together / Anyscale / hosted NexgAI SLM) via env vars.

Single env switch — zero code changes between local and cloud:

    LLM_PROVIDER=ollama        # local
    LLM_PROVIDER=openai_compat # cloud
    LLM_BASE_URL=https://your-slm.nexgai.cloud/v1
    LLM_API_KEY=sk-...
    LLM_MODEL=nexgai-slm-7b
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class LLMClient:
    """Provider-agnostic chat client. Same interface for both backends."""

    def __init__(self) -> None:
        self.provider = settings.effective_llm_provider
        self.base_url = settings.effective_llm_base_url.rstrip("/")
        self.model = settings.effective_llm_model
        self.api_key = settings.llm_api_key
        self.timeout = settings.llm_timeout or settings.ollama_timeout

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        *,
        model: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        stream: bool = False,
    ) -> Dict[str, Any]:
        """Send a chat completion. Returns a dict with 'message' key (Ollama-shaped),
        and optionally 'tool_calls'."""
        if self.provider == "ollama":
            return await self._ollama_chat(messages, model, tools, temperature, max_tokens)
        return await self._openai_compat_chat(messages, model, tools, temperature, max_tokens)

    async def health_check(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                if self.provider == "ollama":
                    r = await c.get(f"{self.base_url}/api/tags")
                else:
                    headers = self._headers()
                    r = await c.get(f"{self.base_url}/models", headers=headers)
                return r.status_code == 200
        except Exception:
            return False

    async def list_models(self) -> List[Dict[str, Any]]:
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                if self.provider == "ollama":
                    r = await c.get(f"{self.base_url}/api/tags")
                    return r.json().get("models", [])
                r = await c.get(f"{self.base_url}/models", headers=self._headers())
                return r.json().get("data", [])
        except Exception as e:
            logger.warning("list_models failed: %s", e)
            return []

    # ─── Internals ───────────────────────────────────────────────────────

    def _headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    async def _ollama_chat(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str],
        tools: Optional[List[Dict[str, Any]]],
        temperature: float,
        max_tokens: Optional[int],
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature},
        }
        if max_tokens:
            body["options"]["num_predict"] = max_tokens
        if tools:
            body["tools"] = tools
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.post(f"{self.base_url}/api/chat", json=body)
            r.raise_for_status()
            return r.json()

    async def _openai_compat_chat(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str],
        tools: Optional[List[Dict[str, Any]]],
        temperature: float,
        max_tokens: Optional[int],
    ) -> Dict[str, Any]:
        """Talk to an OpenAI-compatible /v1/chat/completions endpoint and
        return a response shaped like Ollama's (so the rest of the app
        doesn't care which provider answered)."""
        body: Dict[str, Any] = {
            "model": model or self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens:
            body["max_tokens"] = max_tokens
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.post(
                f"{self.base_url}/chat/completions",
                json=body,
                headers=self._headers(),
            )
            r.raise_for_status()
            data = r.json()
        # Re-shape OpenAI -> Ollama-style so callers stay uniform.
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message", {})
        return {
            "message": {
                "role": msg.get("role", "assistant"),
                "content": msg.get("content") or "",
                "tool_calls": msg.get("tool_calls") or [],
            },
            "done": choice.get("finish_reason") in ("stop", "tool_calls"),
            "_raw": data,
        }


_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
