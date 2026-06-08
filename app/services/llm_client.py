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
import os
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
        self.embed_model = os.environ.get("EMBED_MODEL", "nomic-embed-text")
        # Embeddings always run on local Ollama by default: most chat providers
        # (incl. OpenRouter) don't serve nomic-embed-text, so pointing embeddings
        # at the chat base_url 400s. Override with EMBED_BASE_URL if needed.
        self.embed_base_url = (
            os.environ.get("EMBED_BASE_URL") or settings.ollama_base_url
        ).rstrip("/")
        # Ordered free-model fallbacks for openai_compat (e.g. OpenRouter free
        # models flap with 429s). On a 429/5xx the chat call retries the next.
        self.fallback_models = [
            m.strip() for m in os.environ.get("LLM_FALLBACK_MODELS", "").split(",") if m.strip()
        ]

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

    async def embed(
        self, texts: List[str], *, model: Optional[str] = None
    ) -> List[List[float]]:
        """Embed a list of texts → list of float vectors. Fail-soft: returns []
        on any error (callers treat empty as 'embeddings unavailable')."""
        clean = [t for t in (texts or []) if t and t.strip()]
        if not clean:
            return []
        try:
            # Embeddings run on local Ollama regardless of the chat provider.
            return await self._ollama_embed(clean, model or self.embed_model)
        except Exception as exc:  # noqa: BLE001 — embeddings are optional
            logger.warning("embed failed (semantic memory will no-op): %s", exc)
            return []

    async def _ollama_embed(self, texts: List[str], model: str) -> List[List[float]]:
        out: List[List[float]] = []
        base = self.embed_base_url
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            # Prefer the batch /api/embed; fall back to per-text /api/embeddings.
            try:
                r = await c.post(f"{base}/api/embed",
                                 json={"model": model, "input": texts})
                if r.status_code < 400:
                    data = r.json()
                    embs = data.get("embeddings")
                    if embs:
                        return embs
            except Exception:  # noqa: BLE001
                pass
            for t in texts:
                r = await c.post(f"{base}/api/embeddings",
                                 json={"model": model, "prompt": t})
                r.raise_for_status()
                out.append(r.json().get("embedding") or [])
        return out

    async def _openai_compat_embed(self, texts: List[str], model: str) -> List[List[float]]:
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            r = await c.post(f"{self.base_url}/embeddings",
                             json={"model": model, "input": texts},
                             headers=self._headers())
            r.raise_for_status()
            data = r.json()
        return [item.get("embedding") or [] for item in (data.get("data") or [])]

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

        # Try the requested model, then each fallback, on transient failures
        # (429 rate-limit / 5xx / timeout). Free models flap, so this keeps the
        # assistant responsive by hopping to the next available free model.
        primary = model or self.model
        candidates = [primary] + [m for m in self.fallback_models if m != primary]
        _RETRY = {429, 500, 502, 503, 504}
        last_exc: Optional[Exception] = None
        async with httpx.AsyncClient(timeout=self.timeout) as c:
            for cand in candidates:
                body["model"] = cand
                try:
                    r = await c.post(
                        f"{self.base_url}/chat/completions",
                        json=body,
                        headers=self._headers(),
                    )
                    if r.status_code in _RETRY:
                        last_exc = httpx.HTTPStatusError(
                            f"{r.status_code} from {cand}", request=r.request, response=r
                        )
                        logger.warning("llm %s -> HTTP %s; trying next model", cand, r.status_code)
                        continue
                    r.raise_for_status()
                    data = r.json()
                except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.TransportError) as e:
                    last_exc = e
                    logger.warning("llm call to %s failed (%s); trying next model", cand, e)
                    continue
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
                    "model": cand,
                    "_raw": data,
                }
        # All candidates failed.
        raise last_exc or RuntimeError("LLM chat failed: no candidates")


_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
