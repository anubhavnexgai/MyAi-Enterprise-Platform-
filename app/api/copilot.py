"""Copilot chat endpoint.

Uses a **pre-intercept** approach for tool calling (small local LLMs are
unreliable at native tool calls). Before asking the LLM, we detect the user's
intent (email, calendar, drive, drafts) and call the relevant connector tool
directly. The fetched data is injected as a system message so the model
*has* the user's real data to ground its reply.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.agent.connector_tools import (
    calendar_list_events,
    drive_search,
    gmail_search,
)
from app.agent.outlook_tools import outlook_search_summary
from app.auth.jwt import PlatformTokenClaims
from app.auth.middleware import get_current_user
from app.models.schemas import (
    ChatRequest,
    ChatResponse,
    MailSuggestRequest,
    MailSuggestResponse,
)
from app.services.audit import get_audit_service
from app.services.connector_manager import get_connector_manager
from app.services.llm_client import get_llm_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/copilot", tags=["copilot"])

# Strong refs to fire-and-forget background tasks. asyncio keeps only a WEAK ref
# to a bare create_task, so without this the task can be GC'd before it finishes.
_bg_tasks: set = set()


def _spawn_bg(coro) -> None:
    t = asyncio.create_task(coro)
    _bg_tasks.add(t)
    t.add_done_callback(_bg_tasks.discard)


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
# Questions that need CURRENT, real-world info the local model can't know from
# its stale training data — route these through live web search.
_WEB_RESEARCH_HINTS = re.compile(
    r"\b(research|latest|recent|newest|current|today|tonight|this week|this month|"
    r"this year|right now|up[- ]?to[- ]?date|breaking|news|headlines|trending|"
    r"released?|launch(?:ed|ing)?|announce(?:d|ment)?|price of|stock|score|who won|"
    r"what happened|happening|202[4-9]|203\d)\b",
    re.I,
)


def _wants_web_research(message: str) -> bool:
    """True if the question needs live web data (current events, prices, news).

    Excludes questions about the user's own connected data — those are grounded
    via the connectors instead.
    """
    msg = message.lower()
    if _GMAIL_HINTS.search(msg) or _CALENDAR_HINTS.search(msg) or _DRIVE_HINTS.search(msg):
        return False
    return bool(_WEB_RESEARCH_HINTS.search(msg))


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
        except Exception as exc:
            logger.warning("gmail grounding failed: %s", exc)
            result = "Gmail could not be read right now."
        # Always inject a block so the model is grounded in reality (real
        # messages, "no messages", or "not connected") and cannot invent email.
        blocks["GMAIL_INBOX"] = result
        if "haven't connected" not in result.lower() and "could not be read" not in result.lower():
            sources.append("Gmail")
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
        except Exception as exc:
            logger.warning("calendar grounding failed: %s", exc)
            result = "Calendar could not be read right now."
        # Always inject a block — if there are no events (or it's not connected),
        # the model must say so, never invent meetings.
        blocks["CALENDAR_UPCOMING"] = result
        if "haven't connected" not in result.lower() and "could not be read" not in result.lower():
            sources.append("Google Calendar")

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
        "them to connect it on the Connectors page — do not fabricate data.\n"
        "8. LIVE WEB RESULTS: If a 'WEB SEARCH RESULTS' block is present, it is "
        "real, current web data. Answer using ONLY those results, cite the "
        "relevant URLs inline, and prefer the most recent. If the results do "
        "not cover the question, say so — do NOT fall back to your training "
        "data and do NOT invent sources or links.\n"
        "9. NO FABRICATION: If a context block shows no items, says 'not "
        "connected', or 'could not be read', tell the user exactly that. NEVER "
        "invent emails, meetings, events, names, dates, prices, or facts that "
        "are not present in a context block above. Saying 'I don't see any "
        "events' or 'I couldn't read that' is the correct, expected answer."
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

    # Fully agentic: no pre-fetch. The agent decides which tools to call
    # (search_email / list_calendar / search_drive / web_search / fetch_url) to
    # reach the goal — the Life-Harness layers keep tool use reliable, and each
    # successful multi-tool turn is learned. Connected accounts are named in the
    # system prompt so the model knows what it can reach.
    grounding_blocks: Dict[str, str] = {}
    sources_used: List[str] = []

    # If the message names a known email contact, pull that sender's precomputed
    # memory. For a pure recall question ("what's the latest with X?") answer
    # directly from it (reliable, instant). Otherwise inject it as context so the
    # agent can use it while still being free to act.
    try:
        from app.services.harvester_worker import recall_contacts
        hits = await recall_contacts(user.sub, user.tenant_id, payload.message, top_k=2)
        if hits:
            mem = ("CONTACT MEMORY — what you already know about senders the user "
                   "mentioned (use this to answer; it's precomputed from their inbox):\n\n")
            for c in hits:
                mem += (f"- {c['name']} <{c['addr']}> ({c['message_count']} recent emails, "
                        f"last {c['last_date']}): {c['summary']}\n")
            grounding_blocks["CONTACT_MEMORY"] = mem
            sources_used.append("Contact memory")

            if _is_contact_recall(payload.message):
                answer = await _answer_from_contact_memory(payload.message, mem)
                if answer:
                    first = (user.full_name or user.username or user.email or "Me").split()[0]
                    answer = _scrub_placeholders(answer, first, today_iso)
                    await get_audit_service().log(
                        tenant_id=user.tenant_id, user_id=user.sub,
                        event_type="copilot.chat", message=payload.message[:200],
                        payload={"source": "contact_memory", "model": get_llm_client().model},
                    )
                    return ChatResponse(
                        reply=answer, model=get_llm_client().model,
                        elapsed_ms=int((time.time() - started) * 1000),
                        tool_calls=[{"name": "Contact memory", "type": "grounding"}],
                        grounded="grounded",
                        citations=[{"source": "Contact memory", "label": "CONTACT_MEMORY"}],
                    )
    except Exception as e:
        logger.debug("contact memory path failed: %s", e)

    # Semantic memory (Phase 2): recall relevant snippets from past chats / data
    # and inject them so the agent has continuity across conversations. Fail-soft.
    try:
        from app.services.semantic_memory import recall_semantic
        mem_hits = await recall_semantic(user.sub, user.tenant_id, payload.message, k=3)
        if mem_hits:
            block = ("RELEVANT MEMORY — things from earlier conversations or your data "
                     "that may help answer this (use only if relevant):\n\n")
            for h in mem_hits:
                block += f"- {h['text'][:300]}\n"
            grounding_blocks["SEMANTIC_MEMORY"] = block
            if "Memory" not in sources_used:
                sources_used.append("Memory")
    except Exception as e:
        logger.debug("semantic recall failed: %s", e)

    # NOTE: web research is intentionally NOT pre-fetched here. The agent loop
    # decides for itself to call web_search / fetch_url (made reliable by the
    # Life-Harness layers), so it genuinely "figures out what to do" and the
    # turn becomes a learnable skill. Pre-fetching would short-circuit that.

    # Seed context = behaviour/grounding rules + any pre-fetched real data.
    # The agent loop can take further actions (search, fetch, act) on top of it.
    seed_parts = [_system_prompt(user, connected_display, today_iso, autonomy_level)]
    if grounding_blocks:
        ctx = "CONTEXT FROM USER'S CONNECTED ACCOUNTS / WEB (use these real values):\n\n"
        for label, body in grounding_blocks.items():
            ctx += f"--- {label} ---\n{body}\n\n"
        seed_parts.append(ctx)
    seed_context = "\n\n".join(seed_parts)

    history = [
        {"role": m.role, "content": m.content}
        for m in payload.history
        if m.role in {"user", "assistant", "system"}
    ]
    aut_label = {
        1: "L1 Observe (read-only)", 2: "L2 Draft Assist", 3: "L3 Augmented",
        4: "L4 Guarded Auto", 5: "L5 Autonomous",
    }.get(autonomy_level, "L1 Observe")

    reply = ""
    model = None
    tools_used: List[str] = []
    agents_used: List[str] = []
    orchestrated = False
    try:
        from app.services.agent_loop import run_agent
        from app.services.agents.orchestrator import run_orchestrator, should_orchestrate

        if should_orchestrate(payload.message):
            # Multi-domain goal → lead orchestrator: decompose, run specialists in
            # parallel, synthesize. Degrades to single-agent on a trivial plan.
            res = await run_orchestrator(
                payload.message, history,
                user=user, autonomy_label=aut_label, autonomy_level=autonomy_level,
                today_iso=today_iso, seed_context=seed_context,
            )
            reply = res["answer"]
            tools_used = res.get("tools_used", [])
            agents_used = res.get("agents_used", [])
            orchestrated = bool(res.get("orchestrated"))
        else:
            reply, tools_used = await run_agent(
                payload.message, history,
                user=user, autonomy_label=aut_label, autonomy_level=autonomy_level,
                today_iso=today_iso, seed_context=seed_context,
            )
        model = get_llm_client().model
    except Exception as e:
        logger.exception("agent loop failed")
        raise HTTPException(
            status_code=503, detail=f"LLM backend unavailable: {e}"
        )
    # Tools the agent invoked become part of the surfaced source list.
    for t in tools_used:
        if t not in sources_used:
            sources_used.append(t)

    # Learning loop: distill a reusable skill from this successful multi-tool
    # turn so the agent gets better at similar requests over time (best-effort).
    try:
        from app.services.auto_skill import try_extract_skill
        try_extract_skill(payload.message, tools_used, reply)
    except Exception as exc:
        logger.debug("skill extraction skipped: %s", exc)

    # Belt-and-suspenders: scrub any [bracketed placeholders] the small local
    # model snuck in. The prompt forbids them but qwen2.5:7b occasionally
    # ignores the instruction.
    first = (user.full_name or user.username or user.email or "Me").split()[0]
    reply = _scrub_placeholders(reply, first, today_iso)

    # Correctness spine (Pillar 1): for data-derived turns, verify the answer
    # against the fetched context and attach citations. Fail-open — a verifier
    # error never blocks the reply, it just yields an "unverified" verdict.
    from app.config import settings as _settings
    from app.services.grounding import ground_and_verify

    reply, verdict, citations = await ground_and_verify(
        payload.message,
        reply,
        grounding_blocks,
        sources_used,
        # Skip the single-pass verifier for orchestrated answers — they're
        # synthesized from multiple specialists' contexts the prefetch didn't see,
        # so verifying against grounding_blocks alone would mislabel them.
        enabled=_settings.grounding_verify_enabled and not orchestrated,
    )

    elapsed_ms = int((time.time() - started) * 1000)

    # Semantic memory write-back (Phase 2): remember this turn for future recall.
    # Fire-and-forget so the embedding call never delays the response. Dedup +
    # fail-soft live inside add_memory.
    try:
        from app.services.semantic_memory import add_memory
        _spawn_bg(add_memory(
            user.sub, user.tenant_id,
            f"Q: {payload.message}\nA: {reply[:800]}", kind="chat",
        ))
    except Exception as exc:
        logger.debug("memory write-back skipped: %s", exc)

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
            "grounded": verdict.status,
            "unsupported": verdict.unsupported,
        },
    )

    return ChatResponse(
        reply=reply,
        tool_calls=([{"name": s, "type": "grounding"} for s in sources_used]
                    + [{"name": a, "type": "agent"} for a in agents_used]),
        model=model,
        used_fallback=False,
        elapsed_ms=elapsed_ms,
        grounded=verdict.status,
        citations=citations,
        unsupported_claims=verdict.unsupported,
        agents_used=agents_used,
        orchestrated=orchestrated,
    )


async def _autonomy_level_for(user) -> int:
    from app.storage.models import UserPreference
    from app.tenants.router import get_tenant_router
    from sqlalchemy import select as _sel
    try:
        router_db = get_tenant_router()
        async with router_db.session_for(user.tenant_id) as session:
            pref = (await session.execute(
                _sel(UserPreference)
                .where(UserPreference.tenant_id == user.tenant_id)
                .where(UserPreference.creator_id == user.sub))).scalars().first()
            return int(pref.autonomy_level) if pref else 1
    except Exception:
        return 1


@router.post("/orchestrate", response_model=Dict[str, Any])
async def orchestrate(
    payload: ChatRequest,
    request: Request,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    """Explicit multi-agent run — always uses the lead orchestrator and returns
    the plan, per-step results, and the synthesized answer. Useful for testing
    and for a UI that wants to show the agents at work."""
    if not payload.message.strip():
        raise HTTPException(status_code=400, detail="message is required")
    from app.services.agents.orchestrator import run_orchestrator

    today_iso = datetime.now(timezone.utc).strftime("%A, %d %B %Y")
    autonomy_level = await _autonomy_level_for(user)
    aut_label = {1: "L1 Observe (read-only)", 2: "L2 Draft Assist", 3: "L3 Augmented",
                 4: "L4 Guarded Auto", 5: "L5 Autonomous"}.get(autonomy_level, "L1 Observe")
    history = [
        {"role": m.role, "content": m.content}
        for m in (payload.history or []) if m.role in {"user", "assistant", "system"}
    ]
    res = await run_orchestrator(
        payload.message, history,
        user=user, autonomy_label=aut_label, autonomy_level=autonomy_level,
        today_iso=today_iso,
    )
    await get_audit_service().log(
        tenant_id=user.tenant_id, user_id=user.sub, event_type="copilot.orchestrate",
        message=payload.message[:200],
        payload={"agents_used": res.get("agents_used"), "orchestrated": res.get("orchestrated"),
                 "elapsed_ms": res.get("elapsed_ms")},
    )
    return res


@router.post("/chat/stream")
async def chat_stream_endpoint(
    payload: ChatRequest,
    request: Request,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> StreamingResponse:
    """Token-streaming chat over the NATIVE agent (real connectors + tools).

    Emits SSE events: {"type":"tool","name"}, {"type":"delta","text"},
    {"type":"done","tools_used","answer"}, then `data: [DONE]`."""
    if not payload.message.strip():
        raise HTTPException(status_code=400, detail="message is required")
    from app.services.agent_loop import run_agent_stream

    today_iso = datetime.now(timezone.utc).strftime("%A, %d %B %Y")
    autonomy_level = await _autonomy_level_for(user)
    aut_label = {1: "L1 Observe (read-only)", 2: "L2 Draft Assist", 3: "L3 Augmented",
                 4: "L4 Guarded Auto", 5: "L5 Autonomous"}.get(autonomy_level, "L1 Observe")
    history = [
        {"role": m.role, "content": m.content}
        for m in (payload.history or []) if m.role in {"user", "assistant", "system"}
    ]
    msg = payload.message

    async def gen():
        try:
            async for ev in run_agent_stream(
                msg, history, user=user, autonomy_label=aut_label,
                autonomy_level=autonomy_level, today_iso=today_iso,
                model=(payload.model or None),
                mode=(payload.mode or "agent"),
                force_web=bool(payload.web),
            ):
                yield f"data: {json.dumps(ev)}\n\n"
        except Exception as exc:  # noqa: BLE001
            logger.exception("chat stream failed")
            yield f"data: {json.dumps({'type':'delta','text':'(Sorry — '+str(exc)[:120]+')'})}\n\n"
            yield f"data: {json.dumps({'type':'done','tools_used':[],'answer':''})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/models")
async def copilot_models(
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    """Models the native chat can use. The native client talks to OpenRouter, so
    we surface the FREE models (ids ending ':free') + the configured default."""
    import os as _os
    from app.services.llm_client import get_llm_client, is_chat_capable_model
    llm = get_llm_client()
    free: List[str] = []
    try:
        for m in await llm.list_models():
            mid = m.get("id") or m.get("name")
            # FREE chat-capable models only — exclude embed/safety/tts/image etc.
            if mid and str(mid).endswith(":free") and is_chat_capable_model(mid):
                free.append(mid)
    except Exception:  # noqa: BLE001
        pass
    free.sort()
    defaults = [llm.model] + [x.strip() for x in _os.environ.get("LLM_FALLBACK_MODELS", "").split(",") if x.strip()]
    ordered = [d for d in defaults if d] + [m for m in free if m not in defaults]
    seen: set = set()
    models = [m for m in ordered if not (m in seen or seen.add(m))]
    return {"default": llm.model, "models": models}


_CONTACT_RECALL_RE = re.compile(
    r"\b(latest|update|updates|recent|recently|news|status|owe|outstanding|pending|"
    r"who\s+is|what\s+does|what'?s\s+(?:the\s+)?(?:latest|new|up)|anything\s+(?:new|from)|"
    r"hear(?:d)?\s+from|going\s+on|last\s+(?:email|message)|catch\s+me\s+up)\b",
    re.I,
)


def _is_contact_recall(message: str) -> bool:
    """True if the message is a 'what's the latest with X' style recall question."""
    return bool(_CONTACT_RECALL_RE.search(message or ""))


async def _answer_from_contact_memory(message: str, memory_block: str) -> str:
    """Answer a recall question grounded ONLY in the precomputed contact memory."""
    sys = (
        "Answer the user's question about their email contact using ONLY the "
        "CONTACT MEMORY provided. Be concise and specific. If the memory does not "
        "cover the question, say you don't have recent information on that. Never "
        "invent facts or web information."
    )
    try:
        result = await get_llm_client().chat(
            [{"role": "system", "content": sys},
             {"role": "user", "content": f"{memory_block}\n\nQuestion: {message}"}],
            temperature=0.2, max_tokens=240,
        )
        return (((result or {}).get("message") or {}).get("content", "") or "").strip()
    except Exception as e:
        logger.debug("contact answer failed: %s", e)
        return ""


@router.post("/suggest", response_model=MailSuggestResponse)
async def suggest_mail_action(
    payload: MailSuggestRequest,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> MailSuggestResponse:
    """Fast triage: read one email and suggest the single best next action.

    A single tool-free LLM call (not the agent loop), so it returns quickly when
    a mail is opened. Grounded in the supplied email text only — no fabrication.
    """
    # Instant path: return the background-computed suggestion if we have it.
    if payload.message_id:
        try:
            from app.services.harvester_worker import cached_suggestion
            c = await cached_suggestion(user.sub, user.tenant_id, payload.message_id)
            if c:
                return MailSuggestResponse(suggestion=c["suggestion"], action=c.get("action"))
        except Exception as e:
            logger.debug("cached suggestion lookup failed: %s", e)

    # Fallback: compute on demand, then persist so it's instant next time.
    from app.services.mail_ai import generate_mail_suggestion

    text, action = await generate_mail_suggestion(payload.subject, payload.sender, payload.body)
    if not text:
        return MailSuggestResponse(suggestion="Couldn't generate a suggestion right now.")
    first = (user.full_name or user.username or user.email or "Me").split()[0]
    today_iso = datetime.now(timezone.utc).strftime("%A, %d %B %Y")
    text = _scrub_placeholders(text, first, today_iso)
    if payload.message_id:
        try:
            from app.services.harvester_worker import store_enrichment
            await store_enrichment(
                user.sub, user.tenant_id, payload.message_id,
                payload.subject, payload.body or payload.subject, text, action,
            )
        except Exception as e:
            logger.debug("store enrichment failed: %s", e)
    return MailSuggestResponse(suggestion=text.strip(), action=action)


@router.get("/suggestions", response_model=Dict[str, Any])
async def copilot_suggestions(
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    """Personalized chat starters, assembled from the BACKGROUND-precomputed
    email enrichments + cached calendar — so the copilot opens with real,
    actionable suggestions instead of static cards. Instant (no LLM call here).
    """
    from app.services.harvester_worker import cached_events, cached_messages

    suggestions: List[Dict[str, str]] = []
    try:
        msgs = await cached_messages(user.sub, user.tenant_id)
    except Exception:
        msgs = []
    try:
        events = await cached_events(user.sub, user.tenant_id)
    except Exception:
        events = []

    _prio_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    # Emails the AI flagged as needing a real action (not archive/ignore).
    actionable = [
        m for m in msgs
        if (m.get("suggestion_action") in ("reply", "schedule", "pay"))
    ]
    actionable.sort(key=lambda m: _prio_rank.get((m.get("priority") or "low").lower(), 3))
    seen_keys: set = set()
    for m in actionable:
        if len([s for s in suggestions if s["icon"] == "mail"]) >= 3:
            break
        sender = m.get("from_name") or "sender"
        subj = m.get("subject") or "(no subject)"
        key = (sender.lower(), subj.lower())
        if key in seen_keys:
            continue
        seen_keys.add(key)
        act = m.get("suggestion_action")
        verb = {"reply": "Reply to", "schedule": "Schedule from", "pay": "Handle"}.get(act, "Handle")
        suggestions.append({
            "icon": "mail",
            "title": f"{verb} {sender}",
            "sub": subj[:60],
            "prompt": (
                f"Help me handle this {act or 'email'} from {sender} "
                f"(subject: '{subj}'). Context: {(m.get('snippet') or '')[:300]}. "
                f"Draft what I should send or do next."
            ),
        })

    # Next meeting today/soon → prep suggestion.
    today = datetime.now(timezone.utc).date().isoformat()
    upcoming = [e for e in events if (e.get("start") or "")[:10] >= today]
    if upcoming:
        ev = upcoming[0]
        suggestions.append({
            "icon": "event",
            "title": f"Prep for {ev.get('title','meeting')}"[:40],
            "sub": (ev.get("start") or "")[:16].replace("T", " "),
            "prompt": f"Help me prepare for my meeting '{ev.get('title','')}' "
                      f"at {ev.get('start','')}. What should I review and aim for?",
        })

    # A cleanup suggestion if there's a lot of promo/archivable mail.
    archivable = [m for m in msgs if m.get("suggestion_action") in ("archive", "ignore", "unsubscribe")]
    if len(archivable) >= 4:
        suggestions.append({
            "icon": "cleaning_services",
            "title": "Clean up promotional mail",
            "sub": f"{len(archivable)} low-priority emails",
            "prompt": "List my promotional / low-priority emails and tell me which to archive.",
        })

    # Generic fallbacks — only fill the slots the important (email/meeting/cleanup)
    # cards didn't. Research leads the fallbacks so it surfaces whenever nothing
    # more pressing is queued ("not always, but when there's room"). Its prompt
    # starts with "Research " so clicking it opens the deep-research panel.
    generics = [
        # Trailing space → the UI prefills the input and asks the user what to
        # research instead of firing a canned query.
        {"icon": "travel_explore", "title": "Research a topic", "sub": "Deep web research, with sources",
         "prompt": "Research "},
        {"icon": "summarize", "title": "Summarize my inbox", "sub": "What needs a reply",
         "prompt": "Summarize my unread emails and tell me which ones need a reply."},
        {"icon": "today", "title": "Plan my day", "sub": "Prioritize what matters",
         "prompt": "Look at my emails and calendar and help me plan my day."},
    ]
    for g in generics:
        if len(suggestions) >= 6:
            break
        suggestions.append(g)

    return {"suggestions": suggestions[:6]}


@router.get("/health")
async def copilot_health() -> Dict[str, Any]:
    client = get_llm_client()
    ok = await client.health_check()
    return {
        "provider": client.provider,
        "status": "up" if ok else "down",
        "model": client.model,
    }


# ---------------------------------------------------------------------------
# Deep Research — background task with live progress (SSE + polling fallback)
# ---------------------------------------------------------------------------

from app.config import ROOT_DIR  # noqa: E402

_RESEARCH_DIR = Path(ROOT_DIR) / "data" / "research"


@dataclass
class _ResearchSession:
    session_id: str
    user_id: str
    tenant_id: str
    query: str
    queue: "asyncio.Queue" = field(default_factory=asyncio.Queue)
    events: List[Dict[str, str]] = field(default_factory=list)
    status: str = "running"  # running | done | error
    result: Optional[Dict[str, Any]] = None
    task_id: Optional[int] = None  # backing InboxTask so it shows on the Dashboard
    thread_id: Optional[int] = None  # chat thread to save the report into


async def _append_research_report_to_thread(session: _ResearchSession, report: str) -> None:
    """Save the finished report as an assistant message in its chat thread, so the
    research is a normal saved chat (in the rail, reopenable) even if the user
    navigated away mid-run. Best-effort."""
    if not session.thread_id or not report:
        return
    try:
        from sqlalchemy import select as _sel
        from app.storage.models import ChatThread, ChatMessage
        from app.tenants.router import get_tenant_router
        router_db = get_tenant_router()
        async with router_db.session_for(session.tenant_id) as s:
            t = (await s.execute(
                _sel(ChatThread).where(ChatThread.id == session.thread_id)
                .where(ChatThread.creator_id == session.user_id)
            )).scalars().first()
            if not t:
                return
            # Hide the "## Sources" list in the chat view (inline citations stay);
            # the full report incl. sources is still available via the Download
            # button / /research/result.
            display = re.split(r"\n#{1,6}\s*sources\b", report, maxsplit=1, flags=re.I)[0].rstrip()
            s.add(ChatMessage(
                tenant_id=session.tenant_id, creator_id=session.user_id,
                thread_id=session.thread_id, role="assistant", content=display,
            ))
            await s.commit()
    except Exception as exc:  # noqa: BLE001
        logger.debug("research thread persist skipped: %s", exc)


async def _create_research_task(session: _ResearchSession) -> Optional[int]:
    """Record the run as an InboxTask so it appears on the Dashboard's active list.
    source='research' keeps it out of the email-only inbox. Best-effort."""
    try:
        from app.storage.models import InboxTask
        from app.tenants.router import get_tenant_router
        router_db = get_tenant_router()
        async with router_db.session_for(session.tenant_id) as s:
            t = InboxTask(
                tenant_id=session.tenant_id, creator_id=session.user_id,
                title=f"Research: {session.query[:120]}",
                summary="Deep web research running in the background…",
                source="research", priority="normal", status="in_progress",
                assignee_id="myai", started_at=datetime.now(timezone.utc),
                payload={"session_id": session.session_id},
            )
            s.add(t)
            await s.commit()
            return t.id
    except Exception as exc:  # noqa: BLE001
        logger.debug("research task create skipped: %s", exc)
        return None


async def _complete_research_task(session: _ResearchSession, result: Any) -> None:
    """Flip the backing InboxTask to done when the run finishes. Best-effort."""
    if not session.task_id:
        return
    try:
        from sqlalchemy import select as _sel
        from app.storage.models import InboxTask
        from app.tenants.router import get_tenant_router
        router_db = get_tenant_router()
        async with router_db.session_for(session.tenant_id) as s:
            t = (await s.execute(_sel(InboxTask).where(InboxTask.id == session.task_id))).scalars().first()
            if t:
                ok = bool(getattr(result, "report", "")) and not getattr(result, "error", None)
                # Research is terminal — it's done either way. Never 'blocked'
                # (that renders as a phantom "WAITING ON YOU" with no resume path).
                t.status = "done"
                t.completed_at = datetime.now(timezone.utc)
                n = len(getattr(result, "sources", []) or [])
                t.summary = (f"Done — {n} sources, {getattr(result, 'rounds_done', 0)} round(s)."
                             if ok else f"No usable result: {getattr(result, 'error', 'unknown')}")
                await s.commit()
    except Exception as exc:  # noqa: BLE001
        logger.debug("research task complete skipped: %s", exc)


# In-memory session registry (mirrors the agent loop's bounded, in-process style).
_research_sessions: Dict[str, _ResearchSession] = {}
_MAX_RESEARCH_SESSIONS = 40


def _prune_research_sessions() -> None:
    """Drop the oldest FINISHED sessions so the registry stays bounded. Finished
    results remain available on disk via _load_research, so eviction is lossless."""
    if len(_research_sessions) < _MAX_RESEARCH_SESSIONS:
        return
    finished = [sid for sid, s in _research_sessions.items() if s.status != "running"]
    # Oldest-first (dict preserves insertion order); keep running ones.
    for sid in finished[: max(0, len(_research_sessions) - _MAX_RESEARCH_SESSIONS + 1)]:
        _research_sessions.pop(sid, None)


def _research_path(session_id: str) -> Path:
    return _RESEARCH_DIR / f"{session_id}.json"


def _persist_research(session_id: str, payload: Dict[str, Any]) -> None:
    try:
        _RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
        _research_path(session_id).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception as exc:  # noqa: BLE001 — persistence is best-effort
        logger.warning("could not persist research %s: %s", session_id, exc)


def _load_research(session_id: str) -> Optional[Dict[str, Any]]:
    p = _research_path(session_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


async def _run_research_session(session: _ResearchSession) -> None:
    """Drive run_deep_research, pushing progress events into the session queue."""
    from app.services.deep_research import run_deep_research

    loop = asyncio.get_running_loop()

    def on_progress(stage: str, detail: str) -> None:
        ev = {"stage": stage, "detail": detail}
        session.events.append(ev)
        # Called from the same loop (deep_research awaits in-line), so this is safe.
        try:
            session.queue.put_nowait(ev)
        except Exception:  # noqa: BLE001
            pass

    try:
        result = await run_deep_research(session.query, on_progress=on_progress, max_rounds=3)
        payload = result.to_dict()
        payload["session_id"] = session.session_id
        payload["created_at"] = datetime.now(timezone.utc).isoformat()
        session.result = payload
        session.status = "error" if result.error and not result.report else "done"
        _persist_research(session.session_id, payload)
        await _append_research_report_to_thread(session, result.report)
        await _complete_research_task(session, result)
        await get_audit_service().log(
            tenant_id=session.tenant_id, user_id=session.user_id,
            event_type="copilot.research",
            message=session.query[:200],
            payload={"sources": len(result.sources), "rounds": result.rounds_done,
                     "partial": result.partial, "error": result.error},
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("research session failed")
        session.status = "error"
        session.result = {"query": session.query, "error": str(exc), "report": "", "sources": []}
        await _complete_research_task(session, None)
    finally:
        session.queue.put_nowait({"stage": session.status, "detail": "complete", "_final": True})


async def cleanup_orphaned_research_tasks() -> int:
    """On startup, a research InboxTask still 'in_progress' is orphaned (its
    in-memory session died with the previous process) — flip it to done so it
    stops showing as RUNNING forever. Only 'in_progress' is touched: research
    tasks never sit in 'open'/'blocked', so leaving those alone avoids clobbering
    unrelated states (and a task running in another worker is left as-is).

    NOTE: with multiple workers this still can't distinguish a live run in
    another process from a truly-orphaned one; it's safe for the single-worker
    deployment this app uses.
    """
    from sqlalchemy import update
    from app.storage.models import InboxTask
    from app.tenants.registry import get_tenant_registry
    from app.tenants.router import get_tenant_router
    router_db = get_tenant_router()
    n = 0
    for t in get_tenant_registry().all():
        try:
            async with router_db.session_for(t.tenant_id) as s:
                res = await s.execute(
                    update(InboxTask)
                    .where(InboxTask.source == "research")
                    .where(InboxTask.status == "in_progress")
                    .values(status="done", summary="Research interrupted (server restart).")
                )
                await s.commit()
                n += res.rowcount or 0
        except Exception as exc:  # noqa: BLE001
            logger.debug("research cleanup failed for %s: %s", t.tenant_id, exc)
    return n


def _require_session(session_id: str, user: PlatformTokenClaims) -> _ResearchSession:
    sess = _research_sessions.get(session_id)
    if not sess or sess.user_id != user.sub or sess.tenant_id != user.tenant_id:
        raise HTTPException(status_code=404, detail="research session not found")
    return sess


@router.post("/research/start", response_model=Dict[str, Any])
async def research_start(
    payload: ChatRequest,
    thread_id: Optional[int] = None,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    """Kick off a deep-research run as a background task. Returns a session_id to
    stream/poll. Optional thread_id saves the finished report into that chat
    thread. Read-only (web only) → no autonomy gate, but audited."""
    query = (payload.message or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="message (research query) is required")
    session_id = uuid.uuid4().hex
    session = _ResearchSession(
        session_id=session_id, user_id=user.sub, tenant_id=user.tenant_id, query=query,
        thread_id=thread_id,
    )
    _prune_research_sessions()  # bound in-memory growth; finished results live on disk
    _research_sessions[session_id] = session
    session.task_id = await _create_research_task(session)  # show on Dashboard
    _spawn_bg(_run_research_session(session))  # retain ref so it isn't GC'd
    await get_audit_service().log(
        tenant_id=user.tenant_id, user_id=user.sub,
        event_type="copilot.research.start", message=query[:200],
        payload={"session_id": session_id},
    )
    return {"session_id": session_id, "status": "running", "query": query}


@router.get("/research/stream/{session_id}")
async def research_stream(
    session_id: str,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> StreamingResponse:
    """Server-Sent Events stream of progress events until the run completes."""
    session = _research_sessions.get(session_id)
    if not session or session.user_id != user.sub or session.tenant_id != user.tenant_id:
        # Not in memory (evicted / other worker). If a result was persisted the
        # run is already done — emit a single final frame so the client renders.
        done = _load_research(session_id) is not None

        async def gone():
            stage = "done" if done else "error"
            yield f"data: {json.dumps({'stage': stage, 'detail': 'complete', '_final': True})}\n\n"
        return StreamingResponse(gone(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    async def gen():
        # Poll the APPEND-ONLY events list (not a destructive queue), so multiple
        # concurrent/reconnecting consumers each see every event and none can
        # "steal" the completion signal. Completion is driven by session.status.
        emitted = 0
        idle = 0
        while True:
            evs = session.events
            while emitted < len(evs):
                yield f"data: {json.dumps(evs[emitted])}\n\n"
                emitted += 1
                idle = 0
            if session.status != "running":
                yield f"data: {json.dumps({'stage': session.status, 'detail': 'complete', '_final': True})}\n\n"
                return
            await asyncio.sleep(0.5)
            idle += 1
            if idle >= 60:  # ~30s with no new events → keep-alive comment frame
                yield ": keep-alive\n\n"
                idle = 0

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/research/status/{session_id}", response_model=Dict[str, Any])
async def research_status(
    session_id: str,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    """Polling fallback: current status + all progress events so far."""
    session = _research_sessions.get(session_id)
    if session and session.user_id == user.sub and session.tenant_id == user.tenant_id:
        return {
            "session_id": session_id,
            "status": session.status,
            "events": session.events,
            "done": session.status != "running",
        }
    # Not in this process's memory (evicted, or a different worker) — if a result
    # was persisted, the run is done; otherwise it's genuinely unknown.
    if _load_research(session_id) is not None:
        return {"session_id": session_id, "status": "done", "events": [], "done": True}
    raise HTTPException(status_code=404, detail="research session not found")


@router.get("/research/result/{session_id}", response_model=Dict[str, Any])
async def research_result(
    session_id: str,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    """Final report markdown + sources + citations. Falls back to the persisted
    file so results survive a page refresh / server tick."""
    from app.services.grounding import build_citations

    session = _research_sessions.get(session_id)
    payload: Optional[Dict[str, Any]] = None
    if session and session.user_id == user.sub and session.tenant_id == user.tenant_id:
        if session.status == "running":
            return {"session_id": session_id, "status": "running", "ready": False}
        payload = session.result
    if payload is None:
        payload = _load_research(session_id)  # survives restart / refresh
    if payload is None:
        raise HTTPException(status_code=404, detail="research result not found")

    sources = payload.get("sources") or []
    citations = build_citations({"WEB_SEARCH_RESULTS": "web"}) if sources else []
    return {
        "session_id": session_id,
        "status": payload.get("error") and not payload.get("report") and "error" or "done",
        "ready": True,
        "query": payload.get("query"),
        "report": payload.get("report", ""),
        "sources": sources,
        "citations": citations,
        "sub_questions": payload.get("sub_questions", []),
        "partial": payload.get("partial", False),
        "rounds_done": payload.get("rounds_done", 0),
        "elapsed_s": payload.get("elapsed_s", 0),
        "error": payload.get("error"),
    }


# ---------------------------------------------------------------------------
# Scheduled (recurring) research watches
# ---------------------------------------------------------------------------


@router.get("/research/schedules", response_model=Dict[str, Any])
async def research_schedules(
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    from app.services.research_scheduler import list_schedules
    return {"schedules": await list_schedules(user.sub, user.tenant_id)}


@router.post("/research/schedule", response_model=Dict[str, Any])
async def research_schedule_create(
    payload: ChatRequest,
    interval_hours: int = 24,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    """Create a recurring research watch (message = the topic)."""
    from app.services.research_scheduler import create_schedule
    topic = (payload.message or "").strip()
    if not topic:
        raise HTTPException(status_code=400, detail="topic (message) is required")
    sid = await create_schedule(user.sub, user.tenant_id, topic, interval_hours)
    await get_audit_service().log(
        tenant_id=user.tenant_id, user_id=user.sub,
        event_type="copilot.research.schedule", message=topic[:200],
        payload={"schedule_id": sid, "interval_hours": interval_hours},
    )
    return {"id": sid, "topic": topic, "interval_hours": interval_hours}


@router.delete("/research/schedule/{schedule_id}", response_model=Dict[str, Any])
async def research_schedule_cancel(
    schedule_id: int,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    from app.services.research_scheduler import cancel_schedule
    ok = await cancel_schedule(user.sub, user.tenant_id, schedule_id)
    if not ok:
        raise HTTPException(status_code=404, detail="watch not found")
    return {"status": "cancelled", "id": schedule_id}


@router.post("/research/schedule/{schedule_id}/run", response_model=Dict[str, Any])
async def research_schedule_run_now(
    schedule_id: int,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    """Trigger a watch immediately (fire-and-forget)."""
    from app.services.research_scheduler import _run_one
    _spawn_bg(_run_one(schedule_id, user.tenant_id))
    return {"status": "running", "id": schedule_id}
