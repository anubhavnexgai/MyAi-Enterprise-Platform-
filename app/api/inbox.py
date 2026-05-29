"""Agent inbox + tasks endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response
from sqlalchemy import select

from app.agent.connector_tools import (
    calendar_events_structured,
    gmail_archive,
    gmail_get_full,
    gmail_mark_read,
    gmail_messages_structured,
)
from app.api.preferences import autonomy_allows
from app.storage.models import UserPreference
from sqlalchemy import select as _select  # alias to avoid clash with existing select
from app.agent.outlook_tools import (
    outlook_archive,
    outlook_calendar_events,
    outlook_get_full,
    outlook_mark_read,
    outlook_messages_structured,
)
from app.auth.jwt import PlatformTokenClaims
from app.auth.middleware import get_current_user
from app.models.schemas import InboxTaskCreate, InboxTaskOut
from app.models.schemas import InboxTaskUpdate
from app.services.audit import get_audit_service
from app.services.harvester_gateway import get_harvester_gateway
from app.storage.models import InboxTask
from app.tenants.router import get_tenant_router

router = APIRouter(prefix="/api/inbox", tags=["inbox"])


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

    gmail_fresh = await cache_is_fresh(user.sub, user.tenant_id, "gmail", "messages")
    if gmail_fresh:
        gmail_msgs = await cached_messages(user.sub, user.tenant_id, "gmail")
    else:
        # First load / stale cache — pull live and let the worker catch up next tick.
        gmail_msgs = await gmail_messages_structured(
            user.sub, "is:unread in:inbox", limit=15, tenant_id=user.tenant_id
        )

    outlook_fresh = await cache_is_fresh(user.sub, user.tenant_id, "outlook", "messages")
    if outlook_fresh:
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
        })
    for m in gmail_msgs:
        prio = _classify_email_priority(
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
        "pause": "blocked",
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
    pref = (
        await session.execute(
            _select(UserPreference)
            .where(UserPreference.tenant_id == tenant_id)
            .where(UserPreference.creator_id == user_id)
        )
    ).scalars().first()
    return int(pref.autonomy_level) if pref else 1


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


@router.get("/gmail/{message_id}", response_model=Dict[str, Any])
async def gmail_message_detail(
    message_id: str,
    request: Request,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    """Fetch full content of a single Gmail message for the detail panel."""
    msg = await gmail_get_full(user.sub, message_id, user.tenant_id)
    if msg.get("error"):
        raise HTTPException(status_code=502, detail=msg["error"])
    return msg


@router.post("/gmail/{message_id}/mark-read", response_model=Dict[str, Any])
async def gmail_mark_read_endpoint(
    message_id: str,
    request: Request,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    await _autonomy_gate(user, "tag")  # mark-read is a low-medium risk write
    res = await gmail_mark_read(user.sub, message_id, user.tenant_id)
    ok = res == "OK"
    await get_audit_service().log(
        tenant_id=user.tenant_id,
        user_id=user.sub,
        event_type="gmail.mark_read",
        message=f"Marked message {message_id} as read",
        payload={"message_id": message_id, "result": res},
    )
    if not ok:
        raise HTTPException(status_code=502, detail=res)
    return {"status": "ok", "action": "mark_read", "message_id": message_id}


@router.post("/gmail/{message_id}/archive", response_model=Dict[str, Any])
async def gmail_archive_endpoint(
    message_id: str,
    request: Request,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    await _autonomy_gate(user, "delete")  # archive is destructive-ish, treat as high
    res = await gmail_archive(user.sub, message_id, user.tenant_id)
    ok = res == "OK"
    await get_audit_service().log(
        tenant_id=user.tenant_id,
        user_id=user.sub,
        event_type="gmail.archive",
        message=f"Archived message {message_id}",
        payload={"message_id": message_id, "result": res},
    )
    if not ok:
        raise HTTPException(status_code=502, detail=res)
    return {"status": "ok", "action": "archive", "message_id": message_id}


@router.get("/outlook/{message_id}", response_model=Dict[str, Any])
async def outlook_message_detail(
    message_id: str,
    request: Request,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    msg = await outlook_get_full(user.sub, message_id, user.tenant_id)
    if msg.get("error"):
        raise HTTPException(status_code=502, detail=msg["error"])
    return msg


@router.post("/outlook/{message_id}/mark-read", response_model=Dict[str, Any])
async def outlook_mark_read_endpoint(
    message_id: str,
    request: Request,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    await _autonomy_gate(user, "tag")
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
    request: Request,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    await _autonomy_gate(user, "delete")
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
