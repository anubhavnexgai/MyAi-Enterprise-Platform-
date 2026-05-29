"""Service request lifecycle ticker.

Runs in the background and:
1. Computes ``due_at`` for tasks that have a ``sla_minutes`` but no explicit due.
2. Detects SLA breaches: tasks past their ``due_at`` that are still open or
   in_progress get an audit ``inbox.task_sla_breach`` event and their
   ``escalated_at`` / ``escalation_count`` fields are bumped.
3. (Future hook) auto-starts queued tasks when autonomy >= L4.

Tuning (env):
    LIFECYCLE_ENABLED=true|false      (default true)
    LIFECYCLE_INTERVAL_SECONDS=60     (default 1 minute)
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.services.audit import get_audit_service
from app.storage.models import InboxTask
from app.tenants.router import get_tenant_router

logger = logging.getLogger(__name__)


# Default SLAs by priority (in minutes). Used when a task is created with
# no explicit due_at / sla_minutes.
DEFAULT_SLA_BY_PRIORITY = {
    "critical": 30,            # half-hour
    "high": 4 * 60,            # 4 hours
    "medium": 24 * 60,         # 1 day
    "normal": 24 * 60,
    "low": 7 * 24 * 60,        # 1 week
}


def _enabled() -> bool:
    return os.environ.get("LIFECYCLE_ENABLED", "true").strip().lower() != "false"


def _interval() -> int:
    try:
        return max(15, int(os.environ.get("LIFECYCLE_INTERVAL_SECONDS", "60")))
    except Exception:
        return 60


def default_due_at(priority: str, created_at: datetime | None = None) -> datetime:
    """Compute a sane default due date based on priority."""
    mins = DEFAULT_SLA_BY_PRIORITY.get((priority or "medium").lower(), 24 * 60)
    base = (created_at or datetime.now(timezone.utc))
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    return base + timedelta(minutes=mins)


# ---------------------------------------------------------------------------
# Tick
# ---------------------------------------------------------------------------


async def _tick_for_tenant(tenant_id: str) -> int:
    """One sweep over a tenant's open tasks. Returns # of breaches raised."""
    router_db = get_tenant_router()
    now = datetime.now(timezone.utc)
    breaches = 0

    async with router_db.session_for(tenant_id) as session:
        open_q = await session.execute(
            select(InboxTask)
            .where(InboxTask.tenant_id == tenant_id)
            .where(InboxTask.status.in_(["open", "in_progress", "blocked"]))
        )
        tasks = open_q.scalars().all()

        for t in tasks:
            # If no due_at but we have sla_minutes (or can infer), fill it in.
            if t.due_at is None:
                if t.sla_minutes:
                    base = t.created_at or now
                    if base.tzinfo is None:
                        base = base.replace(tzinfo=timezone.utc)
                    t.due_at = base + timedelta(minutes=t.sla_minutes)
                else:
                    # Use default-by-priority and persist it on first sight so
                    # the UI can show a real countdown.
                    t.due_at = default_due_at(t.priority, t.created_at)
                    t.sla_minutes = int((t.due_at - (t.created_at or now)).total_seconds() / 60)

            due = t.due_at
            if due.tzinfo is None:
                due = due.replace(tzinfo=timezone.utc)

            # SLA breach?
            if due < now:
                # Only raise a fresh breach if we haven't recently flagged it
                # or if it's been more than 1 hour since the last escalation.
                last = t.escalated_at
                if last and last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                fresh = (last is None) or ((now - last).total_seconds() > 3600)
                if fresh:
                    t.escalated_at = now
                    t.escalation_count = int(t.escalation_count or 0) + 1
                    breaches += 1
                    # Write audit event (committed below with task changes)
                    await get_audit_service().log(
                        tenant_id=tenant_id,
                        user_id=t.creator_id,
                        event_type="inbox.task_sla_breach",
                        severity="warning",
                        message=f"SLA breach on task #{t.id}: '{t.title[:80]}' overdue by "
                                f"{int((now - due).total_seconds() / 60)} min",
                        payload={
                            "task_id": t.id,
                            "priority": t.priority,
                            "status": t.status,
                            "due_at": due.isoformat(),
                            "escalation_count": t.escalation_count,
                        },
                    )

        await session.commit()
    return breaches


async def lifecycle_loop() -> None:
    """Background loop. Sweeps every tenant for SLA breaches."""
    if not _enabled():
        logger.info("Lifecycle ticker disabled (LIFECYCLE_ENABLED=false)")
        return

    logger.info("Lifecycle ticker started — interval=%ds", _interval())
    await asyncio.sleep(3)

    while True:
        try:
            # We currently have a single-tenant deployment but the model
            # supports many. Iterate over each.
            from app.tenants.registry import get_tenant_registry

            tenants = [t.tenant_id for t in get_tenant_registry().all()] or [
                os.environ.get("DEV_TENANT_ID", "nexgai")
            ]
            for tenant_id in tenants:
                try:
                    breaches = await _tick_for_tenant(tenant_id)
                    if breaches:
                        logger.warning("Lifecycle tick: %d breach(es) on %s", breaches, tenant_id)
                except Exception as exc:
                    logger.exception("Lifecycle tick failed for %s: %s", tenant_id, exc)
        except Exception as exc:
            logger.exception("Lifecycle outer tick failed: %s", exc)

        await asyncio.sleep(_interval())
