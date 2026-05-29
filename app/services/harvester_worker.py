"""Background harvester worker.

Periodically crawls every connected user's Gmail/Outlook/Calendar and writes
the results into the harvested_* tables. The /api/inbox and /api/dashboard
endpoints prefer the cache; if the cache is stale or empty they fall back to
a live fetch so the UI is never blank.

Started in the FastAPI ``lifespan`` hook with a single asyncio task.

Tuning knobs (env):
    HARVESTER_ENABLED=true|false   (default true)
    HARVESTER_INTERVAL_SECONDS=300 (default 5 minutes)
    HARVESTER_STALE_SECONDS=600    (cache considered fresh under this age)
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select

from app.agent.connector_tools import (
    calendar_events_structured,
    gmail_messages_structured,
)
from app.agent.outlook_tools import (
    outlook_calendar_events,
    outlook_messages_structured,
)
from app.api.inbox import _classify_email_priority
from app.services.connector_manager import get_connector_manager
from app.storage.models import HarvestedEvent, HarvestedMessage, HarvestState
from app.tenants.registry import get_tenant_registry
from app.tenants.router import get_tenant_router

logger = logging.getLogger(__name__)


def _enabled() -> bool:
    return os.environ.get("HARVESTER_ENABLED", "true").strip().lower() != "false"


def _interval() -> int:
    try:
        return max(60, int(os.environ.get("HARVESTER_INTERVAL_SECONDS", "300")))
    except Exception:
        return 300


def stale_seconds() -> int:
    try:
        return max(60, int(os.environ.get("HARVESTER_STALE_SECONDS", "600")))
    except Exception:
        return 600


# ---------------------------------------------------------------------------
# Per-user crawl
# ---------------------------------------------------------------------------


async def _crawl_user_messages(
    *, tenant_id: str, user_id: str, account: str, items: list[dict]
) -> int:
    """Replace cached messages for this (user, account) with `items`."""
    router_db = get_tenant_router()
    async with router_db.session_for(tenant_id) as session:
        await session.execute(
            delete(HarvestedMessage)
            .where(HarvestedMessage.tenant_id == tenant_id)
            .where(HarvestedMessage.creator_id == user_id)
            .where(HarvestedMessage.account == account)
        )
        for m in items:
            prio = _classify_email_priority(
                m.get("subject", ""),
                m.get("snippet", ""),
                m.get("from", ""),
                m.get("from_name", ""),
                label_ids=m.get("label_ids") or [],
            )
            session.add(
                HarvestedMessage(
                    tenant_id=tenant_id,
                    creator_id=user_id,
                    account=account,
                    external_id=m["id"],
                    thread_id=m.get("thread_id"),
                    subject=(m.get("subject") or "")[:1024],
                    from_addr=(m.get("from") or "")[:512],
                    from_name=(m.get("from_name") or "")[:256],
                    date=(m.get("date") or "")[:64],
                    snippet=(m.get("snippet") or "")[:2000],
                    label_ids=m.get("label_ids") or [],
                    priority=prio,
                    unread=1 if m.get("unread") else 0,
                )
            )
        await session.commit()
    return len(items)


async def _crawl_user_events(
    *, tenant_id: str, user_id: str, account: str, items: list[dict]
) -> int:
    router_db = get_tenant_router()
    async with router_db.session_for(tenant_id) as session:
        await session.execute(
            delete(HarvestedEvent)
            .where(HarvestedEvent.tenant_id == tenant_id)
            .where(HarvestedEvent.creator_id == user_id)
            .where(HarvestedEvent.account == account)
        )
        for ev in items:
            session.add(
                HarvestedEvent(
                    tenant_id=tenant_id,
                    creator_id=user_id,
                    account=account,
                    external_id=ev["id"],
                    title=(ev.get("title") or "")[:512],
                    start=(ev.get("start") or "")[:64],
                    end=(ev.get("end") or "")[:64],
                    location=(ev.get("location") or "")[:512],
                    html_link=(ev.get("html_link") or "")[:1024],
                    attendee_count=int(ev.get("attendee_count", 0) or 0),
                    all_day=1 if ev.get("all_day") else 0,
                )
            )
        await session.commit()
    return len(items)


async def _record_state(
    *, tenant_id: str, user_id: str, account: str, kind: str,
    items_count: int, error: str | None,
) -> None:
    router_db = get_tenant_router()
    async with router_db.session_for(tenant_id) as session:
        existing = (
            await session.execute(
                select(HarvestState)
                .where(HarvestState.tenant_id == tenant_id)
                .where(HarvestState.creator_id == user_id)
                .where(HarvestState.account == account)
                .where(HarvestState.kind == kind)
            )
        ).scalars().first()
        now = datetime.now(timezone.utc)
        if existing:
            existing.last_run_at = now
            existing.items_count = items_count
            existing.last_error = error
        else:
            session.add(
                HarvestState(
                    tenant_id=tenant_id,
                    creator_id=user_id,
                    account=account,
                    kind=kind,
                    last_run_at=now,
                    items_count=items_count,
                    last_error=error,
                )
            )
        await session.commit()


async def _crawl_one(user_id: str, tenant_id: str) -> dict[str, Any]:
    """Crawl all connected accounts for one user."""
    cm = get_connector_manager()
    out: dict[str, Any] = {"user_id": user_id, "tenant_id": tenant_id, "accounts": {}}

    # Gmail messages
    try:
        gmail_msgs = await gmail_messages_structured(
            user_id, "is:unread in:inbox", limit=25, tenant_id=tenant_id
        )
        count = await _crawl_user_messages(
            tenant_id=tenant_id, user_id=user_id, account="gmail", items=gmail_msgs
        )
        await _record_state(
            tenant_id=tenant_id, user_id=user_id, account="gmail",
            kind="messages", items_count=count, error=None,
        )
        out["accounts"]["gmail_messages"] = count
    except Exception as exc:
        logger.warning("gmail crawl failed for %s: %s", user_id, exc)
        await _record_state(
            tenant_id=tenant_id, user_id=user_id, account="gmail",
            kind="messages", items_count=0, error=str(exc)[:200],
        )

    # Google calendar
    try:
        cal_events = await calendar_events_structured(user_id, days_ahead=7, tenant_id=tenant_id)
        count = await _crawl_user_events(
            tenant_id=tenant_id, user_id=user_id, account="google_calendar", items=cal_events
        )
        await _record_state(
            tenant_id=tenant_id, user_id=user_id, account="google_calendar",
            kind="events", items_count=count, error=None,
        )
        out["accounts"]["calendar_events"] = count
    except Exception as exc:
        logger.warning("calendar crawl failed for %s: %s", user_id, exc)
        await _record_state(
            tenant_id=tenant_id, user_id=user_id, account="google_calendar",
            kind="events", items_count=0, error=str(exc)[:200],
        )

    # Outlook messages
    try:
        out_msgs = await outlook_messages_structured(
            user_id, query="", limit=25, unread_only=True, tenant_id=tenant_id
        )
        count = await _crawl_user_messages(
            tenant_id=tenant_id, user_id=user_id, account="outlook", items=out_msgs
        )
        await _record_state(
            tenant_id=tenant_id, user_id=user_id, account="outlook",
            kind="messages", items_count=count, error=None,
        )
        out["accounts"]["outlook_messages"] = count
    except Exception as exc:
        logger.warning("outlook crawl failed for %s: %s", user_id, exc)

    # Outlook calendar
    try:
        out_events = await outlook_calendar_events(user_id, days_ahead=7, tenant_id=tenant_id)
        count = await _crawl_user_events(
            tenant_id=tenant_id, user_id=user_id, account="outlook_calendar", items=out_events
        )
        out["accounts"]["outlook_events"] = count
    except Exception as exc:
        logger.warning("outlook calendar crawl failed for %s: %s", user_id, exc)

    # Unused but might help upstream debugging
    _ = cm
    return out


# ---------------------------------------------------------------------------
# Discover users to crawl
# ---------------------------------------------------------------------------


async def _connected_users() -> list[tuple[str, str]]:
    """Return all (user_id, tenant_id) that have at least one OAuth connection."""
    cm = get_connector_manager()
    seen: set[tuple[str, str]] = set()
    # Read directly from connector DB
    conn = await cm._conn()
    try:
        cur = await conn.execute(
            "SELECT DISTINCT user_id, tenant_id FROM user_connections"
        )
        rows = await cur.fetchall()
        for r in rows:
            seen.add((r["user_id"], r["tenant_id"]))
    finally:
        await conn.close()
    return list(seen)


# ---------------------------------------------------------------------------
# Long-running loop
# ---------------------------------------------------------------------------


async def harvester_loop() -> None:
    """Main loop. Crawls every connected user every HARVESTER_INTERVAL_SECONDS."""
    if not _enabled():
        logger.info("Harvester disabled (HARVESTER_ENABLED=false)")
        return

    logger.info(
        "Harvester started — interval=%ds, stale_threshold=%ds",
        _interval(), stale_seconds(),
    )

    # Wait a few seconds after boot so the first request to the app doesn't
    # race with the crawler initialising the DB schema.
    await asyncio.sleep(5)

    while True:
        try:
            users = await _connected_users()
            if not users:
                logger.debug("Harvester: no connected users — sleeping")
            for user_id, tenant_id in users:
                # Guard each user with try/except so one bad user can't kill
                # the whole loop.
                try:
                    result = await _crawl_one(user_id, tenant_id)
                    logger.info("Harvested %s/%s -> %s", tenant_id, user_id, result["accounts"])
                except Exception as exc:
                    logger.exception("Crawl failed for %s/%s: %s", tenant_id, user_id, exc)
        except Exception as exc:
            logger.exception("Harvester tick failed: %s", exc)

        await asyncio.sleep(_interval())


# ---------------------------------------------------------------------------
# Cache readers used by inbox / dashboard
# ---------------------------------------------------------------------------


async def cached_messages(
    user_id: str, tenant_id: str, account: str | None = None
) -> list[dict]:
    """Return cached unread messages for a user, oldest-first per account."""
    router_db = get_tenant_router()
    async with router_db.session_for(tenant_id) as session:
        stmt = (
            select(HarvestedMessage)
            .where(HarvestedMessage.tenant_id == tenant_id)
            .where(HarvestedMessage.creator_id == user_id)
            .order_by(HarvestedMessage.date.desc())
        )
        if account:
            stmt = stmt.where(HarvestedMessage.account == account)
        rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": r.external_id,
            "thread_id": r.thread_id,
            "subject": r.subject,
            "from": r.from_addr,
            "from_name": r.from_name,
            "date": r.date,
            "snippet": r.snippet,
            "label_ids": r.label_ids or [],
            "unread": bool(r.unread),
            "priority": r.priority,
            "account": r.account,
        }
        for r in rows
    ]


async def cached_events(
    user_id: str, tenant_id: str, account: str | None = None
) -> list[dict]:
    router_db = get_tenant_router()
    async with router_db.session_for(tenant_id) as session:
        stmt = (
            select(HarvestedEvent)
            .where(HarvestedEvent.tenant_id == tenant_id)
            .where(HarvestedEvent.creator_id == user_id)
            .order_by(HarvestedEvent.start.asc())
        )
        if account:
            stmt = stmt.where(HarvestedEvent.account == account)
        rows = (await session.execute(stmt)).scalars().all()
    return [
        {
            "id": r.external_id,
            "title": r.title,
            "start": r.start,
            "end": r.end,
            "location": r.location,
            "html_link": r.html_link,
            "attendee_count": r.attendee_count,
            "all_day": bool(r.all_day),
            "account": r.account,
        }
        for r in rows
    ]


async def cache_is_fresh(
    user_id: str, tenant_id: str, account: str, kind: str
) -> bool:
    router_db = get_tenant_router()
    async with router_db.session_for(tenant_id) as session:
        row = (
            await session.execute(
                select(HarvestState)
                .where(HarvestState.tenant_id == tenant_id)
                .where(HarvestState.creator_id == user_id)
                .where(HarvestState.account == account)
                .where(HarvestState.kind == kind)
            )
        ).scalars().first()
    if not row or not row.last_run_at:
        return False
    age = (datetime.now(timezone.utc) - row.last_run_at.replace(tzinfo=timezone.utc)).total_seconds()
    return age < stale_seconds()


async def force_refresh_for_user(user_id: str, tenant_id: str) -> dict:
    """Trigger an immediate crawl for one user (used by /api/inbox?refresh=1)."""
    return await _crawl_one(user_id, tenant_id)
