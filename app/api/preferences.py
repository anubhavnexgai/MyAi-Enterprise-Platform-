"""User preferences (autonomy level + UI bits)."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.auth.jwt import PlatformTokenClaims
from app.auth.middleware import get_current_user
from app.services.audit import get_audit_service
from app.storage.models import UserPreference
from app.tenants.router import get_tenant_router

router = APIRouter(prefix="/api/preferences", tags=["preferences"])


class PreferencesPayload(BaseModel):
    autonomy_level: int | None = Field(default=None, ge=1, le=5)
    data: dict | None = None


async def _get_or_create(session, tenant_id: str, user_id: str) -> UserPreference:
    result = await session.execute(
        select(UserPreference)
        .where(UserPreference.tenant_id == tenant_id)
        .where(UserPreference.creator_id == user_id)
    )
    pref = result.scalars().first()
    if not pref:
        pref = UserPreference(
            tenant_id=tenant_id,
            creator_id=user_id,
            autonomy_level=1,
            data={},
        )
        session.add(pref)
        await session.commit()
        await session.refresh(pref)
    return pref


@router.get("", response_model=Dict[str, Any])
async def get_preferences(
    request: Request,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    router_db = get_tenant_router()
    async with router_db.session_for(user.tenant_id) as session:
        pref = await _get_or_create(session, user.tenant_id, user.sub)
    return {
        "autonomy_level": pref.autonomy_level,
        "data": pref.data or {},
    }


@router.put("", response_model=Dict[str, Any])
async def update_preferences(
    payload: PreferencesPayload,
    request: Request,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    router_db = get_tenant_router()
    async with router_db.session_for(user.tenant_id) as session:
        pref = await _get_or_create(session, user.tenant_id, user.sub)
        changed = []
        if payload.autonomy_level is not None and payload.autonomy_level != pref.autonomy_level:
            old = pref.autonomy_level
            pref.autonomy_level = payload.autonomy_level
            changed.append(f"autonomy {old}→{payload.autonomy_level}")
        if payload.data is not None:
            merged = {**(pref.data or {}), **payload.data}
            pref.data = merged
            changed.append("data updated")
        await session.commit()
        await session.refresh(pref)

    if changed:
        await get_audit_service().log(
            tenant_id=user.tenant_id,
            user_id=user.sub,
            event_type="preferences.updated",
            message="; ".join(changed),
            payload={
                "autonomy_level": pref.autonomy_level,
                "data": pref.data,
            },
        )
    return {
        "autonomy_level": pref.autonomy_level,
        "data": pref.data or {},
    }


def autonomy_allows(level: int, action: str) -> tuple[bool, str]:
    """Decide whether the current autonomy level permits an action.

    Returns (allowed, reason). The frontend mirrors this so behaviour is
    consistent in both layers.

    L1 — Observe: no actions
    L2 — Draft Assist: drafts only (no send)
    L3 — Augmented: drafts + low-risk reads, action with approval
    L4 — Guarded Auto: auto for low-risk, approval for high-risk
    L5 — Autonomous: full auto with audit trail
    """
    low_risk = {"draft", "list", "search", "summarize", "read", "open"}
    medium_risk = {"approve", "tag", "snooze", "schedule"}
    high_risk = {"send", "delete", "cancel", "transfer", "pay"}

    if level <= 1:
        if action in low_risk:
            return True, "observe-only mode: read-only actions OK"
        return False, "L1 Observe — agent cannot take this action"
    if level == 2:
        if action in low_risk or action == "draft":
            return True, "L2 Draft Assist"
        return False, "L2 — needs your approval"
    if level == 3:
        if action in low_risk or action in medium_risk or action == "draft":
            return True, "L3 Augmented"
        if action in high_risk:
            return False, "L3 — high-risk action needs approval"
    if level == 4:
        if action in high_risk:
            return False, "L4 — high-risk action needs approval"
        return True, "L4 Guarded Auto"
    # L5
    return True, "L5 Autonomous"
