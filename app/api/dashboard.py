"""Dashboard endpoints - personal-assistant KPIs grounded in real Gmail/Calendar.

When the user has connected Gmail / Google Calendar, the KPIs are filled with
real counts. Anything that requires a connector that's not connected falls
back to a soft em-dash so the UI still renders.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select, func

from app.agent.connector_tools import (
    calendar_events_structured,
    gmail_counts,
    gmail_messages_structured,
)
from app.api.inbox import _classify_email_priority
from app.auth.middleware import get_current_user
from app.auth.jwt import PlatformTokenClaims
from app.services.harvester_gateway import get_harvester_gateway
from app.storage.models import AuditLog, InboxTask
from app.tenants.router import get_tenant_router

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def _task_assistant_says(t) -> str:
    """One-liner about what MyAi is doing for this task."""
    if t.status == "blocked":
        return (
            f"Paused — waiting for your approval before continuing. "
            f"Tap 'Resume' or open the task to see the proposed next step."
        )
    if t.status == "in_progress":
        return (
            f"Working on this in the background. "
            f"Started {(t.created_at.strftime('%H:%M') if t.created_at else 'recently')}. "
            "I'll surface anything that needs you."
        )
    if t.status == "open":
        return "Queued. I'll start as soon as you give the go-ahead."
    return t.summary or "No details available."


def _assistant_says(m: dict, prio_label: str) -> str:
    """Generate a useful one-liner about this email."""
    snip = (m.get("snippet") or "").strip()
    subj = m.get("subject") or "(no subject)"
    name = m.get("from_name") or "Sender"
    text_l = (subj + " " + snip).lower()

    if "?" in text_l:
        return f"{name} asked you a question in '{subj}'. Reply or ask MyAi to draft one."
    if any(k in text_l for k in ["please", "kindly"]) and any(
        k in text_l for k in ["review", "approve", "confirm", "send", "share"]
    ):
        return f"{name} needs you to take an action. Quick reply recommended."
    if any(k in text_l for k in ["urgent", "asap", "deadline", "by eod", "by tomorrow"]):
        return f"Marked urgent by {name}. Skim and decide if it's actually time-sensitive."
    if any(k in text_l for k in ["meeting", "call", "schedule", "invite"]):
        return f"Meeting or scheduling note from {name}. Accept, decline or propose a time."
    return f"Message from {name}. {snip[:120]}{'…' if len(snip) > 120 else ''}"


def _today_bounds() -> tuple[datetime, datetime]:
    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start.replace(hour=23, minute=59, second=59, microsecond=999999)
    return start, end


@router.get("", response_model=Dict[str, Any])
async def dashboard_summary(
    request: Request,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    """Aggregate dashboard endpoint.

    Returns KPIs + Today's Focus + Active threads, all grounded in the
    user's real data where connected.
    """
    user_id, tenant_id = user.sub, user.tenant_id

    # Fan-out the slow calls in parallel
    gmail_counts_task = asyncio.create_task(gmail_counts(user_id, tenant_id))
    gmail_msgs_task = asyncio.create_task(
        gmail_messages_structured(
            user_id, "is:unread in:inbox", limit=10, tenant_id=tenant_id
        )
    )
    calendar_task = asyncio.create_task(
        calendar_events_structured(user_id, days_ahead=2, tenant_id=tenant_id)
    )

    # Local DB stats (cheap)
    router_db = get_tenant_router()
    async with router_db.session_for(tenant_id) as session:
        open_tasks_q = await session.execute(
            select(func.count(InboxTask.id))
            .where(InboxTask.tenant_id == tenant_id)
            .where(InboxTask.creator_id == user_id)
            .where(InboxTask.status.in_(["open", "in_progress", "blocked"]))
        )
        open_tasks_count = int(open_tasks_q.scalar() or 0)

        due_week_q = await session.execute(
            select(func.count(InboxTask.id))
            .where(InboxTask.tenant_id == tenant_id)
            .where(InboxTask.creator_id == user_id)
            .where(InboxTask.priority.in_(["high", "critical"]))
            .where(InboxTask.status != "done")
        )
        due_week_count = int(due_week_q.scalar() or 0)

        today_start, today_end = _today_bounds()
        actions_q = await session.execute(
            select(func.count(AuditLog.id))
            .where(AuditLog.tenant_id == tenant_id)
            .where(AuditLog.creator_id == user_id)
            .where(AuditLog.created_at >= today_start)
            .where(AuditLog.created_at <= today_end)
        )
        actions_today = int(actions_q.scalar() or 0)

    gmail_c = await gmail_counts_task
    gmail_msgs = await gmail_msgs_task
    cal_events = await calendar_task

    # Classify every unread email so we can filter promos / system out of
    # both the UNREAD KPI and the Active threads card.
    classified: list[tuple[str, dict]] = []
    for m in gmail_msgs:
        prio = _classify_email_priority(
            m["subject"], m["snippet"], m.get("from", ""), m.get("from_name", ""),
            label_ids=m.get("label_ids") or [],
        )
        classified.append((prio, m))
    actionable = [m for prio, m in classified if prio in ("high", "medium")]
    high_only = [m for prio, m in classified if prio == "high"]

    today = datetime.now(timezone.utc).date()
    meetings_today = sum(1 for e in cal_events if e["start"].startswith(str(today)))

    # Estimate "focus hours left" = 8h workday - meetings hours today already passed
    focus_hours_left = max(
        0, 8 - sum(1 for e in cal_events if e["start"].startswith(str(today)))
    )

    # NEEDS REPLY = unread that classifier flagged as high priority
    needs_reply = len(high_only)
    # UNREAD that matters — actionable count rather than lifetime unread
    unread_attention = len(actionable)

    em = "—"

    def _v(n: int | None, available: bool, tone: str | None = None) -> dict:
        if not available:
            return {"value": em}
        d = {"value": str(n)}
        if tone:
            d["tone"] = tone
        return d

    kpis = [
        {"label": "NEEDS ATTENTION", **_v(unread_attention, gmail_c["available"])},
        {
            "label": "NEEDS REPLY",
            **_v(needs_reply, gmail_c["available"], tone="warn" if needs_reply else None),
        },
        {"label": "MEETINGS TODAY", **_v(meetings_today, bool(cal_events) or True)},
        {
            "label": "FOCUS HOURS LEFT",
            "value": f"{focus_hours_left}h" if cal_events or gmail_c["available"] else em,
        },
        {
            "label": "OPEN TASKS",
            "value": str(open_tasks_count),
        },
        {
            "label": "DUE THIS WEEK",
            "value": str(due_week_count),
            **({"tone": "warn"} if due_week_count > 0 else {}),
        },
        {
            "label": "DRAFTS WAITING",
            **_v(
                gmail_c.get("drafts", 0),
                gmail_c["available"],
                tone="warn" if gmail_c.get("drafts", 0) else None,
            ),
        },
        {"label": "ASSISTANT ACTIONS", "value": str(actions_today)},
    ]

    # Today's Focus substats — real numbers, em-dash where N/A
    focus = {
        "active": open_tasks_count,
        "wonWeek": 0,  # Will be wired to recent done count when needed
        "lostWeek": 0,
        "saveRate": em,
        "avgDiscount": em,
        "avgLevels": str(actions_today) if actions_today else em,
        "competitors": em,
        "escalations": needs_reply,
    }

    # Active tasks — what MyAi is currently running or has queued for you.
    # Pulls real InboxTask rows in any "live" state (in_progress, blocked,
    # open with assignee=agent, etc.). Email triage is a separate concern.
    async with router_db.session_for(tenant_id) as session:
        live_tasks_q = await session.execute(
            select(InboxTask)
            .where(InboxTask.tenant_id == tenant_id)
            .where(InboxTask.creator_id == user_id)
            .where(InboxTask.status.in_(["in_progress", "blocked", "open"]))
            .order_by(InboxTask.updated_at.desc())
            .limit(5)
        )
        live_tasks = live_tasks_q.scalars().all()

    from app.api.inbox import _build_lifecycle  # avoid circular at module level

    threads: List[Dict[str, Any]] = []
    for t in live_tasks:
        status_label = {
            "in_progress": "RUNNING",
            "blocked": "WAITING ON YOU",
            "open": "QUEUED",
        }.get(t.status, t.status.upper())
        progress = (
            70 if t.status == "in_progress" else
            45 if t.status == "blocked" else
            10
        )
        lifecycle = _build_lifecycle(t)
        threads.append(
            {
                "id": f"TASK-{t.id}",
                "task_id": t.id,
                "name": t.title or f"Task #{t.id}",
                "level": 3 if t.priority in ("high", "critical") else 2,
                "competitor": (t.priority or "normal").title(),
                "status": status_label,
                "product": (t.summary or "(no description)")[:160],
                "fee": (t.created_at.isoformat()[:16] if t.created_at else ""),
                "tenure": t.source or "agent",
                "progress": progress,
                "confidence": 90,
                "incentives": (
                    ["Resume", "Cancel"] if t.status == "blocked" else
                    ["Pause", "Cancel"] if t.status == "in_progress" else
                    ["Run now", "Cancel"]
                ),
                "thinking": _task_assistant_says(t),
                "recommended_action": (
                    "Approve and continue" if t.status == "blocked" else
                    "Let it run" if t.status == "in_progress" else
                    "Kick it off"
                ),
                "conversation": [],
                "lifecycle": lifecycle,
            }
        )

    if not threads:
        threads.append(
            {
                "id": "EMPTY-1",
                "name": "Nothing running",
                "level": 0,
                "competitor": "",
                "status": "IDLE",
                "product": "MyAi has no active or scheduled tasks. Ask MyAi to take on a goal and it'll appear here.",
                "fee": "—",
                "tenure": "—",
                "progress": 0,
                "confidence": 100,
                "incentives": [],
                "thinking": "Try: \"Plan my week\" or \"Watch my inbox for emails from Priti and draft replies for me to review\".",
                "recommended_action": "Start a task",
                "conversation": [],
            }
        )

    return {
        "scope": {"tenant_id": tenant_id, "user_id": user_id},
        "kpis": kpis,
        "retention": focus,
        "negotiations": threads,
        "connected": {
            "gmail": gmail_c["available"],
            "calendar": bool(cal_events) or False,
        },
    }


@router.get("/kpis", response_model=Dict[str, Any])
async def dashboard_kpis(
    request: Request,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    gateway = get_harvester_gateway()
    return await gateway.get_user_data(
        user_id=user.sub,
        tenant_id=user.tenant_id,
        query_type="dashboard_kpis",
    )


@router.get("/agent-activity", response_model=Dict[str, Any])
async def dashboard_agent_activity(
    request: Request,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    gateway = get_harvester_gateway()
    return await gateway.get_user_data(
        user_id=user.sub,
        tenant_id=user.tenant_id,
        query_type="agent_activity",
    )
