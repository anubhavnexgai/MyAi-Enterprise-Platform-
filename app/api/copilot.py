"""Copilot chat endpoint.

Uses a **pre-intercept** approach for tool calling (small local LLMs are
unreliable at native tool calls). Before asking the LLM, we detect the user's
intent (email, calendar, drive, drafts) and call the relevant connector tool
directly. The fetched data is injected as a system message so the model
*has* the user's real data to ground its reply.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from fastapi import APIRouter, Depends, HTTPException, Request

from app.agent.connector_tools import (
    calendar_list_events,
    drive_search,
    gmail_search,
)
from app.agent.outlook_tools import outlook_search_summary
from app.auth.jwt import PlatformTokenClaims
from app.auth.middleware import get_current_user
from app.models.schemas import ChatRequest, ChatResponse
from app.services.audit import get_audit_service
from app.services.connector_manager import get_connector_manager
from app.services.llm_client import get_llm_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/copilot", tags=["copilot"])


# ---------------------------------------------------------------------------
# Intent detection (pre-intercept)
# ---------------------------------------------------------------------------

_GMAIL_HINTS = re.compile(
    r"\b(email|emails|inbox|unread|mail|message|messages|reply|gmail|"
    r"sender|subject|thread|threads|notification)\b",
    re.I,
)
_CALENDAR_HINTS = re.compile(
    r"\b(calendar|meeting|meetings|schedule|today|tomorrow|week|event|events|"
    r"agenda|free|busy|appointment|invite|invites|standup|sync|call|conflict)\b",
    re.I,
)
_DRIVE_HINTS = re.compile(
    r"\b(drive|file|files|doc|docs|document|sheet|sheets|slide|slides|pdf|"
    r"folder|prd)\b",
    re.I,
)
_DRAFT_HINTS = re.compile(
    r"\b(draft|drafting|status update|sprint update|weekly update|recap|"
    r"summary|summarize)\b",
    re.I,
)
_GMAIL_SEND_HINTS = re.compile(
    r"\b(send|reply|email back|respond|forward)\b.*\b(to|@)\b", re.I
)


async def _gather_grounding(
    message: str, user_id: str, tenant_id: str
) -> Tuple[Dict[str, str], List[str]]:
    """Fetch real connector data based on the user's message intent.

    Returns (context_blocks, sources_used).
    """
    msg = message.lower()
    blocks: Dict[str, str] = {}
    sources: List[str] = []

    wants_email = bool(_GMAIL_HINTS.search(msg))
    wants_calendar = bool(_CALENDAR_HINTS.search(msg))
    wants_drive = bool(_DRIVE_HINTS.search(msg))
    wants_draft = bool(_DRAFT_HINTS.search(msg))

    # A status-update draft needs both unread and recent sent context.
    if wants_draft:
        wants_email = True

    if wants_email:
        # "unread" if explicit, else "newer_than:7d" for broader context
        q = "is:unread" if "unread" in msg else "newer_than:7d in:inbox"
        if "important" in msg or "need" in msg and "reply" in msg:
            q = "is:unread is:important"
        try:
            result = await gmail_search(
                user_id=user_id, query=q, limit=12, tenant_id=tenant_id
            )
            if "haven't connected" not in result.lower():
                blocks["GMAIL_INBOX"] = result
                sources.append("Gmail")
        except Exception as exc:
            logger.warning("gmail grounding failed: %s", exc)
        # Also Outlook if connected
        try:
            o = await outlook_search_summary(
                user_id=user_id, query="", limit=10, tenant_id=tenant_id
            )
            if "not connected" not in o.lower():
                blocks["OUTLOOK_INBOX"] = o
                sources.append("Outlook")
        except Exception as exc:
            logger.warning("outlook grounding failed: %s", exc)

    if wants_draft:
        # Recent sent items help model match tone/style
        try:
            result = await gmail_search(
                user_id=user_id,
                query="in:sent newer_than:14d",
                limit=6,
                tenant_id=tenant_id,
            )
            blocks["GMAIL_RECENT_SENT"] = result
            sources.append("Gmail Sent")
        except Exception as exc:
            logger.warning("gmail sent grounding failed: %s", exc)

    if wants_calendar:
        try:
            result = await calendar_list_events(
                user_id=user_id, days_ahead=7, tenant_id=tenant_id
            )
            blocks["CALENDAR_UPCOMING"] = result
            sources.append("Google Calendar")
        except Exception as exc:
            logger.warning("calendar grounding failed: %s", exc)

    if wants_drive:
        # Try to extract a quoted or after-"for"/"about" query
        m = re.search(r"(?:for|about|titled|named)\s+['\"]?([^'\"?.!,]+)", message, re.I)
        q = (m.group(1).strip() if m else "").split()[:6]
        query = " ".join(q) or "recent"
        try:
            result = await drive_search(
                user_id=user_id, query=query, limit=8, tenant_id=tenant_id
            )
            blocks["DRIVE_RESULTS"] = result
            sources.append("Google Drive")
        except Exception as exc:
            logger.warning("drive grounding failed: %s", exc)

    return blocks, sources


def _system_prompt(
    user: PlatformTokenClaims,
    connected: List[str],
    today_iso: str,
    autonomy_level: int = 1,
) -> str:
    name = user.full_name or user.username or user.email
    first = name.split()[0] if name else "Me"
    connectors_line = (
        ", ".join(connected) if connected else "(none — user can connect on /connectors)"
    )
    aut_label = {
        1: "L1 Observe (READ-ONLY; suggest, never act)",
        2: "L2 Draft Assist (draft replies; do not send or modify)",
        3: "L3 Augmented (propose actions, ask before acting)",
        4: "L4 Guarded Auto (auto-handle low-risk; ask for high-risk)",
        5: "L5 Autonomous (full auto with audit trail)",
    }.get(autonomy_level, "L1 Observe")
    return (
        f"You are MyAi, the personal AI assistant for {name} ({user.email}). "
        f"Today is {today_iso}.\n"
        f"Connected accounts you can read from: {connectors_line}.\n"
        f"Current autonomy: {aut_label}.\n\n"
        "BEHAVIOR PER AUTONOMY LEVEL:\n"
        f"  - At L1, you NEVER take actions — you observe and surface insight only. "
        "If asked to send/archive/delete, refuse politely and explain the user "
        "is in Observe mode.\n"
        "  - At L2, you may write drafts but tell the user 'here's a draft, "
        "I haven't sent it.'\n"
        "  - At L3+, you may propose to act and ask 'should I do this?'\n\n"
        "GROUNDING RULES — these are not optional:\n"
        "1. If a 'CONTEXT FROM USER'S CONNECTED ACCOUNTS' block is provided in this "
        "conversation, treat it as the user's actual current data. Quote real "
        "names, subjects, times, and senders FROM THAT BLOCK. Never invent.\n"
        "2. ABSOLUTE BAN ON PLACEHOLDERS. You MUST NEVER write text inside "
        "square brackets, curly braces, angle brackets, or 'TODO' markers — "
        "for example: [Your Name], [Date], [Project X], [Manager's Name], "
        "{name}, <recipient>, TBD, TODO, XYZ Corp. If you would otherwise "
        "use a placeholder, instead either (a) use a real value from the "
        "context block, (b) use a generic but real word like 'Manager' or "
        "'the team' WITH NO BRACKETS, or (c) ask the user a clarifying "
        "question.\n"
        f"3. The user's name is {name}. ALWAYS sign drafts with '{first}' "
        "(do not write [Your Name] or [Sender]).\n"
        f"4. Today's date is {today_iso}. When you need a date, use this one "
        "(or a real date from the context block). Never write [Date] or [Today].\n"
        "5. For drafting work updates / status emails: write the draft as a "
        f"complete, sendable email signed by {first}. If you do not know the "
        "recipient's name, address them as 'Hi Manager,' or 'Hi team,' (NO "
        "brackets). If you don't know the specific project, refer to 'this "
        "week's work' or 'current work' generically — NEVER write [Project Name].\n"
        "6. Be concise. For lists, use short bullets with the real "
        "subject/sender/time from the context block.\n"
        "7. If a service is not connected and the user asks about it, tell "
        "them to connect it on the Connectors page — do not fabricate data."
    )


# Regex used to scrub any placeholder the model still emits despite the prompt.
_PLACEHOLDER_RE = re.compile(
    r"\[(?P<inner>(?:Your |Sender|Recipient|Manager|Project|Date|Today|"
    r"Today's date|Company|Team|Department|Boss|Name|Title|Position|"
    r"Subject|Address|Contact|Insert)[^\]]{0,40})\]"
    r"|\{\{?[A-Za-z_ ]{1,40}\}?\}",
    re.IGNORECASE,
)

_DEFAULT_SUBS = {
    "name": "{first}",
    "your name": "{first}",
    "sender": "{first}",
    "manager": "Manager",
    "manager's name": "Manager",
    "recipient": "Manager",
    "date": "{today}",
    "today": "{today}",
    "today's date": "{today}",
    "project": "this work",
    "project name": "this work",
    "team": "the team",
    "company": "the team",
}


def _scrub_placeholders(text: str, first_name: str, today_iso: str) -> str:
    """Replace any bracketed placeholders the LLM emitted with safe defaults.

    This is a safety net — the prompt already tells the model not to use
    placeholders, but small local models sometimes do anyway.
    """
    def _sub(match: re.Match) -> str:
        inner = (match.group("inner") or match.group(0)).strip().strip("{}").lower()
        for key, val in _DEFAULT_SUBS.items():
            if key in inner:
                return val.format(first=first_name, today=today_iso)
        # Generic fallback — drop the brackets but keep the word
        return inner.title()

    return _PLACEHOLDER_RE.sub(_sub, text)


# ---------------------------------------------------------------------------
# Chat endpoint
# ---------------------------------------------------------------------------


@router.post("/chat", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    request: Request,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> ChatResponse:
    if not payload.message.strip():
        raise HTTPException(status_code=400, detail="message is required")

    started = time.time()
    today_iso = datetime.now(timezone.utc).strftime("%A, %d %B %Y")

    # Discover which connectors are live for this user so the system prompt
    # tells the model what data is available.
    cm = get_connector_manager()
    try:
        connections = await cm.list_connections(user.sub, user.tenant_id)
        connected_display = [
            c["display_name"] for c in connections if c.get("connected")
        ]
    except Exception:
        connected_display = []

    # Look up autonomy level so the prompt can shape behaviour
    from app.storage.models import UserPreference
    from app.tenants.router import get_tenant_router
    from sqlalchemy import select as _sel

    autonomy_level = 1
    try:
        router_db = get_tenant_router()
        async with router_db.session_for(user.tenant_id) as session:
            pref = (
                await session.execute(
                    _sel(UserPreference)
                    .where(UserPreference.tenant_id == user.tenant_id)
                    .where(UserPreference.creator_id == user.sub)
                )
            ).scalars().first()
            if pref:
                autonomy_level = int(pref.autonomy_level)
    except Exception:
        pass

    # Pre-intercept: fetch grounding data BEFORE the LLM call.
    grounding_blocks, sources_used = await _gather_grounding(
        payload.message, user.sub, user.tenant_id
    )

    messages: List[Dict[str, str]] = [
        {
            "role": "system",
            "content": _system_prompt(
                user, connected_display, today_iso, autonomy_level
            ),
        }
    ]

    if grounding_blocks:
        ctx = "CONTEXT FROM USER'S CONNECTED ACCOUNTS (use these real values):\n\n"
        for label, body in grounding_blocks.items():
            ctx += f"--- {label} ---\n{body}\n\n"
        messages.append({"role": "system", "content": ctx})

    for m in payload.history:
        if m.role in {"user", "assistant", "system"}:
            messages.append({"role": m.role, "content": m.content})
    messages.append({"role": "user", "content": payload.message})

    reply = ""
    model = None
    try:
        client = get_llm_client()
        result = await client.chat(messages)
        msg = (result or {}).get("message") or {}
        reply = msg.get("content", "") or ""
        model = (result or {}).get("model") or client.model
    except Exception as e:
        logger.exception("LLM call failed")
        raise HTTPException(
            status_code=503, detail=f"LLM backend unavailable: {e}"
        )

    # Belt-and-suspenders: scrub any [bracketed placeholders] the small local
    # model snuck in. The prompt forbids them but qwen2.5:7b occasionally
    # ignores the instruction.
    first = (user.full_name or user.username or user.email or "Me").split()[0]
    reply = _scrub_placeholders(reply, first, today_iso)

    elapsed_ms = int((time.time() - started) * 1000)

    await get_audit_service().log(
        tenant_id=user.tenant_id,
        user_id=user.sub,
        event_type="copilot.chat",
        message=payload.message[:200],
        payload={
            "reply_chars": len(reply),
            "model": model,
            "elapsed_ms": elapsed_ms,
            "grounding": sources_used,
        },
    )

    return ChatResponse(
        reply=reply,
        tool_calls=[{"name": s, "type": "grounding"} for s in sources_used],
        model=model,
        used_fallback=False,
        elapsed_ms=elapsed_ms,
    )


@router.get("/health")
async def copilot_health() -> Dict[str, Any]:
    client = get_llm_client()
    ok = await client.health_check()
    return {
        "provider": client.provider,
        "status": "up" if ok else "down",
        "model": client.model,
    }
