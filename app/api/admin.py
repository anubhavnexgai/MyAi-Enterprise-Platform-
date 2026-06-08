"""Super-admin endpoints — read-only per-employee usage analytics.

Gated by the ``super_admin`` role. Aggregates the existing ``AuditLog`` (the
same per-user event stream the dashboard/insights already write) ACROSS all
users in the tenant, so the admin can see who is using MyAi and how much.

Milestone 1 is read-only (no account management). Token/cost columns are
estimates until the model router emits real per-call usage (Milestone 2).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select

from app.auth.jwt import PlatformTokenClaims
from app.auth.middleware import require_roles
from app.services.employees import list_employees
from app.storage.models import AuditLog
from app.tenants.router import get_tenant_router

router = APIRouter(prefix="/api/admin", tags=["admin"])

_AUTH_EVENTS = ("auth.",)
# A "chat turn" — native copilot or a bridged Odysseus chat/agent turn. All of
# these roll up into the per-employee "chats" column and the active-user counts.
_CHAT_EVENTS = ("copilot.chat", "oui.chat", "oui.agent")
# Events whose payload carries real token counts (input/output/total_tokens),
# written by app/services/usage.py and app/api/copilot.py. Summed for real
# per-employee token totals; kept in sync with app/services/usage.py.
_TOKEN_EVENTS = ("copilot.chat", "oui.chat", "oui.agent", "oui.research")
# Fallback estimate for legacy chat rows that predate real token accounting.
_EST_TOKENS_PER_CHAT = 800


def _is_action(event_type: str) -> bool:
    """An 'action' = anything that isn't a chat turn or a pure auth event."""
    if event_type in _CHAT_EVENTS:
        return False
    return not any(event_type.startswith(p) for p in _AUTH_EVENTS)


def _tool_name(event_type: str) -> str | None:
    if event_type.startswith("tool."):
        return event_type[len("tool."):]
    return None


async def _aggregate(tenant_id: str) -> Dict[str, dict]:
    """Per-user rollup from AuditLog: {creator_id: {chats, actions, tools, last_active}}."""
    router_db = get_tenant_router()
    async with router_db.session_for(tenant_id) as session:
        rows = (
            await session.execute(
                select(
                    AuditLog.creator_id,
                    AuditLog.event_type,
                    func.count(AuditLog.id),
                    func.max(AuditLog.created_at),
                )
                .where(AuditLog.tenant_id == tenant_id)
                .group_by(AuditLog.creator_id, AuditLog.event_type)
            )
        ).all()

    agg: Dict[str, dict] = {}
    for creator_id, event_type, count, last in rows:
        a = agg.setdefault(
            creator_id,
            {"chats": 0, "actions": 0, "tools": set(), "events": 0,
             "tokens": 0, "last_active": None},
        )
        count = int(count or 0)
        a["events"] += count
        if event_type in _CHAT_EVENTS:
            a["chats"] += count
        elif _is_action(event_type):
            a["actions"] += count
        t = _tool_name(event_type)
        if t:
            a["tools"].add(t)
        if last and (a["last_active"] is None or last > a["last_active"]):
            a["last_active"] = last

    # Real token totals from the payloads of token-bearing events.
    async with router_db.session_for(tenant_id) as session:
        tok_rows = (
            await session.execute(
                select(AuditLog.creator_id, AuditLog.payload)
                .where(AuditLog.tenant_id == tenant_id)
                .where(AuditLog.event_type.in_(_TOKEN_EVENTS))
            )
        ).all()
    for creator_id, payload in tok_rows:
        if not isinstance(payload, dict):
            continue
        a = agg.setdefault(
            creator_id,
            {"chats": 0, "actions": 0, "tools": set(), "events": 0,
             "tokens": 0, "last_active": None},
        )
        a["tokens"] += int(payload.get("total_tokens") or 0)
    return agg


def _row_for(user_id: str, agg: Dict[str, dict]) -> dict:
    a = agg.get(user_id, {"chats": 0, "actions": 0, "tools": set(), "events": 0,
                          "tokens": 0, "last_active": None})
    real = int(a.get("tokens") or 0)
    return {
        "chats": a["chats"],
        "actions": a["actions"],
        "tools_used": sorted(a["tools"]),
        "events": a["events"],
        # Real tokens when we have them; fall back to the estimate for legacy
        # chat rows that predate token accounting.
        "tokens": real,
        "est_tokens": real if real else a["chats"] * _EST_TOKENS_PER_CHAT,
        "last_active": a["last_active"].isoformat() if a["last_active"] else None,
    }


@router.get("/employees", response_model=Dict[str, Any])
async def admin_employees(
    user: PlatformTokenClaims = Depends(require_roles(["super_admin"])),
) -> Dict[str, Any]:
    """List every employee in the tenant with their usage summary."""
    tenant_id = user.tenant_id
    employees = await list_employees(tenant_id)
    agg = await _aggregate(tenant_id)

    out: List[dict] = []
    for e in employees:
        out.append({**e, **_row_for(e["user_id"], agg)})
    # Sort most-active first.
    out.sort(key=lambda r: (r["chats"] + r["actions"]), reverse=True)
    return {"tenant_id": tenant_id, "count": len(out), "employees": out}


@router.get("/usage", response_model=Dict[str, Any])
async def admin_usage(
    days: int = Query(7, ge=1, le=90),
    user: PlatformTokenClaims = Depends(require_roles(["super_admin"])),
) -> Dict[str, Any]:
    """Org-wide totals + a daily activity trend over the last ``days`` days."""
    tenant_id = user.tenant_id
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=7)
    window_start = today_start - timedelta(days=days - 1)

    router_db = get_tenant_router()
    async with router_db.session_for(tenant_id) as session:
        # Active employees (distinct creators with any event) today / this week.
        active_today = int((await session.execute(
            select(func.count(func.distinct(AuditLog.creator_id)))
            .where(AuditLog.tenant_id == tenant_id)
            .where(AuditLog.created_at >= today_start))).scalar() or 0)
        active_week = int((await session.execute(
            select(func.count(func.distinct(AuditLog.creator_id)))
            .where(AuditLog.tenant_id == tenant_id)
            .where(AuditLog.created_at >= week_start))).scalar() or 0)

        # Daily totals (chats vs everything) for the trend.
        day_rows = (await session.execute(
            select(
                func.date(AuditLog.created_at),
                func.count(AuditLog.id),
                func.sum(case((AuditLog.event_type.in_(_CHAT_EVENTS), 1), else_=0)),
            )
            .where(AuditLog.tenant_id == tenant_id)
            .where(AuditLog.created_at >= window_start)
            .group_by(func.date(AuditLog.created_at))
            .order_by(func.date(AuditLog.created_at))
        )).all()

    trend = [
        {"date": str(d), "events": int(total or 0), "chats": int(chats or 0)}
        for (d, total, chats) in day_rows
    ]

    agg = await _aggregate(tenant_id)
    total_chats = sum(a["chats"] for a in agg.values())
    total_actions = sum(a["actions"] for a in agg.values())
    total_events = sum(a["events"] for a in agg.values())
    total_tokens = sum(int(a.get("tokens") or 0) for a in agg.values())
    employees = await list_employees(tenant_id)

    return {
        "tenant_id": tenant_id,
        "window_days": days,
        "totals": {
            "employees": len(employees),
            "active_today": active_today,
            "active_week": active_week,
            "chats": total_chats,
            "actions": total_actions,
            "events": total_events,
            "tokens": total_tokens,
            "est_tokens": total_tokens if total_tokens else total_chats * _EST_TOKENS_PER_CHAT,
        },
        "trend": trend,
    }


@router.get("/usage/{user_id}", response_model=Dict[str, Any])
async def admin_usage_for(
    user_id: str,
    days: int = Query(30, ge=1, le=90),
    user: PlatformTokenClaims = Depends(require_roles(["super_admin"])),
) -> Dict[str, Any]:
    """One employee's daily activity time series."""
    tenant_id = user.tenant_id
    now = datetime.now(timezone.utc)
    window_start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)

    router_db = get_tenant_router()
    async with router_db.session_for(tenant_id) as session:
        day_rows = (await session.execute(
            select(func.date(AuditLog.created_at), func.count(AuditLog.id))
            .where(AuditLog.tenant_id == tenant_id)
            .where(AuditLog.creator_id == user_id)
            .where(AuditLog.created_at >= window_start)
            .group_by(func.date(AuditLog.created_at))
            .order_by(func.date(AuditLog.created_at))
        )).all()

    agg = await _aggregate(tenant_id)
    return {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "summary": _row_for(user_id, agg),
        "trend": [{"date": str(d), "events": int(c or 0)} for (d, c) in day_rows],
    }
