"""Agent inbox + tasks endpoints."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response
from sqlalchemy import select

from app.auth.jwt import PlatformTokenClaims
from app.auth.middleware import get_current_user
from app.models.schemas import InboxTaskCreate, InboxTaskOut
from app.models.schemas import InboxTaskUpdate
from app.services.audit import get_audit_service
from app.services.harvester_gateway import get_harvester_gateway
from app.storage.models import InboxTask
from app.tenants.router import get_tenant_router

router = APIRouter(prefix="/api/inbox", tags=["inbox"])


@router.get("", response_model=Dict[str, Any])
async def inbox_summary(
    request: Request,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    """Aggregate endpoint - tasks + lightweight stats."""
    gateway = get_harvester_gateway()
    tasks = await gateway.get_user_data(
        user_id=user.sub, tenant_id=user.tenant_id, query_type="inbox_tasks", limit=100
    )
    activity = await gateway.get_user_data(
        user_id=user.sub, tenant_id=user.tenant_id, query_type="agent_activity"
    )
    return {
        "scope": {"tenant_id": user.tenant_id, "user_id": user.sub},
        "tasks": tasks.get("data"),
        "stats": activity.get("data"),
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
    router_db = get_tenant_router()
    async with router_db.session_for(user.tenant_id) as session:
        task = InboxTask(
            tenant_id=user.tenant_id,
            creator_id=user.sub,
            title=payload.title,
            summary=payload.summary,
            priority=payload.priority or "normal",
            source=payload.source or "manual",
            payload=payload.payload,
            status="open",
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
        return {
            **_row_to_dict(task),
            # Stitch in mock detail for now; real harvester data plugs in later.
            **_mock_detail_fields(),
        }


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
    """Mock fields the harvester will eventually fill in."""
    return {
        "ai_strategy": "Final Incentive — fee waiver + retention credit",
        "ai_confidence": 0.62,
        "progress": {"done": 4, "total": 6, "label": "Level 4 of 6 — Negotiation"},
        "steps": [
            {"id": 1, "status": "done", "tool": "harvester.lookup", "description": "Pulled account history + tenure"},
            {"id": 2, "status": "done", "tool": "competitor_scan", "description": "Found Monzo switch bonus £150"},
            {"id": 3, "status": "done", "tool": "planner.compose", "description": "Selected Level 4 strategy"},
            {"id": 4, "status": "done", "tool": "drafter.compose", "description": "Drafted retention offer"},
            {"id": 5, "status": "running", "tool": "approval.queue", "description": "Awaiting supervisor approval for discount"},
            {"id": 6, "status": "pending", "tool": "execute.offer", "description": "Send finalised offer to customer"},
        ],
        "ai_reasoning": (
            "Customer cites the Monzo switch bonus and zero fee. Value framing alone will not hold; "
            "recommend the fee waiver plus matched bonus, and escalate as the ask exceeds my discount authority."
        ),
        "incentives_offered": [
            {"label": "20% discount unlocked", "tone": "good"},
            {"label": "12-month fee waiver", "tone": "neutral"},
            {"label": "GBP 100 retention credit", "tone": "neutral"},
            {"label": "1.5% cashback boost", "tone": "neutral"},
        ],
        "conversation": [
            {"role": "customer", "name": "James Whitfield", "text": "I have been with you years but Monzo is offering no fee and 150 pounds to switch."},
            {"role": "ai", "text": "I hear you, and I appreciate your loyalty over the past four and a half years. Let me see what I can do to keep your banking with us."},
            {"role": "customer", "name": "James Whitfield", "text": "It would need to beat what they are offering."},
            {"role": "ai", "text": "I can waive your monthly fee for 12 months and add a retention credit — let me confirm the bonus match with a manager."},
        ],
        "recommended_action": {
            "label": "Approve 12-month fee waiver + GBP 150 matched switch bonus",
            "tone": "warn",
        },
    }


def _mock_task_detail(task_id: int) -> dict:
    """For task IDs that don't exist in DB yet — return a realistic shaped record."""
    return {
        "id": task_id,
        "title": f"Task #{task_id}",
        "summary": "Mock detail — task not yet persisted",
        "status": "open",
        "priority": "high",
        "source": "agent",
        "created_at": None,
        "updated_at": None,
        **_mock_detail_fields(),
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
    }
