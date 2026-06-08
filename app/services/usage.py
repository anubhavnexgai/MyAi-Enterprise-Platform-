"""Per-employee LLM usage accounting (Milestone 2 — real tokens).

A thin wrapper over the audit service that records one ``AuditLog`` row per LLM
call with real token counts in the payload, scoped by ``(tenant_id, creator_id)``.
This is what makes the super-admin dashboard (``app/api/admin.py``) able to show
each employee's true token consumption — including the Odysseus-bridged chat /
agent / research features, which previously bypassed analytics entirely.

Event types written here:
  - ``oui.chat``     — a chat-mode turn via the Odysseus bridge
  - ``oui.agent``    — an agent-mode turn (tool use) via the bridge
  - ``oui.research`` — a deep-research run via the bridge

Token-bearing events (the set above plus the native ``copilot.chat``) are summed
by the admin aggregation. All failures are swallowed: usage accounting must never
break a user's chat stream.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.services.audit import get_audit_service

logger = logging.getLogger(__name__)

# Kept in sync with app/api/admin.py (_TOKEN_EVENTS / _CHAT_EVENTS).
FEATURE_CHAT = "oui.chat"
FEATURE_AGENT = "oui.agent"
FEATURE_RESEARCH = "oui.research"


async def record_usage_event(
    *,
    tenant_id: str,
    creator_id: str,
    feature: str,
    model: Optional[str] = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    elapsed_ms: Optional[int] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    """Record one LLM usage event for per-employee analytics.

    Args:
        tenant_id: Tenant scope (``user.tenant_id``).
        creator_id: Employee id (``user.sub``).
        feature: Event type, e.g. ``oui.chat`` / ``oui.agent`` / ``oui.research``.
        model: Model name (e.g. ``qwen2.5:3b``) for per-model rollups.
        input_tokens / output_tokens: Prompt / completion token counts.
        elapsed_ms: Wall time of the call, if known.
        metadata: Extra payload fields (tokens_per_second, session, etc.).
    """
    try:
        in_t = int(input_tokens or 0)
        out_t = int(output_tokens or 0)
        payload: Dict[str, Any] = {
            "input_tokens": in_t,
            "output_tokens": out_t,
            "total_tokens": in_t + out_t,
        }
        if model:
            payload["model"] = model
        if elapsed_ms is not None:
            payload["elapsed_ms"] = int(elapsed_ms)
        if metadata:
            payload.update({k: v for k, v in metadata.items() if v is not None})

        await get_audit_service().log(
            tenant_id=tenant_id,
            user_id=creator_id,
            event_type=feature,
            message=f"{feature} · {model or 'model'} · {in_t + out_t} tok",
            payload=payload,
        )
    except Exception as exc:  # noqa: BLE001 — analytics must never break the request
        logger.warning(
            "usage.record_usage_event failed tenant=%s feature=%s: %s",
            tenant_id, feature, exc,
        )
