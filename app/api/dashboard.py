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
    # Personal productivity KPIs — for THIS employee's own day.
    # Real numbers will come from connectors (Gmail/Calendar/Drive/Slack)
    # plus the user's own task & goal records, all scoped to user_id.
    kpis = [
        {"label": "UNREAD EMAILS", "value": "12"},
        {"label": "NEEDS REPLY", "value": "4", "tone": "warn"},
        {"label": "MEETINGS TODAY", "value": "5"},
        {"label": "FOCUS HOURS LEFT", "value": "3.2h"},
        {"label": "OPEN TASKS", "value": "8"},
        {"label": "DUE THIS WEEK", "value": "3"},
        {"label": "DRAFTS WAITING", "value": "2", "tone": "warn"},
        {"label": "FILES TO REVIEW", "value": "1"},
        {"label": "ASSISTANT ACTIONS", "value": "14"},
        {"label": "TIME SAVED", "value": "1h 45m"},
    ]

    # Sub-stats for the "Today's Focus" card (re-uses the retention slot
    # the frontend already renders; semantic mapping is the rename).
    focus = {
        "active": 3,           # Active threads
        "wonWeek": 8,          # Items resolved this week
        "lostWeek": 0,         # Items that slipped
        "saveRate": "94%",     # On-time response rate
        "avgDiscount": "1.4h", # Avg response time
        "avgLevels": "5",      # Avg actions per task
        "competitors": "—",
        "escalations": 1,      # Things waiting on you
    }

    threads = [
        {
            "id": "TH-1001",
            "name": "Priti Padhy",
            "level": 3,
            "competitor": "Sprint review",
            "status": "NEEDS REPLY",
            "product": "Quick summary of MyAi sprint outcomes",
            "fee": "2 hrs ago",
            "tenure": "high priority",
            "progress": 60,
            "confidence": 88,
            "incentives": ["Draft ready", "3 bullet summary", "Suggested follow-ups"],
            "thinking": "Priti asked for sprint outcomes by EOD. I drafted a 3-bullet summary from your standups and PRs covering Life-Harness, the welcome flow, and Docker support. Suggest you skim and send.",
            "recommended_action": "Approve draft + send",
            "conversation": [
                {"role": "customer", "name": "Priti Padhy", "text": "Can you send me a quick summary of the MyAi sprint outcomes before EOD?"},
                {"role": "ai", "text": "Drafted a 3-bullet summary from your standups: 1) Life-Harness integration (60→100% tool accuracy), 2) Welcome flow + Docker for new users, 3) Tasks page with action buttons. Want me to send?"},
            ],
        },
        {
            "id": "TH-1002",
            "name": "Calendar conflict",
            "level": 2,
            "competitor": "Tomorrow 2pm",
            "status": "WAITING ON YOU",
            "product": "Architecture review vs 1:1 with Sarah",
            "fee": "tomorrow",
            "tenure": "scheduling",
            "progress": 50,
            "confidence": 92,
            "incentives": ["Move 1:1 to Wed 3pm", "Skip architecture review", "Send delegate"],
            "thinking": "The architecture review is recurring and well-attended; your 1:1 with Sarah is monthly and you usually do strategic planning there. Sarah's calendar is free Wed 3pm — suggest moving.",
            "recommended_action": "Move 1:1 with Sarah to Wed 3pm",
            "conversation": [],
        },
    ]

    return {
        "scope": {"tenant_id": user.tenant_id, "user_id": user.sub},
        "kpis": kpis,
        "retention": focus,        # template key kept; content is now "Today's Focus"
        "negotiations": threads,   # template key kept; content is now "Active threads"
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
