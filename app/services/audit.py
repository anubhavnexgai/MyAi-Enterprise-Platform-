"""Audit logging service.

Writes to the ``audit_log`` table AND mirrors to a per-day JSONL file under
``logs/`` so a parallel pipeline can tail it.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, date
from functools import lru_cache
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from sqlalchemy import select

from app.config import get_settings
from app.storage.models import AuditLog
from app.tenants.router import get_tenant_router

logger = logging.getLogger(__name__)


class AuditService:
    def __init__(self) -> None:
        self.log_dir = get_settings().logs_dir

    async def log(
        self,
        *,
        tenant_id: str,
        user_id: str,
        event_type: str,
        message: str,
        severity: str = "info",
        payload: Optional[Dict[str, Any]] = None,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> AuditLog:
        if not (tenant_id and user_id):
            raise ValueError("audit.log requires tenant_id and user_id")

        entry = AuditLog(
            tenant_id=tenant_id,
            creator_id=user_id,
            event_type=event_type,
            severity=severity,
            message=message,
            payload=payload,
            ip_address=ip_address,
            user_agent=user_agent,
        )

        router = get_tenant_router()
        async with router.session_for(tenant_id) as session:
            session.add(entry)
            await session.commit()
            await session.refresh(entry)

        self._append_jsonl(entry)
        return entry

    async def list_recent(
        self,
        *,
        tenant_id: str,
        user_id: str,
        limit: int = 100,
        event_type: Optional[str] = None,
    ) -> List[AuditLog]:
        router = get_tenant_router()
        async with router.session_for(tenant_id) as session:
            stmt = (
                select(AuditLog)
                .where(AuditLog.tenant_id == tenant_id)
                .where(AuditLog.creator_id == user_id)
                .order_by(AuditLog.created_at.desc())
                .limit(limit)
            )
            if event_type:
                stmt = stmt.where(AuditLog.event_type == event_type)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def stream(
        self,
        *,
        tenant_id: str,
        user_id: str,
        poll_interval_seconds: float = 1.0,
        max_iterations: int = 600,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Yield audit rows as they arrive (poll-based, good enough for v1).

        max_iterations bounds the stream so a runaway client can't keep a
        connection open forever; the SPA reconnects.
        """
        import asyncio

        last_id = 0
        # Seed last_id from current latest
        existing = await self.list_recent(tenant_id=tenant_id, user_id=user_id, limit=1)
        if existing:
            last_id = existing[0].id

        for _ in range(max_iterations):
            await asyncio.sleep(poll_interval_seconds)
            router = get_tenant_router()
            async with router.session_for(tenant_id) as session:
                stmt = (
                    select(AuditLog)
                    .where(AuditLog.tenant_id == tenant_id)
                    .where(AuditLog.creator_id == user_id)
                    .where(AuditLog.id > last_id)
                    .order_by(AuditLog.id.asc())
                    .limit(50)
                )
                result = await session.execute(stmt)
                rows = list(result.scalars().all())
            for r in rows:
                last_id = max(last_id, r.id)
                yield {
                    "id": r.id,
                    "event_type": r.event_type,
                    "severity": r.severity,
                    "message": r.message,
                    "payload": r.payload,
                    "tenant_id": r.tenant_id,
                    "creator_id": r.creator_id,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                }

    # ---- internals ----------------------------------------------------------

    def _append_jsonl(self, entry: AuditLog) -> None:
        try:
            day = (entry.created_at or datetime.utcnow()).date()
            path = self.log_dir / f"audit-{day.isoformat()}.jsonl"
            doc = {
                "id": entry.id,
                "ts": (entry.created_at or datetime.utcnow()).isoformat(),
                "tenant_id": entry.tenant_id,
                "creator_id": entry.creator_id,
                "event_type": entry.event_type,
                "severity": entry.severity,
                "message": entry.message,
                "payload": entry.payload,
            }
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(doc, default=str) + "\n")
        except Exception:
            logger.exception("Failed to append audit JSONL")


@lru_cache
def get_audit_service() -> AuditService:
    return AuditService()
