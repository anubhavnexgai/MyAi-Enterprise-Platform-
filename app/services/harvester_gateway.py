"""Harvester gateway - the ONLY way the rest of the app reads harvested data.

Hard rule: every query is scoped by ``tenant_id`` + ``creator_id``. There is
no escape hatch.

For local development we don't actually run the harvester pipeline; the
gateway returns synthetic but tenant/user-stamped rows so the dashboard and
inbox can render. Production callers swap the implementation by setting
``HARVESTER_BACKEND=v2`` (TODO) and pointing at the real harvester DB.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any, Dict, List

from sqlalchemy import select

from app.storage.models import AuditLog, InboxTask
from app.tenants.router import get_tenant_router

logger = logging.getLogger(__name__)


# Supported query_type strings; documented here so callers don't typo
SUPPORTED_QUERY_TYPES = {
    "dashboard_kpis",
    "retention_at_risk",
    "inbox_tasks",
    "agent_activity",
    "audit_recent",
}


class HarvesterGateway:
    """Per-user-scoped read interface over harvested data + local stores."""

    async def get_user_data(
        self,
        *,
        user_id: str,
        tenant_id: str,
        query_type: str,
        **filters: Any,
    ) -> Dict[str, Any]:
        """The single entry point.

        Returns ``{"query_type": ..., "tenant_id": ..., "user_id": ..., "data": ...}``
        Always scoped to (tenant_id, creator_id=user_id).
        """
        if not user_id:
            raise ValueError("harvester_gateway: user_id is required")
        if not tenant_id:
            raise ValueError("harvester_gateway: tenant_id is required")
        if query_type not in SUPPORTED_QUERY_TYPES:
            raise ValueError(
                f"harvester_gateway: unknown query_type {query_type!r}; "
                f"supported: {sorted(SUPPORTED_QUERY_TYPES)}"
            )

        logger.debug(
            "harvester_gateway query type=%s tenant=%s user=%s filters=%s",
            query_type, tenant_id, user_id, filters,
        )

        if query_type == "dashboard_kpis":
            data = self._stub_dashboard_kpis(user_id=user_id, tenant_id=tenant_id)
        elif query_type == "retention_at_risk":
            data = self._stub_retention(user_id=user_id, tenant_id=tenant_id)
        elif query_type == "inbox_tasks":
            data = await self._read_inbox(user_id=user_id, tenant_id=tenant_id, **filters)
        elif query_type == "agent_activity":
            data = self._stub_agent_activity(user_id=user_id, tenant_id=tenant_id)
        elif query_type == "audit_recent":
            data = await self._read_audit(user_id=user_id, tenant_id=tenant_id, **filters)
        else:  # pragma: no cover - guarded above
            data = {}

        return {
            "query_type": query_type,
            "tenant_id": tenant_id,
            "user_id": user_id,
            "data": data,
            "generated_at": datetime.utcnow().isoformat(),
        }

    # ---- local-store reads (always scoped) ----------------------------------

    async def _read_inbox(
        self, *, user_id: str, tenant_id: str, status: str | None = None, limit: int = 50
    ) -> List[Dict[str, Any]]:
        router = get_tenant_router()
        async with router.session_for(tenant_id) as session:
            stmt = (
                select(InboxTask)
                .where(InboxTask.tenant_id == tenant_id)
                .where(InboxTask.creator_id == user_id)
                .order_by(InboxTask.created_at.desc())
                .limit(limit)
            )
            if status:
                stmt = stmt.where(InboxTask.status == status)
            result = await session.execute(stmt)
            rows = result.scalars().all()
        return [_inbox_to_dict(r) for r in rows]

    async def _read_audit(
        self, *, user_id: str, tenant_id: str, limit: int = 100
    ) -> List[Dict[str, Any]]:
        router = get_tenant_router()
        async with router.session_for(tenant_id) as session:
            stmt = (
                select(AuditLog)
                .where(AuditLog.tenant_id == tenant_id)
                .where(AuditLog.creator_id == user_id)
                .order_by(AuditLog.created_at.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            rows = result.scalars().all()
        return [_audit_to_dict(r) for r in rows]

    # ---- harvester stubs ----------------------------------------------------
    # In local dev we have no harvester DB. These return deterministic synthetic
    # rows stamped with the caller's tenant + user so multi-tenant isolation is
    # observable end-to-end.

    def _stub_dashboard_kpis(self, *, user_id: str, tenant_id: str) -> Dict[str, Any]:
        return {
            "kpis": [
                {"label": "Open tasks", "value": 12, "delta": -3, "trend": "down"},
                {"label": "At-risk customers", "value": 4, "delta": 1, "trend": "up"},
                {"label": "Pending follow-ups", "value": 7, "delta": 0, "trend": "flat"},
                {"label": "Avg first response", "value": "1h 42m", "trend": "down"},
            ],
            "scope": {"tenant_id": tenant_id, "user_id": user_id},
        }

    def _stub_retention(self, *, user_id: str, tenant_id: str) -> List[Dict[str, Any]]:
        now = datetime.utcnow()
        return [
            {
                "customer_id": f"{tenant_id}_C001",
                "name": "Acme Corp",
                "risk_score": 0.82,
                "last_contact": (now - timedelta(days=14)).isoformat(),
                "reason": "Two missed follow-ups + renewal due in 30 days",
                "recommended_action": "Schedule QBR call this week",
            },
            {
                "customer_id": f"{tenant_id}_C002",
                "name": "Globex",
                "risk_score": 0.67,
                "last_contact": (now - timedelta(days=9)).isoformat(),
                "reason": "Sentiment dipped in last 3 emails",
                "recommended_action": "Send personalised check-in",
            },
        ]

    def _stub_agent_activity(self, *, user_id: str, tenant_id: str) -> Dict[str, Any]:
        return {
            "by_status": {"open": 12, "in_progress": 5, "blocked": 2, "done": 41},
            "by_source": {"agent": 28, "rule": 14, "manual": 18},
            "scope": {"tenant_id": tenant_id, "user_id": user_id},
        }


def _inbox_to_dict(row: InboxTask) -> Dict[str, Any]:
    return {
        "id": row.id,
        "title": row.title,
        "summary": row.summary,
        "source": row.source,
        "priority": row.priority,
        "status": row.status,
        "payload": row.payload,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _audit_to_dict(row: AuditLog) -> Dict[str, Any]:
    return {
        "id": row.id,
        "event_type": row.event_type,
        "severity": row.severity,
        "message": row.message,
        "payload": row.payload,
        "creator_id": row.creator_id,
        "tenant_id": row.tenant_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


@lru_cache
def get_harvester_gateway() -> HarvesterGateway:
    return HarvesterGateway()
