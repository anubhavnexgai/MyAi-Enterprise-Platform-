"""Scheduled recurring deep-research + new-development alerts.

A background loop re-runs each active ScheduledResearch on its interval, diffs the
fresh findings against the previous run, and raises an alert (a high-priority
InboxTask, which surfaces on the Dashboard) ONLY when something genuinely new
shows up. This is how "research X every day and tell me when there's a new
development" works. Fail-soft throughout — a bad run never kills the loop.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select

from app.storage.models import InboxTask, ScheduledResearch
from app.tenants.router import get_tenant_router

logger = logging.getLogger(__name__)

_TICK_SECONDS = 120  # how often the loop checks for due watches
_MIN_INTERVAL_HOURS = 1


def _enabled() -> bool:
    return os.environ.get("RESEARCH_SCHEDULER_ENABLED", "true").strip().lower() != "false"


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def create_schedule(user_id: str, tenant_id: str, topic: str,
                          interval_hours: int = 24) -> Optional[int]:
    """Create (or update) a recurring research watch. Returns its id."""
    topic = (topic or "").strip()
    if not topic:
        return None
    interval_hours = max(_MIN_INTERVAL_HOURS, int(interval_hours or 24))
    router_db = get_tenant_router()
    async with router_db.session_for(tenant_id) as s:
        # De-dupe: same active topic → just update the interval.
        existing = (await s.execute(
            select(ScheduledResearch)
            .where(ScheduledResearch.tenant_id == tenant_id)
            .where(ScheduledResearch.creator_id == user_id)
            .where(ScheduledResearch.topic == topic)
            .where(ScheduledResearch.active == 1)
        )).scalars().first()
        if existing:
            existing.interval_hours = interval_hours
            await s.commit()
            return existing.id
        row = ScheduledResearch(
            tenant_id=tenant_id, creator_id=user_id, topic=topic,
            interval_hours=interval_hours, active=1,
            next_run_at=_now(),  # run the first baseline shortly
        )
        s.add(row)
        await s.commit()
        return row.id


async def list_schedules(user_id: str, tenant_id: str) -> list[dict]:
    router_db = get_tenant_router()
    async with router_db.session_for(tenant_id) as s:
        rows = (await s.execute(
            select(ScheduledResearch)
            .where(ScheduledResearch.tenant_id == tenant_id)
            .where(ScheduledResearch.creator_id == user_id)
            .where(ScheduledResearch.active == 1)
            .order_by(ScheduledResearch.created_at.desc())
        )).scalars().all()
    return [{
        "id": r.id, "topic": r.topic, "interval_hours": r.interval_hours,
        "last_run_at": r.last_run_at.isoformat() if r.last_run_at else None,
        "next_run_at": r.next_run_at.isoformat() if r.next_run_at else None,
        "run_count": r.run_count,
    } for r in rows]


async def cancel_schedule(user_id: str, tenant_id: str, sched_id: int) -> bool:
    router_db = get_tenant_router()
    async with router_db.session_for(tenant_id) as s:
        row = (await s.execute(
            select(ScheduledResearch).where(ScheduledResearch.id == sched_id)
            .where(ScheduledResearch.creator_id == user_id)
        )).scalars().first()
        if not row:
            return False
        row.active = 0
        await s.commit()
        return True


_DIFF_SYSTEM = (
    "You compare an EARLIER research summary with the LATEST findings on the same "
    "topic and report ONLY what is genuinely NEW or changed (new releases, new "
    "facts, updated numbers, new events). Be concise — a few bullet points. If "
    "there is nothing substantively new, reply with exactly: NONE"
)


async def _diff_developments(llm, topic: str, prev: str, latest: str) -> str:
    if not prev:
        return ""  # first run → baseline only, no alert
    try:
        r = await llm.chat([
            {"role": "system", "content": _DIFF_SYSTEM},
            {"role": "user", "content":
                f"Topic: {topic}\n\nEARLIER findings:\n{prev[:4000]}\n\n"
                f"LATEST findings:\n{latest[:4000]}\n\nWhat is NEW?"},
        ], temperature=0.2, max_tokens=400)
        out = (((r or {}).get("message") or {}).get("content") or "").strip()
        return "" if out.upper().startswith("NONE") else out
    except Exception as exc:  # noqa: BLE001
        logger.debug("diff failed: %s", exc)
        return ""


async def _raise_alert(sched: ScheduledResearch, whats_new: str) -> None:
    """Surface new developments as a high-priority InboxTask (shows on Dashboard)."""
    router_db = get_tenant_router()
    async with router_db.session_for(sched.tenant_id) as s:
        s.add(InboxTask(
            tenant_id=sched.tenant_id, creator_id=sched.creator_id,
            title=f"New on “{sched.topic[:80]}”",
            summary=whats_new[:1000],
            source="research_alert", priority="high", status="open",
            assignee_id="myai", started_at=_now(),
            payload={"schedule_id": sched.id, "topic": sched.topic},
        ))
        await s.commit()


async def _run_one(sched_id: int, tenant_id: str) -> None:
    from app.services.deep_research import run_deep_research
    from app.services.llm_client import get_llm_client

    router_db = get_tenant_router()
    async with router_db.session_for(tenant_id) as s:
        sched = (await s.execute(
            select(ScheduledResearch).where(ScheduledResearch.id == sched_id)
        )).scalars().first()
        if not sched or not sched.active:
            return
        topic, prev, run_count = sched.topic, sched.last_summary or "", sched.run_count

    result = await run_deep_research(topic, max_rounds=2, time_budget=120.0)
    report = result.report or ""
    whats_new = await _diff_developments(get_llm_client(), topic, prev, report)

    async with router_db.session_for(tenant_id) as s:
        sched = (await s.execute(
            select(ScheduledResearch).where(ScheduledResearch.id == sched_id)
        )).scalars().first()
        if not sched:
            return
        sched.last_summary = report
        sched.last_sources = [x.get("url") for x in (result.sources or [])]
        sched.last_run_at = _now()
        sched.next_run_at = _now() + timedelta(hours=max(_MIN_INTERVAL_HOURS, sched.interval_hours))
        sched.run_count = run_count + 1
        await s.commit()
        snap = sched

    if whats_new:
        await _raise_alert(snap, whats_new)
        logger.info("research watch %s: new developments on '%s'", sched_id, topic)
    else:
        logger.info("research watch %s: no new developments on '%s'", sched_id, topic)


async def research_scheduler_loop() -> None:
    """Background loop: run any watch whose next_run_at is due. Best-effort."""
    if not _enabled():
        logger.info("Research scheduler disabled")
        return
    logger.info("Research scheduler started — tick=%ss", _TICK_SECONDS)
    router_db = get_tenant_router()
    from app.tenants.registry import get_tenant_registry
    while True:
        try:
            for t in get_tenant_registry().all():
                async with router_db.session_for(t.tenant_id) as s:
                    due = (await s.execute(
                        select(ScheduledResearch.id)
                        .where(ScheduledResearch.tenant_id == t.tenant_id)
                        .where(ScheduledResearch.active == 1)
                        .where((ScheduledResearch.next_run_at == None)  # noqa: E711
                               | (ScheduledResearch.next_run_at <= _now()))
                        .limit(5)
                    )).scalars().all()
                for sid in due:
                    try:
                        await _run_one(sid, t.tenant_id)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("scheduled research %s failed: %s", sid, exc)
        except Exception as exc:  # noqa: BLE001
            logger.warning("research scheduler tick failed: %s", exc)
        await asyncio.sleep(_TICK_SECONDS)
