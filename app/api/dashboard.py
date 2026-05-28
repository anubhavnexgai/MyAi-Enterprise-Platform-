"""Dashboard endpoints - KPIs and retention center."""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Request

from app.auth.middleware import get_current_user
from app.auth.jwt import PlatformTokenClaims
from app.services.harvester_gateway import get_harvester_gateway

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("", response_model=Dict[str, Any])
async def dashboard_summary(
    request: Request,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    """Aggregate endpoint shaped for the SPA dashboard:
       kpis: list of {label, value, foot, tone}
       retention: object with active/wonWeek/lostWeek/saveRate/...
       negotiations: list of customer cards with progress/incentives/conversation
    """
    # KPIs for the top tile row — 10 tiles as in the inspiration screens.
    # When real harvester data is wired in, swap these constants for queries
    # scoped to (tenant_id=user.tenant_id, creator_id=user.sub).
    kpis = [
        {"label": "REQUESTS TODAY", "value": "20"},
        {"label": "AVG RESOLUTION", "value": "16.5m"},
        {"label": "SLA COMPLIANCE", "value": "94%"},
        {"label": "AI AUTO-RESOLVE", "value": "55%"},
        {"label": "PENDING REVIEW", "value": "9", "tone": "warn"},
        {"label": "ESCALATED TODAY", "value": "4"},
        {"label": "FRAUD ALERTS TODAY", "value": "4", "tone": "alert"},
        {"label": "PROACTIVE ALERTS", "value": "0"},
        {"label": "CHURN RISK CUSTOMERS", "value": "0"},
        {"label": "CUSTOMERS SAVED", "value": "0"},
    ]

    retention = {
        "active": 3, "wonWeek": 11, "lostWeek": 2,
        "saveRate": "84%", "avgDiscount": "10%", "avgLevels": "3.4",
        "competitors": 3, "escalations": 1,
    }

    negotiations = [
        {
            "id": "OB-RET-1001",
            "name": "James Whitfield", "level": 5, "competitor": "MONZO",
            "status": "NEEDS APPROVAL",
            "product": "Premier Current Account", "fee": "£25/mo", "tenure": "54 months",
            "progress": 84, "confidence": 62,
            "incentives": ["20% discount", "12-month fee waiver", "GBP 100 retention credit", "1.5% cashback boost"],
            "thinking": "Customer cites the Monzo switch bonus and zero fee. Value framing alone will not hold; recommend the fee waiver plus matched bonus, and escalate as the ask exceeds my discount authority.",
            "recommended_action": "Approve 12-month fee waiver + GBP 150 matched switch bonus",
            "conversation": [
                {"role": "customer", "name": "James Whitfield", "text": "I have been with you years but Monzo is offering no fee and 150 pounds to switch."},
                {"role": "ai", "text": "I hear you, and I appreciate your loyalty over the past four and a half years. Let me see what I can do to keep your banking with us."},
                {"role": "customer", "name": "James Whitfield", "text": "It would need to beat what they are offering."},
                {"role": "ai", "text": "I can waive your monthly fee for 12 months and add a retention credit — let me confirm the bonus match with a manager."},
            ],
        },
        {
            "id": "OB-RET-1002",
            "name": "Emma Richardson", "level": 4, "competitor": "Starling",
            "status": "AT RISK",
            "product": "Platinum Savings", "fee": "£0/mo", "tenure": "28 months",
            "progress": 62, "confidence": 81,
            "incentives": ["+0.25% APY boost", "Skip 1 month fee", "Free overdraft buffer"],
            "thinking": "Rate-driven churn signal. Starling 4.4% beats our 4.1%. Try APY boost + frame multi-product loyalty bonuses.",
            "recommended_action": "Offer +0.25% APY for 6 months + cross-sell loyalty program",
            "conversation": [],
        },
    ]

    return {
        "scope": {"tenant_id": user.tenant_id, "user_id": user.sub},
        "kpis": kpis,
        "retention": retention,
        "negotiations": negotiations,
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


@router.get("/retention", response_model=Dict[str, Any])
async def dashboard_retention(
    request: Request,
    user: PlatformTokenClaims = Depends(get_current_user),
) -> Dict[str, Any]:
    gateway = get_harvester_gateway()
    return await gateway.get_user_data(
        user_id=user.sub,
        tenant_id=user.tenant_id,
        query_type="retention_at_risk",
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
