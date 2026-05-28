"""Copilot chat endpoint.

Tries to use the full MyAi agent (copied into ``app.agent``); if that import or
initialisation fails (which it often will outside MyAi's runtime), falls back
to a thin Ollama passthrough so the chat surface still works.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth.jwt import PlatformTokenClaims
from app.auth.middleware import get_current_user
from app.models.schemas import ChatRequest, ChatResponse
from app.services.audit import get_audit_service
from app.services.llm_client import get_llm_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/copilot", tags=["copilot"])


def _system_prompt(user: PlatformTokenClaims) -> str:
    return (
        "You are MyAi, an enterprise copilot for "
        f"{user.full_name or user.username} ({user.email}) at tenant "
        f"'{user.tenant_id}'. Always answer in the user's own context - their "
        "tasks, customers, and data. Be concise, professional, and propose "
        "next actions when relevant."
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    request: Request,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> ChatResponse:
    if not payload.message.strip():
        raise HTTPException(status_code=400, detail="message is required")

    started = time.time()

    messages: List[Dict[str, str]] = [{"role": "system", "content": _system_prompt(user)}]
    for m in payload.history:
        if m.role in {"user", "assistant", "system"}:
            messages.append({"role": m.role, "content": m.content})
    messages.append({"role": "user", "content": payload.message})

    used_fallback = True
    reply = ""
    model = None
    tool_calls: List[Dict[str, Any]] = []

    # Try the full MyAi agent first; fall back to Ollama passthrough.
    try:
        # Importing here avoids the heavy agent dependency tree on app boot
        from app.agent import AgentCore  # noqa: F401 - presence check only

        # The full agent requires a Database + ToolRegistry that aren't wired
        # up in this skeleton yet. Until that integration lands, fall through
        # to the Ollama passthrough to keep the chat usable.
        raise RuntimeError("AgentCore wiring is pending; using Ollama fallback")
    except Exception as e:
        logger.debug("Agent unavailable, using Ollama passthrough: %s", e)

    try:
        client = get_llm_client()
        result = await client.chat(messages)
        # Both Ollama and OpenAI-compat paths return the same shape:
        # {"message": {"role": "assistant", "content": "..."}}
        msg = (result or {}).get("message") or {}
        reply = msg.get("content", "") or ""
        model = (result or {}).get("model") or client.model
        tool_calls = msg.get("tool_calls") or []
    except Exception as e:
        logger.exception("LLM call failed")
        raise HTTPException(
            status_code=503,
            detail=f"LLM backend unavailable: {e}",
        )

    elapsed_ms = int((time.time() - started) * 1000)

    await get_audit_service().log(
        tenant_id=user.tenant_id,
        user_id=user.sub,
        event_type="copilot.chat",
        message=payload.message[:200],
        payload={"reply_chars": len(reply), "model": model, "elapsed_ms": elapsed_ms},
    )

    return ChatResponse(
        reply=reply,
        tool_calls=tool_calls,
        model=model,
        used_fallback=used_fallback,
        elapsed_ms=elapsed_ms,
    )


@router.get("/health")
async def copilot_health() -> Dict[str, Any]:
    client = get_llm_client()
    ok = await client.health_check()
    return {"provider": client.provider, "status": "up" if ok else "down", "model": client.model}
