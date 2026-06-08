"""Copilot insights — real activity stats from audit log + connectors."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select, func

from app.agent.connector_tools import gmail_counts
from app.services.vision import vision_status
from app.auth.jwt import PlatformTokenClaims
from app.auth.middleware import get_current_user
from app.storage.models import AuditLog
from app.tenants.router import get_tenant_router

router = APIRouter(prefix="/api/insights", tags=["insights"])


@router.get("", response_model=Dict[str, Any])
async def insights(
    request: Request,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=7)

    router_db = get_tenant_router()
    async with router_db.session_for(user.tenant_id) as session:
        actions_today = int(
            (
                await session.execute(
                    select(func.count(AuditLog.id))
                    .where(AuditLog.tenant_id == user.tenant_id)
                    .where(AuditLog.creator_id == user.sub)
                    .where(AuditLog.created_at >= today_start)
                )
            ).scalar()
            or 0
        )
        actions_week = int(
            (
                await session.execute(
                    select(func.count(AuditLog.id))
                    .where(AuditLog.tenant_id == user.tenant_id)
                    .where(AuditLog.creator_id == user.sub)
                    .where(AuditLog.created_at >= week_start)
                )
            ).scalar()
            or 0
        )
        chat_count = int(
            (
                await session.execute(
                    select(func.count(AuditLog.id))
                    .where(AuditLog.tenant_id == user.tenant_id)
                    .where(AuditLog.creator_id == user.sub)
                    .where(AuditLog.event_type == "copilot.chat")
                    .where(AuditLog.created_at >= today_start)
                )
            ).scalar()
            or 0
        )

    # Cache-first Gmail counts — read the same warm cache the Inbox/Dashboard
    # use so this agrees with them (the live connector is empty when the Google
    # token is expired or in demo mode).
    from app.services.demo_seed import demo_mode_enabled, is_demo_user
    from app.services.harvester_worker import cache_is_fresh, cached_messages

    demo = demo_mode_enabled() or is_demo_user(user.sub)
    gmail_fresh = await cache_is_fresh(user.sub, user.tenant_id, "gmail", "messages")
    if gmail_fresh or demo:
        msgs = await cached_messages(user.sub, user.tenant_id, "gmail")
        gmail_unread = sum(1 for m in msgs if m.get("unread"))
        gmail_drafts = 0
    else:
        gmail_c = await gmail_counts(user.sub, user.tenant_id)
        gmail_unread = gmail_c.get("unread") if gmail_c.get("available") else None
        gmail_drafts = gmail_c.get("drafts") if gmail_c.get("available") else None

    # Rough time-saved estimate: 30 sec / agent action
    time_saved_min = round(actions_today * 0.5)

    return {
        "actions_today": actions_today,
        "actions_week": actions_week,
        "chat_messages_today": chat_count,
        "time_saved_minutes": time_saved_min,
        "gmail_unread": gmail_unread,
        "gmail_drafts": gmail_drafts,
        "generated_at": now.isoformat(),
    }


@router.get("/vision", response_model=Dict[str, Any])
async def insights_vision(
    request: Request,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    return vision_status()
