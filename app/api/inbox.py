"""Agent inbox + tasks endpoints."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response
from sqlalchemy import select

from app.agent.connector_tools import (
    calendar_create_event,
    calendar_events_structured,
    gmail_archive,
    gmail_get_attachment,
    gmail_get_full,
    gmail_mark_read,
    gmail_messages_structured,
    gmail_modify_labels,
    gmail_send,
    gmail_thread_get,
    gmail_trash,
)
from app.api.preferences import autonomy_allows, decide_write_gate
from app.storage.models import UserPreference
from sqlalchemy import select as _select  # alias to avoid clash with existing select
from app.agent.outlook_tools import (
    outlook_archive,
    outlook_calendar_events,
    outlook_delete,
    outlook_get_full,
    outlook_mark_read,
    outlook_messages_structured,
    outlook_send,
)
from app.auth.jwt import PlatformTokenClaims
from app.auth.middleware import get_current_user
from app.models.schemas import (
    CreateEventRequest,
    InboxTaskCreate,
    InboxTaskOut,
    InboxTaskUpdate,
    SendEmailRequest,
    SnoozeRequest,
    WriteActionResponse,
)
from app.services.audit import get_audit_service
from app.services.harvester_gateway import get_harvester_gateway
from app.storage.models import InboxTask, ScheduledEmail
from app.tenants.router import get_tenant_router

router = APIRouter(prefix="/api/inbox", tags=["inbox"])
logger = logging.getLogger(__name__)


def _within_days(iso: str, days: int) -> bool:
    try:
        when = datetime.fromisoformat((iso or "").replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return (when - datetime.now(timezone.utc)).total_seconds() <= days * 86400
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Email priority classifier
# ---------------------------------------------------------------------------


_PROMO_DOMAINS = (
    "offers.", "noreply", "no-reply", "newsletter", "marketing",
    "mailer", "campaign", "deals", "promo", "info@", "notifications@",
    "alerts@", "updates@",
)
_PROMO_SENDER_NAMES = (
    "linkedin", "pepperfry", "myntra", "amazon", "bewakoof", "adidas", "nike",
    "flipkart", "ajio", "swiggy", "zomato", "uber", "ola", "indigo", "vistara",
    "spicejet", "make my trip", "makemytrip", "easemytrip", "hdfc", "icici",
    "sbi", "axis", "yes bank", "kotak", "paypal", "razorpay", "groupon",
    "stylus", "h&m", "zara", "bath & body", "ikea", "google play", "play store",
    "apple", "youtube", "netflix", "spotify", "prime video", "hotstar", "jio",
    "airtel", "vi", "bsnl",
)
_PROMO_SUBJECT_WORDS = (
    "sale", "off", "% off", "deal", "deals", "save", "saving", "savings",
    "discount", "free", "exclusive", "limited time", "last day", "ending",
    "expires", "expiring", "flash sale", "weekend", "weekly", "newsletter",
    "digest", "summary of", "promo", "promotion", "coupon", "voucher",
    "membership", "renew", "ends today", "ends in", "buy now",
)
_SYSTEM_SUBJECT_WORDS = (
    "verification", "verify your", "otp", "security alert", "sign-in",
    "new sign", "sign in", "your statement", "monthly statement",
    "payment received", "transaction", "receipt", "invoice",
    "delivery update", "shipped", "shipping update", "out for delivery",
    "tracking", "your order",
)
_ACTION_WORDS = (
    "?", "let me know", "can you", "could you", "please", "urgent", "asap",
    "by tomorrow", "by eod", "review and", "approve", "sign off", "deadline",
    "blocker", "follow up", "follow-up", "waiting on you", "any update",
    "thoughts?", "feedback", "kindly", "needs your", "action required",
)


def _classify_email_priority(
    subject: str,
    snippet: str,
    from_full: str,
    from_name: str,
    label_ids: list[str] | None = None,
) -> str:
    """Return one of low | medium | high based on the email content.

    Promotional / system mail is forced to low even if it contains "please"
    (most marketing copy does).
    """
    subj_l = (subject or "").lower()
    snip_l = (snippet or "").lower()
    sender_l = (from_full or "").lower()
    name_l = (from_name or "").lower()
    labels = label_ids or []

    # Heuristic: Gmail's "CATEGORY_PROMOTIONS" / "CATEGORY_UPDATES" are explicit signals.
    if "CATEGORY_PROMOTIONS" in labels:
        return "low"
    if "CATEGORY_UPDATES" in labels or "CATEGORY_FORUMS" in labels:
        return "low"
    if "IMPORTANT" in labels:
        return "high"

    is_promo_sender = (
        any(d in sender_l for d in _PROMO_DOMAINS)
        or any(p in name_l for p in _PROMO_SENDER_NAMES)
    )
    is_promo_subject = any(w in subj_l for w in _PROMO_SUBJECT_WORDS)
    is_system = any(w in subj_l for w in _SYSTEM_SUBJECT_WORDS)

    if is_promo_sender or is_promo_subject:
        return "low"
    if is_system:
        return "low"

    # From here on it's likely a real human email
    has_action_signal = any(w in subj_l or w in snip_l for w in _ACTION_WORDS)
    if has_action_signal:
        return "high"

    # Real person, no obvious ask — medium
    return "medium"


@router.post("/refresh", response_model=Dict[str, Any])
async def inbox_force_refresh(
    request: Request,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    """Force the harvester to recrawl this user's connectors right now."""
    from app.services.harvester_worker import force_refresh_for_user

    result = await force_refresh_for_user(user.sub, user.tenant_id)
    return {"status": "ok", "refresh": result}


@router.get("", response_model=Dict[str, Any])
async def inbox_summary(
    request: Request,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    """Aggregate endpoint — DB tasks + real Gmail + Calendar items.

    Each item is shaped uniformly so the SPA can render them all in the same
    list. Gmail and calendar items use string ids (eg ``gmail:<id>``) so they
    never collide with the integer-id DB tasks.
    """
    gateway = get_harvester_gateway()
    db_tasks = await gateway.get_user_data(
        user_id=user.sub,
        tenant_id=user.tenant_id,
        query_type="inbox_tasks",
        limit=50,
    )
    activity = await gateway.get_user_data(
        user_id=user.sub, tenant_id=user.tenant_id, query_type="agent_activity"
    )

    tasks: list[dict] = list(db_tasks.get("data") or [])

    # Cache-first reads (harvester_worker keeps these warm).
    from app.services.harvester_worker import (
        cache_is_fresh,
        cached_messages,
        force_refresh_for_user,
    )

    gmail_msgs: list[dict] = []
    outlook_msgs: list[dict] = []

    from app.services.demo_seed import demo_mode_enabled, is_demo_user
    # Demo accounts read the seeded synthetic dataset; everyone else (real SSO /
    # personal account) reads ONLY their own connected data.
    demo = demo_mode_enabled() or is_demo_user(user.sub)

    gmail_fresh = await cache_is_fresh(user.sub, user.tenant_id, "gmail", "messages")
    if gmail_fresh or demo:
        # In demo mode always read the seeded cache — never crawl personal Gmail.
        gmail_msgs = await cached_messages(user.sub, user.tenant_id, "gmail")
        if demo and not gmail_msgs:
            from app.services.harvester_worker import force_refresh_for_user
            await force_refresh_for_user(user.sub, user.tenant_id)
            gmail_msgs = await cached_messages(user.sub, user.tenant_id, "gmail")
    else:
        # First load / stale cache — pull live and let the worker catch up next tick.
        gmail_msgs = await gmail_messages_structured(
            user.sub, "in:inbox", limit=60, tenant_id=user.tenant_id
        )

    outlook_fresh = await cache_is_fresh(user.sub, user.tenant_id, "outlook", "messages")
    if outlook_fresh or demo:
        outlook_msgs = await cached_messages(user.sub, user.tenant_id, "outlook")
    else:
        outlook_msgs = await outlook_messages_structured(
            user.sub, query="", limit=15, unread_only=True, tenant_id=user.tenant_id
        )
    for m in outlook_msgs:
        prio = _classify_email_priority(
            m["subject"], m["snippet"], m["from"], m.get("from_name", "")
        )
        tasks.append({
            "id": f"outlook:{m['id']}",
            "title": m["subject"],
            "summary": f"{m['from_name']} — {m['snippet']}",
            "source": "email",
            "account": "outlook",
            "priority": prio,
            "status": "open",
            "created_at": m["date"],
            "updated_at": m["date"],
            "external_id": m["id"],
            "external_kind": "outlook",
            "thread_id": m.get("thread_id"),
            "from_name": m["from_name"],
            "suggestion": m.get("suggestion"),
            "suggestion_action": m.get("suggestion_action"),
        })
    for m in gmail_msgs:
        # Prefer the priority already assigned at harvest/seed time; only classify
        # live-fetched messages that don't carry one yet.
        prio = m.get("priority") or _classify_email_priority(
            m["subject"], m["snippet"], m.get("from", ""), m.get("from_name", ""),
            label_ids=m.get("label_ids") or [],
        )
        tasks.append(
            {
                "id": f"gmail:{m['id']}",
                "title": m["subject"],
                "summary": f"{m['from_name']} — {m['snippet']}",
                "source": "email",
                "account": "gmail",
                "priority": prio,
                "status": "open",
                "created_at": m["date"],
                "updated_at": m["date"],
                "external_id": m["id"],
                "external_kind": "gmail",
                "thread_id": m.get("thread_id"),
                "from_name": m["from_name"],
                "suggestion": m.get("suggestion"),
                "suggestion_action": m.get("suggestion_action"),
            }
        )

    # Calendar items needing a response (today + tomorrow)
    cal_fresh = await cache_is_fresh(user.sub, user.tenant_id, "google_calendar", "events")
    if cal_fresh:
        from app.services.harvester_worker import cached_events
        cal_events = [
            e for e in await cached_events(user.sub, user.tenant_id, "google_calendar")
            if e["start"][:10] <= (datetime.now(timezone.utc).date().isoformat())[:10]
            or _within_days(e["start"], 2)
        ]
    else:
        cal_events = await calendar_events_structured(
            user.sub, days_ahead=2, tenant_id=user.tenant_id
        )
    today_iso = datetime.now(timezone.utc).date().isoformat()
    for ev in cal_events[:5]:
        if not ev["start"].startswith(today_iso):
            continue
        tasks.append(
            {
                "id": f"cal:{ev['id']}",
                "title": f"Meeting: {ev['title']}",
                "summary": f"{ev['start']} · {ev['attendee_count']} attendee(s)"
                + (f" @ {ev['location']}" if ev["location"] else ""),
                "source": "calendar",
                "priority": "medium",
                "status": "open",
                "created_at": ev["start"],
                "updated_at": ev["start"],
                "external_id": ev["id"],
                "external_kind": "calendar",
            }
        )

    # The inbox is email-only — calendar items and background tasks live on the
    # Dashboard. (They're still counted in `sources` below for transparency.)
    tasks = [t for t in tasks if t.get("source") == "email"]

    return {
        "scope": {"tenant_id": user.tenant_id, "user_id": user.sub},
        "tasks": tasks,
        "stats": activity.get("data"),
        "sources": {
            "db": len(db_tasks.get("data") or []),
            "gmail": len(gmail_msgs),
            "outlook": len(outlook_msgs),
            "calendar": sum(
                1 for ev in cal_events if ev["start"].startswith(today_iso)
            ),
        },
    }


@router.get("/tasks", response_model=Dict[str, Any])
async def list_tasks(
    request: Request,
    status_filter: Optional[str] = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    gateway = get_harvester_gateway()
    return await gateway.get_user_data(
        user_id=user.sub,
        tenant_id=user.tenant_id,
        query_type="inbox_tasks",
        status=status_filter,
        limit=limit,
    )


@router.post("/tasks", response_model=InboxTaskOut, status_code=status.HTTP_201_CREATED)
async def create_task(
    payload: InboxTaskCreate,
    request: Request,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> InboxTaskOut:
    from app.services.lifecycle_ticker import DEFAULT_SLA_BY_PRIORITY, default_due_at

    router_db = get_tenant_router()
    async with router_db.session_for(user.tenant_id) as session:
        prio = payload.priority or "normal"
        now = datetime.now(timezone.utc)
        sla = DEFAULT_SLA_BY_PRIORITY.get(prio.lower(), 24 * 60)
        task = InboxTask(
            tenant_id=user.tenant_id,
            creator_id=user.sub,
            title=payload.title,
            summary=payload.summary,
            priority=prio,
            source=payload.source or "manual",
            payload=payload.payload,
            status="open",
            # Default SLA from priority. User can PATCH due_at/sla_minutes later.
            due_at=default_due_at(prio, now),
            sla_minutes=sla,
            assignee_id="me",
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)

    await get_audit_service().log(
        tenant_id=user.tenant_id,
        user_id=user.sub,
        event_type="inbox.task_created",
        message=f"Created task #{task.id}: {task.title}",
        payload={"task_id": task.id, "priority": task.priority},
    )

    return InboxTaskOut.model_validate(_row_to_dict(task))


@router.patch("/tasks/{task_id}", response_model=InboxTaskOut)
async def update_task(
    task_id: int,
    payload: InboxTaskUpdate,
    request: Request,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> InboxTaskOut:
    router_db = get_tenant_router()
    async with router_db.session_for(user.tenant_id) as session:
        result = await session.execute(
            select(InboxTask)
            .where(InboxTask.id == task_id)
            .where(InboxTask.tenant_id == user.tenant_id)
            .where(InboxTask.creator_id == user.sub)
        )
        task = result.scalars().first()
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        if payload.status is not None:
            task.status = payload.status
        if payload.priority is not None:
            task.priority = payload.priority
        if payload.summary is not None:
            task.summary = payload.summary
        if payload.due_at is not None:
            task.due_at = payload.due_at
        if payload.sla_minutes is not None:
            task.sla_minutes = payload.sla_minutes
        if payload.assignee_id is not None:
            task.assignee_id = payload.assignee_id
        await session.commit()
        await session.refresh(task)

    await get_audit_service().log(
        tenant_id=user.tenant_id,
        user_id=user.sub,
        event_type="inbox.task_updated",
        message=f"Updated task #{task.id}",
        payload={"task_id": task.id, "status": task.status},
    )
    return InboxTaskOut.model_validate(_row_to_dict(task))


@router.get("/tasks/{task_id}", response_model=Dict[str, Any])
async def get_task_detail(
    task_id: int,
    request: Request,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    """Full task detail: header + steps + AI reasoning + conversation + recommended action."""
    router_db = get_tenant_router()
    async with router_db.session_for(user.tenant_id) as session:
        result = await session.execute(
            select(InboxTask)
            .where(InboxTask.id == task_id)
            .where(InboxTask.tenant_id == user.tenant_id)
            .where(InboxTask.creator_id == user.sub)
        )
        task = result.scalars().first()
        if not task:
            # Mock detail data for unknown ids (so the UI looks alive in dev mode)
            return _mock_task_detail(task_id)

        # Build a real lifecycle ribbon from row state
        row_dict = _row_to_dict(task)
        lifecycle = _build_lifecycle(task)
        return {
            **row_dict,
            **_mock_detail_fields(),
            "lifecycle": lifecycle,
        }


def _build_lifecycle(task: InboxTask) -> dict:
    """Produce a lifecycle ribbon payload for the task detail UI."""
    now = datetime.now(timezone.utc)
    due = task.due_at
    if due and due.tzinfo is None:
        due = due.replace(tzinfo=timezone.utc)

    breached = bool(due and due < now and not task.completed_at)
    remaining = None
    if due and not task.completed_at:
        delta = (due - now).total_seconds()
        remaining = {
            "seconds": int(delta),
            "human": _humanize_seconds(int(delta)),
        }

    states = ["open", "in_progress", "blocked", "done"]
    cur = task.status if task.status in states else "open"
    idx = states.index(cur)

    return {
        "due_at": due.isoformat() if due else None,
        "sla_minutes": task.sla_minutes,
        "assignee_id": task.assignee_id or "me",
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "escalation_count": int(task.escalation_count or 0),
        "breached": breached,
        "remaining": remaining,
        "stage": cur,
        "stage_index": idx,
        "stages": states,
    }


def _humanize_seconds(s: int) -> str:
    if s <= 0:
        return f"overdue by {_humanize_seconds(-s) if s != 0 else '0 min'}"
    if s < 60:
        return f"{s} sec"
    if s < 3600:
        return f"{s // 60} min"
    if s < 86400:
        h = s // 3600
        m = (s % 3600) // 60
        return f"{h}h {m}m" if m else f"{h}h"
    return f"{s // 86400}d"


async def _do_action(task_id: int, action: str, user: PlatformTokenClaims) -> Dict[str, Any]:
    """Shared handler for Run/Pause/Resume/Cancel/Retry/Approve."""
    valid_states = {
        "run": "in_progress",
        "pause": "paused",       # distinct from 'blocked' (which means waiting-on-you)
        "resume": "in_progress",
        "cancel": "cancelled",
        "retry": "in_progress",
        "approve": "done",
    }
    if action not in valid_states:
        raise HTTPException(status_code=400, detail=f"Unknown action: {action}")

    new_status = valid_states[action]
    router_db = get_tenant_router()
    async with router_db.session_for(user.tenant_id) as session:
        result = await session.execute(
            select(InboxTask)
            .where(InboxTask.id == task_id)
            .where(InboxTask.tenant_id == user.tenant_id)
            .where(InboxTask.creator_id == user.sub)
        )
        task = result.scalars().first()
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        task.status = new_status
        now = datetime.now(timezone.utc)
        # Track lifecycle timestamps
        if action in ("run", "resume", "retry") and task.started_at is None:
            task.started_at = now
        if action == "approve":
            task.completed_at = now
        if action == "cancel":
            task.completed_at = now
        await session.commit()
        await session.refresh(task)
        task_dict = _row_to_dict(task)

    await get_audit_service().log(
        tenant_id=user.tenant_id,
        user_id=user.sub,
        event_type=f"inbox.task_{action}",
        message=f"Task #{task_id} -> {new_status} (via {action})",
        payload={"task_id": task_id, "action": action, "new_status": new_status},
    )
    return {"task_id": task_id, "action": action, "status": new_status, "task": task_dict}


@router.post("/tasks/{task_id}/run", response_model=Dict[str, Any])
async def task_run(task_id: int, user: PlatformTokenClaims = Depends(get_current_user)):
    return await _do_action(task_id, "run", user)


@router.post("/tasks/{task_id}/pause", response_model=Dict[str, Any])
async def task_pause(task_id: int, user: PlatformTokenClaims = Depends(get_current_user)):
    return await _do_action(task_id, "pause", user)


@router.post("/tasks/{task_id}/resume", response_model=Dict[str, Any])
async def task_resume(task_id: int, user: PlatformTokenClaims = Depends(get_current_user)):
    return await _do_action(task_id, "resume", user)


@router.post("/tasks/{task_id}/retry", response_model=Dict[str, Any])
async def task_retry(task_id: int, user: PlatformTokenClaims = Depends(get_current_user)):
    return await _do_action(task_id, "retry", user)


@router.post("/tasks/{task_id}/approve", response_model=Dict[str, Any])
async def task_approve(task_id: int, user: PlatformTokenClaims = Depends(get_current_user)):
    return await _do_action(task_id, "approve", user)


def _mock_detail_fields() -> dict:
    """Default detail fields when a DB task has no AI run yet."""
    return {
        "ai_strategy": "",
        "ai_confidence": 0.0,
        "progress": {"done": 0, "total": 1, "label": "Not started"},
        "steps": [],
        "ai_reasoning": "",
        "incentives_offered": [],
        "conversation": [],
        "recommended_action": None,
    }


def _mock_task_detail(task_id: int) -> dict:
    """For task IDs that don't exist in DB — return a neutral shaped record."""
    return {
        "id": task_id,
        "title": f"Task #{task_id}",
        "summary": "No detail available",
        "status": "open",
        "priority": "medium",
        "source": "manual",
        "created_at": None,
        "updated_at": None,
        **_mock_detail_fields(),
    }


def _gmail_task_detail(external_id: str, user_id: str, tenant_id: str) -> dict:
    """Build a detail view for a Gmail-backed task."""
    return {
        "id": f"gmail:{external_id}",
        "title": "Gmail message",
        "summary": "Open Gmail to read the full thread.",
        "status": "open",
        "priority": "medium",
        "source": "email",
        "created_at": None,
        "updated_at": None,
        "external_kind": "gmail",
        "external_id": external_id,
        **_mock_detail_fields(),
        "recommended_action": {
            "label": "Open in Gmail / draft reply via Copilot",
            "tone": "good",
        },
    }


@router.delete("/tasks/{task_id}", response_class=Response, status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(
    task_id: int,
    request: Request,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Response:
    router_db = get_tenant_router()
    async with router_db.session_for(user.tenant_id) as session:
        result = await session.execute(
            select(InboxTask)
            .where(InboxTask.id == task_id)
            .where(InboxTask.tenant_id == user.tenant_id)
            .where(InboxTask.creator_id == user.sub)
        )
        task = result.scalars().first()
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        await session.delete(task)
        await session.commit()

    await get_audit_service().log(
        tenant_id=user.tenant_id,
        user_id=user.sub,
        event_type="inbox.task_deleted",
        message=f"Deleted task #{task_id}",
        payload={"task_id": task_id},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _get_autonomy(session, tenant_id: str, user_id: str) -> int:
    # Autonomy removed — always fully autonomous (L5) so email send/delete/etc.
    # work without a gating slider.
    return 5


async def _autonomy_gate(user: PlatformTokenClaims, action: str) -> None:
    """Check the user's current autonomy level allows ``action``.

    Raises HTTPException(403) if not allowed. The exception payload includes
    the current level and a human reason so the UI can prompt for approval.
    """
    router_db = get_tenant_router()
    async with router_db.session_for(user.tenant_id) as session:
        level = await _get_autonomy(session, user.tenant_id, user.sub)
    ok, reason = autonomy_allows(level, action)
    if not ok:
        raise HTTPException(
            status_code=403,
            detail={
                "blocked_by_autonomy": True,
                "level": level,
                "action": action,
                "reason": reason,
                "hint": "Raise the autonomy slider on the Inbox page to allow this.",
            },
        )


async def _autonomy_gate_write(
    user: PlatformTokenClaims, action: str, confirmed: bool
) -> int:
    """Gate a user-initiated write, honouring explicit confirmation.

    Returns the user's autonomy level on success. Raises HTTPException(403)
    with ``needs_confirmation`` in the payload when a confirm dialog could
    unblock the action (L2-L4), or a hard block at L1 Observe.
    """
    router_db = get_tenant_router()
    async with router_db.session_for(user.tenant_id) as session:
        level = await _get_autonomy(session, user.tenant_id, user.sub)
    allowed, needs_confirmation, reason = decide_write_gate(level, action, confirmed)
    if not allowed:
        raise HTTPException(
            status_code=403,
            detail={
                "blocked_by_autonomy": True,
                "level": level,
                "action": action,
                "reason": reason,
                "needs_confirmation": needs_confirmation,
                "hint": (
                    "Confirm to proceed."
                    if needs_confirmation
                    else "Raise the autonomy slider above L1 to act on your data."
                ),
            },
        )
    return level


async def _create_reminder_task(
    user: PlatformTokenClaims, title: str, summary: str, due_at: datetime
) -> int:
    """Create a lightweight InboxTask so a snoozed item resurfaces. Returns id."""
    from app.services.lifecycle_ticker import DEFAULT_SLA_BY_PRIORITY

    router_db = get_tenant_router()
    async with router_db.session_for(user.tenant_id) as session:
        task = InboxTask(
            tenant_id=user.tenant_id,
            creator_id=user.sub,
            title=title,
            summary=summary,
            priority="medium",
            source="snooze",
            status="open",
            due_at=due_at,
            sla_minutes=DEFAULT_SLA_BY_PRIORITY.get("medium", 24 * 60),
            assignee_id="me",
        )
        session.add(task)
        await session.commit()
        await session.refresh(task)
        return task.id


async def _cached_message_detail(user_id: str, tenant_id: str, message_id: str,
                                 account: str) -> Optional[Dict[str, Any]]:
    """Build a detail-panel payload from the harvester cache (used for demo data
    and as a fallback) so we never hit the live API for a synthetic message."""
    from app.services.harvester_worker import cached_messages
    try:
        msgs = await cached_messages(user_id, tenant_id, account)
    except Exception:
        return None
    for m in msgs:
        if m.get("id") == message_id:
            return {
                "id": message_id,
                "subject": m.get("subject", ""),
                "from": m.get("from", ""),
                "from_name": m.get("from_name", ""),
                "date": m.get("date", ""),
                "snippet": m.get("snippet", ""),
                "body": m.get("snippet", ""),
                "thread_id": m.get("thread_id"),
            }
    return None


async def _demo_write_action(user, message_id: str, action: str) -> Optional[Dict[str, Any]]:
    """In demo mode, apply mail actions to the CACHE (synthetic messages have no
    upstream, so a live call would 502). archive/delete remove the row; mark_read
    clears unread. Returns a response dict if handled, else None. Runs AFTER the
    autonomy gate, so L1 still blocks as expected."""
    from app.services.demo_seed import demo_mode_enabled
    if not (demo_mode_enabled() or str(message_id).startswith("demo-")):
        return None
    from sqlalchemy import delete as _del, update as _upd
    from app.storage.models import HarvestedMessage, MessageEnrichment
    try:
        async with get_tenant_router().session_for(user.tenant_id) as s:
            base = (HarvestedMessage.tenant_id == user.tenant_id,
                    HarvestedMessage.creator_id == user.sub,
                    HarvestedMessage.external_id == message_id)
            if action in ("archive", "delete"):
                await s.execute(_del(HarvestedMessage).where(*base))
                await s.execute(_del(MessageEnrichment)
                                .where(MessageEnrichment.tenant_id == user.tenant_id)
                                .where(MessageEnrichment.creator_id == user.sub)
                                .where(MessageEnrichment.external_id == message_id))
            elif action == "mark_read":
                await s.execute(_upd(HarvestedMessage).where(*base).values(unread=0))
            await s.commit()
    except Exception as exc:  # noqa: BLE001
        logger.debug("demo write action failed: %s", exc)
    return {"status": "ok", "action": action, "message_id": message_id, "demo": True}


@router.get("/gmail/{message_id}", response_model=Dict[str, Any])
async def gmail_message_detail(
    message_id: str,
    request: Request,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    """Fetch full content of a single Gmail message for the detail panel."""
    from app.services.demo_seed import demo_mode_enabled
    # Demo / synthetic messages don't exist upstream — serve them from cache.
    if demo_mode_enabled() or message_id.startswith("demo-"):
        cached = await _cached_message_detail(user.sub, user.tenant_id, message_id, "gmail")
        if cached:
            return cached
    msg = await gmail_get_full(user.sub, message_id, user.tenant_id)
    if msg.get("error"):
        # Fall back to cache rather than erroring the panel.
        cached = await _cached_message_detail(user.sub, user.tenant_id, message_id, "gmail")
        if cached:
            return cached
        raise HTTPException(status_code=502, detail=msg["error"])
    return msg


@router.post("/gmail/{message_id}/mark-read", response_model=Dict[str, Any])
async def gmail_mark_read_endpoint(
    message_id: str,
    confirm: bool = Query(default=False),
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    await _autonomy_gate_write(user, "tag", confirm)  # mark-read is a medium-risk write
    demo = await _demo_write_action(user, message_id, "mark_read")
    if demo:
        return demo
    res = await gmail_mark_read(user.sub, message_id, user.tenant_id)
    ok = res == "OK"
    await get_audit_service().log(
        tenant_id=user.tenant_id,
        user_id=user.sub,
        event_type="gmail.mark_read" if ok else "gmail.mark_read.failed",
        message=(f"Marked message {message_id} as read" if ok
                 else f"FAILED to mark {message_id} read: {res}"),
        payload={"message_id": message_id, "result": res, "ok": ok},
    )
    if not ok:
        raise HTTPException(status_code=502, detail=res)
    return {"status": "ok", "action": "mark_read", "message_id": message_id}


@router.post("/gmail/{message_id}/archive", response_model=Dict[str, Any])
async def gmail_archive_endpoint(
    message_id: str,
    confirm: bool = Query(default=False),
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    await _autonomy_gate_write(user, "delete", confirm)  # archive is destructive-ish, treat as high
    demo = await _demo_write_action(user, message_id, "archive")
    if demo:
        return demo
    res = await gmail_archive(user.sub, message_id, user.tenant_id)
    ok = res == "OK"
    await get_audit_service().log(
        tenant_id=user.tenant_id,
        user_id=user.sub,
        event_type="gmail.archive" if ok else "gmail.archive.failed",
        message=(f"Archived message {message_id}" if ok
                 else f"FAILED to archive {message_id}: {res}"),
        payload={"message_id": message_id, "result": res, "ok": ok},
    )
    if not ok:
        raise HTTPException(status_code=502, detail=res)
    return {"status": "ok", "action": "archive", "message_id": message_id}


# --- Email feature-parity endpoints (search, flags, reply/forward) -----------

@router.get("/search", response_model=Dict[str, Any])
async def email_search_endpoint(
    q: str = Query(default="in:inbox", description="Gmail search query, e.g. 'is:unread', 'is:starred', 'from:boss'"),
    limit: int = Query(default=30, ge=1, le=100),
    account: str = Query(default="gmail"),
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    """Structured email search / folder filter. Powers the folder switcher
    (in:inbox / is:unread / is:starred / in:sent / is:important) and search box.
    (Path is /search, not /gmail/search, to avoid the /gmail/{id} route.)"""
    msgs = await gmail_messages_structured(user.sub, q, limit, user.tenant_id)
    out = []
    for m in msgs:
        labels = m.get("label_ids", []) or []
        out.append({
            "id": f"gmail:{m['id']}",
            "external_id": m["id"],
            "external_kind": "gmail",
            "account": "gmail",
            "title": m.get("subject", "(no subject)"),
            "from_name": m.get("from_name", ""),
            "summary": f"{m.get('from_name','')} — {m.get('snippet','')}",
            "snippet": m.get("snippet", ""),
            "thread_id": m.get("thread_id"),
            "created_at": m.get("date", ""),
            "is_read": "UNREAD" not in labels,
            "is_starred": "STARRED" in labels,
            "source": "email",
        })
    return {"messages": out, "count": len(out), "query": q}


@router.post("/gmail/{message_id}/mark-unread", response_model=Dict[str, Any])
async def gmail_mark_unread_endpoint(
    message_id: str,
    confirm: bool = Query(default=False),
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    await _autonomy_gate_write(user, "tag", confirm)
    demo = await _demo_write_action(user, message_id, "mark_unread")
    if demo:
        return demo
    res = await gmail_modify_labels(user.sub, message_id, add=["UNREAD"], tenant_id=user.tenant_id)
    if res != "OK":
        raise HTTPException(status_code=502, detail=res)
    return {"status": "ok", "action": "mark_unread", "message_id": message_id}


@router.post("/gmail/{message_id}/star", response_model=Dict[str, Any])
async def gmail_star_endpoint(
    message_id: str,
    confirm: bool = Query(default=False),
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    await _autonomy_gate_write(user, "tag", confirm)
    demo = await _demo_write_action(user, message_id, "star")
    if demo:
        return demo
    res = await gmail_modify_labels(user.sub, message_id, add=["STARRED"], tenant_id=user.tenant_id)
    if res != "OK":
        raise HTTPException(status_code=502, detail=res)
    return {"status": "ok", "action": "star", "message_id": message_id}


@router.post("/gmail/{message_id}/unstar", response_model=Dict[str, Any])
async def gmail_unstar_endpoint(
    message_id: str,
    confirm: bool = Query(default=False),
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    await _autonomy_gate_write(user, "tag", confirm)
    demo = await _demo_write_action(user, message_id, "unstar")
    if demo:
        return demo
    res = await gmail_modify_labels(user.sub, message_id, remove=["STARRED"], tenant_id=user.tenant_id)
    if res != "OK":
        raise HTTPException(status_code=502, detail=res)
    return {"status": "ok", "action": "unstar", "message_id": message_id}


def _addr_only(s: str) -> str:
    """Extract the bare email address from a 'Name <email>' header."""
    import re as _re
    m = _re.search(r"<([^>]+)>", s or "")
    return (m.group(1) if m else (s or "")).strip()


@router.post("/gmail/{message_id}/reply", response_model=Dict[str, Any])
async def gmail_reply_endpoint(
    message_id: str,
    body: Dict[str, Any],
    confirm: bool = Query(default=False),
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    """Reply to a Gmail message. body: {text, reply_all?: bool}."""
    await _autonomy_gate_write(user, "send", confirm)
    text = str(body.get("text") or body.get("body") or "")
    orig = await gmail_get_full(user.sub, message_id, user.tenant_id)
    if orig.get("error"):
        raise HTTPException(status_code=502, detail=orig["error"])
    to = _addr_only(orig.get("from") or orig.get("from_address") or "")
    subj = orig.get("subject", "") or ""
    if not subj.lower().startswith("re:"):
        subj = f"Re: {subj}"
    cc = ""
    if body.get("reply_all"):
        cc = orig.get("cc", "") or ""
    res = await gmail_send(user.sub, to, subj, text, user.tenant_id)
    ok = "sent" in res.lower()
    await get_audit_service().log(
        tenant_id=user.tenant_id, user_id=user.sub,
        event_type="gmail.reply" if ok else "gmail.reply.failed",
        message=f"Reply to {to}: {subj}", payload={"message_id": message_id, "to": to, "result": res},
    )
    if not ok:
        raise HTTPException(status_code=502, detail=res)
    # Best-effort: mark the original answered/read.
    await gmail_modify_labels(user.sub, message_id, remove=["UNREAD"], tenant_id=user.tenant_id)
    return {"status": "ok", "action": "reply", "to": to, "cc": cc}


@router.post("/gmail/{message_id}/forward", response_model=Dict[str, Any])
async def gmail_forward_endpoint(
    message_id: str,
    body: Dict[str, Any],
    confirm: bool = Query(default=False),
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    """Forward a Gmail message. body: {to, text?}."""
    await _autonomy_gate_write(user, "send", confirm)
    to = str(body.get("to") or "").strip()
    if not to:
        raise HTTPException(status_code=400, detail="'to' is required")
    note = str(body.get("text") or "")
    orig = await gmail_get_full(user.sub, message_id, user.tenant_id)
    if orig.get("error"):
        raise HTTPException(status_code=502, detail=orig["error"])
    subj = orig.get("subject", "") or ""
    if not subj.lower().startswith("fwd:"):
        subj = f"Fwd: {subj}"
    quoted = (
        f"{note}\n\n---------- Forwarded message ----------\n"
        f"From: {orig.get('from','')}\nDate: {orig.get('date','')}\n"
        f"Subject: {orig.get('subject','')}\n\n{orig.get('body','')}"
    )
    res = await gmail_send(user.sub, to, subj, quoted, user.tenant_id)
    ok = "sent" in res.lower()
    if not ok:
        raise HTTPException(status_code=502, detail=res)
    return {"status": "ok", "action": "forward", "to": to}


@router.get("/gmail/{message_id}/attachment/{attachment_id}")
async def gmail_attachment_download(
    message_id: str,
    attachment_id: str,
    filename: str = Query(default="attachment"),
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Response:
    """Download a Gmail attachment (raw bytes, as a file)."""
    raw, err = await gmail_get_attachment(user.sub, message_id, attachment_id, user.tenant_id)
    if err or raw is None:
        raise HTTPException(status_code=502, detail=err or "Attachment unavailable")
    safe = (filename or "attachment").replace('"', "").replace("\n", " ")
    return Response(
        content=raw,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{safe}"'},
    )


# --- Scheduled send -----------------------------------------------------------

@router.post("/{account}/schedule", response_model=Dict[str, Any])
async def schedule_email_endpoint(
    account: str,
    payload: Dict[str, Any],
    confirm: bool = Query(default=False),
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    """Queue an email to send later. body: {to, cc, subject, body, send_at(ISO)}."""
    await _autonomy_gate_write(user, "send", confirm)
    raw_dt = str(payload.get("send_at") or "")
    try:
        dt = datetime.fromisoformat(raw_dt.replace("Z", "+00:00"))
    except Exception:
        raise HTTPException(status_code=400, detail="invalid send_at (use ISO-8601)")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    router_db = get_tenant_router()
    async with router_db.session_for(user.tenant_id) as session:
        row = ScheduledEmail(
            tenant_id=user.tenant_id, creator_id=user.sub,
            account=(account or "gmail"), to_addr=str(payload.get("to") or ""),
            cc_addr=payload.get("cc"), subject=str(payload.get("subject") or ""),
            body=str(payload.get("body") or ""), send_at=dt,
        )
        session.add(row); await session.commit(); await session.refresh(row)
    return {"status": "ok", "id": row.id, "send_at": dt.isoformat()}


@router.get("/scheduled", response_model=Dict[str, Any])
async def list_scheduled_endpoint(
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    router_db = get_tenant_router()
    async with router_db.session_for(user.tenant_id) as session:
        rows = (await session.execute(
            select(ScheduledEmail)
            .where(ScheduledEmail.tenant_id == user.tenant_id)
            .where(ScheduledEmail.creator_id == user.sub)
            .where(ScheduledEmail.status == "pending")
            .order_by(ScheduledEmail.send_at)
        )).scalars().all()
    return {"scheduled": [
        {"id": r.id, "to": r.to_addr, "cc": r.cc_addr, "subject": r.subject,
         "account": r.account, "send_at": r.send_at.isoformat()} for r in rows
    ]}


@router.delete("/scheduled/{sid}", response_model=Dict[str, Any])
async def cancel_scheduled_endpoint(
    sid: int,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    router_db = get_tenant_router()
    async with router_db.session_for(user.tenant_id) as session:
        row = (await session.execute(
            select(ScheduledEmail).where(ScheduledEmail.id == sid)
            .where(ScheduledEmail.tenant_id == user.tenant_id)
            .where(ScheduledEmail.creator_id == user.sub)
        )).scalars().first()
        if not row:
            raise HTTPException(status_code=404, detail="not found")
        row.status = "cancelled"; await session.commit()
    return {"status": "ok", "cancelled": sid}


async def send_due_scheduled_emails(tenant_id: str) -> int:
    """Send any pending scheduled emails whose time has come (called by the
    lifecycle ticker). Uses each row's creator's connector token."""
    now = datetime.now(timezone.utc)
    router_db = get_tenant_router()
    sent = 0
    async with router_db.session_for(tenant_id) as session:
        rows = (await session.execute(
            select(ScheduledEmail)
            .where(ScheduledEmail.tenant_id == tenant_id)
            .where(ScheduledEmail.status == "pending")
            .where(ScheduledEmail.send_at <= now)
        )).scalars().all()
        for r in rows:
            try:
                if r.account == "outlook":
                    res = await outlook_send(r.creator_id, r.to_addr, r.subject, r.body, r.tenant_id)
                else:
                    res = await gmail_send(r.creator_id, r.to_addr, r.subject, r.body, r.tenant_id)
                ok = "sent" in str(res).lower() or str(res).strip() == "OK"
                r.status = "sent" if ok else "failed"
                r.error = None if ok else str(res)[:500]
                r.sent_at = now if ok else None
                if ok:
                    sent += 1
            except Exception as exc:  # noqa: BLE001
                r.status = "failed"; r.error = str(exc)[:500]
        await session.commit()
    return sent


@router.get("/outlook/{message_id}", response_model=Dict[str, Any])
async def outlook_message_detail(
    message_id: str,
    request: Request,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    from app.services.demo_seed import demo_mode_enabled
    if demo_mode_enabled() or message_id.startswith("demo-"):
        cached = await _cached_message_detail(user.sub, user.tenant_id, message_id, "outlook")
        if cached:
            return cached
    msg = await outlook_get_full(user.sub, message_id, user.tenant_id)
    if msg.get("error"):
        cached = await _cached_message_detail(user.sub, user.tenant_id, message_id, "outlook")
        if cached:
            return cached
        raise HTTPException(status_code=502, detail=msg["error"])
    return msg


@router.post("/outlook/{message_id}/mark-read", response_model=Dict[str, Any])
async def outlook_mark_read_endpoint(
    message_id: str,
    confirm: bool = Query(default=False),
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    await _autonomy_gate_write(user, "tag", confirm)
    demo = await _demo_write_action(user, message_id, "mark_read")
    if demo:
        return demo
    res = await outlook_mark_read(user.sub, message_id, user.tenant_id)
    ok = res == "OK"
    await get_audit_service().log(
        tenant_id=user.tenant_id,
        user_id=user.sub,
        event_type="outlook.mark_read",
        message=f"Marked Outlook {message_id} read",
        payload={"message_id": message_id, "result": res},
    )
    if not ok:
        raise HTTPException(status_code=502, detail=res)
    return {"status": "ok", "action": "mark_read", "message_id": message_id}


@router.post("/outlook/{message_id}/archive", response_model=Dict[str, Any])
async def outlook_archive_endpoint(
    message_id: str,
    confirm: bool = Query(default=False),
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    await _autonomy_gate_write(user, "delete", confirm)
    demo = await _demo_write_action(user, message_id, "archive")
    if demo:
        return demo
    res = await outlook_archive(user.sub, message_id, user.tenant_id)
    ok = res == "OK"
    await get_audit_service().log(
        tenant_id=user.tenant_id,
        user_id=user.sub,
        event_type="outlook.archive",
        message=f"Archived Outlook {message_id}",
        payload={"message_id": message_id, "result": res},
    )
    if not ok:
        raise HTTPException(status_code=502, detail=res)
    return {"status": "ok", "action": "archive", "message_id": message_id}


# ---------------------------------------------------------------------------
# Write capabilities (send / schedule / delete / snooze) — all autonomy-gated
# and audited. A user-confirmed action satisfies the L2-L4 approval ladder.
# ---------------------------------------------------------------------------


async def _audit_write(user, event_type: str, message: str, payload: dict) -> None:
    await get_audit_service().log(
        tenant_id=user.tenant_id,
        user_id=user.sub,
        event_type=event_type,
        message=message,
        payload=payload,
    )


@router.post("/gmail/send", response_model=WriteActionResponse)
async def gmail_send_endpoint(
    payload: SendEmailRequest,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> WriteActionResponse:
    level = await _autonomy_gate_write(user, "send", payload.confirm)
    from app.services.demo_seed import demo_mode_enabled
    if demo_mode_enabled():
        await _audit_write(user, "gmail.send", f"[demo] Sent email to {payload.to}",
                           {"to": payload.to, "subject": payload.subject, "demo": True, "ok": True})
        return WriteActionResponse(status="ok", action="send",
                                   result=f"Email sent to {payload.to} (demo mode).", autonomy_level=level)
    res = await gmail_send(user.sub, payload.to, payload.subject, payload.body, user.tenant_id)
    ok = res.lower().startswith("email sent")
    await _audit_write(
        user, "gmail.send", f"Sent email to {payload.to}",
        {"to": payload.to, "subject": payload.subject, "result": res, "ok": ok},
    )
    if not ok:
        raise HTTPException(status_code=502, detail=res)
    return WriteActionResponse(status="ok", action="send", result=res, autonomy_level=level)


@router.post("/outlook/send", response_model=WriteActionResponse)
async def outlook_send_endpoint(
    payload: SendEmailRequest,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> WriteActionResponse:
    level = await _autonomy_gate_write(user, "send", payload.confirm)
    from app.services.demo_seed import demo_mode_enabled
    if demo_mode_enabled():
        await _audit_write(user, "outlook.send", f"[demo] Sent Outlook email to {payload.to}",
                           {"to": payload.to, "subject": payload.subject, "demo": True, "ok": True})
        return WriteActionResponse(status="ok", action="send",
                                   result=f"Email sent to {payload.to} (demo mode).", autonomy_level=level)
    res = await outlook_send(user.sub, payload.to, payload.subject, payload.body, user.tenant_id)
    ok = res.lower().startswith("email sent")
    await _audit_write(
        user, "outlook.send", f"Sent Outlook email to {payload.to}",
        {"to": payload.to, "subject": payload.subject, "result": res, "ok": ok},
    )
    if not ok:
        # The Microsoft connector intentionally does NOT request Mail.Send, so a
        # real send fails with an access/scope error. Surface an actionable 403
        # instead of an opaque 502 so the UI can tell the user why.
        low = res.lower()
        if any(k in low for k in ("scope", "permission", "accessdenied", "forbidden", "403")):
            raise HTTPException(
                status_code=403,
                detail="Outlook isn't authorized to send mail (the Mail.Send permission "
                       "wasn't granted). Add Mail.Send to the Microsoft 365 app and reconnect.",
            )
        raise HTTPException(status_code=502, detail=res)
    return WriteActionResponse(status="ok", action="send", result=res, autonomy_level=level)


@router.get("/calendar/events")
async def calendar_events_endpoint(
    days_ahead: int = Query(70, ge=1, le=366),
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    """List the user's connected Google + Outlook calendar events (cache-first).

    The inbox summary is intentionally email-only (see inbox_summary), so the
    Calendar page reads events here instead. Merges both connected providers.
    """
    from app.services.harvester_worker import cached_events, cache_is_fresh
    from app.services.demo_seed import is_demo_user
    _demo = is_demo_user(user.sub)

    events: List[Dict[str, Any]] = []
    try:
        # Demo accounts read the seeded cache; real accounts LIVE-crawl the
        # requested range so any month the user browses is fetched fresh (the
        # cache only holds the harvester's narrow window).
        if _demo:
            g = await cached_events(user.sub, user.tenant_id, "google_calendar")
        else:
            g = await calendar_events_structured(user.sub, days_ahead=days_ahead, tenant_id=user.tenant_id)
        for e in g or []:
            ev = dict(e); ev["account"] = "google_calendar"; events.append(ev)
    except Exception:  # noqa: BLE001
        pass
    if not _demo:
        try:
            o = await outlook_calendar_events(user.sub, days_ahead=days_ahead, tenant_id=user.tenant_id)
            for e in o or []:
                ev = dict(e); ev["account"] = "outlook"; events.append(ev)
        except Exception:  # noqa: BLE001
            pass
    events.sort(key=lambda x: x.get("start") or "")
    return {"events": events}


@router.post("/calendar/create", response_model=WriteActionResponse)
async def calendar_create_endpoint(
    payload: CreateEventRequest,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> WriteActionResponse:
    level = await _autonomy_gate_write(user, "schedule", payload.confirm)
    from app.services.demo_seed import demo_mode_enabled
    if demo_mode_enabled():
        await _audit_write(user, "calendar.create", f"[demo] Created event '{payload.title}'",
                           {"title": payload.title, "start": payload.start, "demo": True, "ok": True})
        return WriteActionResponse(status="ok", action="schedule",
                                   result=f"Event '{payload.title}' created (demo mode).", autonomy_level=level)
    res = await calendar_create_event(
        user.sub, payload.title, payload.start, payload.duration_min,
        payload.attendees, payload.description, user.tenant_id,
    )
    ok = "created" in res.lower()
    await _audit_write(
        user, "calendar.create", f"Created event '{payload.title}'",
        {"title": payload.title, "start": payload.start, "result": res, "ok": ok},
    )
    if not ok:
        raise HTTPException(status_code=502, detail=res)
    return WriteActionResponse(status="ok", action="schedule", result=res, autonomy_level=level)


@router.post("/gmail/{message_id}/delete", response_model=WriteActionResponse)
async def gmail_delete_endpoint(
    message_id: str,
    confirm: bool = Query(default=False),
    user: PlatformTokenClaims = Depends(get_current_user),
) -> WriteActionResponse:
    level = await _autonomy_gate_write(user, "delete", confirm)
    if await _demo_write_action(user, message_id, "delete"):
        return WriteActionResponse(status="ok", action="delete", result="trashed", autonomy_level=level)
    res = await gmail_trash(user.sub, message_id, user.tenant_id)
    ok = res == "OK"
    await _audit_write(
        user, "gmail.delete", f"Trashed Gmail message {message_id}",
        {"message_id": message_id, "result": res},
    )
    if not ok:
        raise HTTPException(status_code=502, detail=res)
    return WriteActionResponse(status="ok", action="delete", result="trashed", autonomy_level=level)


@router.post("/outlook/{message_id}/delete", response_model=WriteActionResponse)
async def outlook_delete_endpoint(
    message_id: str,
    confirm: bool = Query(default=False),
    user: PlatformTokenClaims = Depends(get_current_user),
) -> WriteActionResponse:
    level = await _autonomy_gate_write(user, "delete", confirm)
    if await _demo_write_action(user, message_id, "delete"):
        return WriteActionResponse(status="ok", action="delete", result="deleted", autonomy_level=level)
    res = await outlook_delete(user.sub, message_id, user.tenant_id)
    ok = res == "OK"
    await _audit_write(
        user, "outlook.delete", f"Deleted Outlook message {message_id}",
        {"message_id": message_id, "result": res},
    )
    if not ok:
        raise HTTPException(status_code=502, detail=res)
    return WriteActionResponse(status="ok", action="delete", result="deleted", autonomy_level=level)


@router.get("/gmail/thread/{thread_id}", response_model=Dict[str, Any])
async def gmail_thread_endpoint(
    thread_id: str,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    """Read every message in a Gmail thread (read-only, allowed at any level)."""
    data = await gmail_thread_get(user.sub, thread_id, user.tenant_id)
    if data.get("error"):
        raise HTTPException(status_code=502, detail=data["error"])
    return data


@router.post("/gmail/{message_id}/snooze", response_model=WriteActionResponse)
async def gmail_snooze_endpoint(
    message_id: str,
    payload: SnoozeRequest,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> WriteActionResponse:
    level = await _autonomy_gate_write(user, "snooze", payload.confirm)
    # Mark read so it stops nagging, then create a resurface reminder.
    await gmail_mark_read(user.sub, message_id, user.tenant_id)
    due = _parse_snooze_until(payload.until)
    task_id = await _create_reminder_task(
        user,
        title="Snoozed email",
        summary=f"Resurfaced snoozed Gmail message {message_id}",
        due_at=due,
    )
    await _audit_write(
        user, "gmail.snooze", f"Snoozed Gmail {message_id} until {due.isoformat()}",
        {"message_id": message_id, "until": due.isoformat(), "reminder_task_id": task_id},
    )
    return WriteActionResponse(
        status="ok", action="snooze",
        result=f"Snoozed until {due.isoformat()} (reminder task #{task_id})",
        autonomy_level=level,
    )


def _parse_snooze_until(until: Optional[str]) -> datetime:
    """Parse an ISO-8601 snooze time; default to 24h from now."""
    if until:
        try:
            dt = datetime.fromisoformat(until.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    from datetime import timedelta
    return datetime.now(timezone.utc) + timedelta(days=1)


def _row_to_dict(row: InboxTask) -> dict:
    return {
        "id": row.id,
        "title": row.title,
        "summary": row.summary,
        "source": row.source,
        "priority": row.priority,
        "status": row.status,
        "payload": row.payload,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "due_at": row.due_at.isoformat() if row.due_at else None,
        "sla_minutes": row.sla_minutes,
        "assignee_id": row.assignee_id,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "escalated_at": row.escalated_at.isoformat() if row.escalated_at else None,
        "escalation_count": row.escalation_count or 0,
    }
