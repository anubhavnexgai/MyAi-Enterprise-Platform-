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
import hashlib
import logging
import os
import re
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
from app.storage.models import (
    ContactMemory,
    HarvestedEvent,
    HarvestedMessage,
    HarvestState,
    MessageEnrichment,
)
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

    # Demo mode: seed employee-centric data instead of crawling personal accounts.
    from app.services.demo_seed import demo_mode_enabled, seed_demo_data
    if demo_mode_enabled():
        seeded = await seed_demo_data(user_id, tenant_id)
        await _record_state(
            tenant_id=tenant_id, user_id=user_id, account="gmail",
            kind="messages", items_count=seeded["emails"], error=None,
        )
        await _record_state(
            tenant_id=tenant_id, user_id=user_id, account="google_calendar",
            kind="events", items_count=seeded["events"], error=None,
        )
        out["accounts"] = {"demo": seeded}
        _ = cm
        return out

    # Gmail messages
    try:
        gmail_msgs = await gmail_messages_structured(
            user_id, "in:inbox", limit=60, tenant_id=tenant_id
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
            user_id, query="", limit=60, unread_only=False, tenant_id=tenant_id
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

    # Background enrichment: pre-compute AI triage suggestions for harvested
    # mail so opening an email in the inbox is instant (the "wiki" pattern).
    try:
        out["accounts"]["enriched"] = await _enrich_messages(
            tenant_id=tenant_id, user_id=user_id
        )
    except Exception as exc:
        logger.warning("enrichment failed for %s: %s", user_id, exc)

    # Per-sender memory ('what's the latest with X?') built from harvested mail.
    try:
        out["accounts"]["contacts"] = await _build_contact_memory(
            tenant_id=tenant_id, user_id=user_id
        )
    except Exception as exc:
        logger.warning("contact memory failed for %s: %s", user_id, exc)

    # Unused but might help upstream debugging
    _ = cm
    return out


def _msg_hash(subject: str, snippet: str) -> str:
    return hashlib.sha1(f"{subject}|{snippet}".encode("utf-8")).hexdigest()[:16]


async def _enrich_messages(*, tenant_id: str, user_id: str, limit: int = 30) -> int:
    """Compute & cache AI triage suggestions for the user's harvested mail.

    Only (re)computes messages whose content hash changed since last time, so
    each tick is cheap after the first pass. Returns how many were computed.
    """
    from app.services.mail_ai import generate_mail_suggestion

    router_db = get_tenant_router()
    async with router_db.session_for(tenant_id) as session:
        rows = (
            await session.execute(
                select(HarvestedMessage)
                .where(HarvestedMessage.tenant_id == tenant_id)
                .where(HarvestedMessage.creator_id == user_id)
                .order_by(HarvestedMessage.date.desc())
                .limit(limit)
            )
        ).scalars().all()
        ext_ids = [r.external_id for r in rows]
        existing: dict[str, MessageEnrichment] = {}
        if ext_ids:
            ex = (
                await session.execute(
                    select(MessageEnrichment)
                    .where(MessageEnrichment.tenant_id == tenant_id)
                    .where(MessageEnrichment.creator_id == user_id)
                    .where(MessageEnrichment.external_id.in_(ext_ids))
                )
            ).scalars().all()
            existing = {e.external_id: e for e in ex}

    todo = []
    for r in rows:
        h = _msg_hash(r.subject or "", r.snippet or "")
        e = existing.get(r.external_id)
        if e and e.content_hash == h:
            continue
        todo.append((r, h))
    if not todo:
        return 0

    sem = asyncio.Semaphore(3)

    async def _one(r, h):
        async with sem:
            sug, act = await generate_mail_suggestion(
                r.subject or "", r.from_name or r.from_addr or "", r.snippet or ""
            )
            return (r.external_id, h, sug, act)

    results = await asyncio.gather(*[_one(r, h) for r, h in todo], return_exceptions=True)

    now = datetime.now(timezone.utc)
    async with router_db.session_for(tenant_id) as session:
        for res in results:
            if isinstance(res, Exception) or not res:
                continue
            ext, h, sug, act = res
            if not sug:
                continue
            row = (
                await session.execute(
                    select(MessageEnrichment)
                    .where(MessageEnrichment.tenant_id == tenant_id)
                    .where(MessageEnrichment.creator_id == user_id)
                    .where(MessageEnrichment.external_id == ext)
                )
            ).scalars().first()
            if row:
                row.content_hash, row.suggestion, row.action, row.computed_at = h, sug, act, now
            else:
                session.add(MessageEnrichment(
                    tenant_id=tenant_id, creator_id=user_id, external_id=ext,
                    content_hash=h, suggestion=sug, action=act, computed_at=now,
                ))
        await session.commit()
    return len(todo)


async def store_enrichment(
    user_id: str, tenant_id: str, external_id: str,
    subject: str, body: str, suggestion: str, action: str | None,
) -> None:
    """Upsert an on-demand-computed suggestion so it's instant next time."""
    h = _msg_hash(subject or "", (body or "")[:2000])
    now = datetime.now(timezone.utc)
    router_db = get_tenant_router()
    async with router_db.session_for(tenant_id) as session:
        row = (
            await session.execute(
                select(MessageEnrichment)
                .where(MessageEnrichment.tenant_id == tenant_id)
                .where(MessageEnrichment.creator_id == user_id)
                .where(MessageEnrichment.external_id == external_id)
            )
        ).scalars().first()
        if row:
            row.content_hash, row.suggestion, row.action, row.computed_at = h, suggestion, action, now
        else:
            session.add(MessageEnrichment(
                tenant_id=tenant_id, creator_id=user_id, external_id=external_id,
                content_hash=h, suggestion=suggestion, action=action, computed_at=now,
            ))
        await session.commit()


def _sender_key(name: str, addr: str) -> str:
    m = re.search(r"[\w.+-]+@[\w.-]+\.\w+", (addr or "").lower())
    if m:
        return m.group(0)
    return ((name or "unknown").lower().strip())[:200]


async def _build_contact_memory(*, tenant_id: str, user_id: str, max_senders: int = 20) -> int:
    """Build/refresh a short memory note per sender from harvested mail."""
    from app.services.mail_ai import summarize_contact

    router_db = get_tenant_router()
    async with router_db.session_for(tenant_id) as session:
        rows = (
            await session.execute(
                select(HarvestedMessage)
                .where(HarvestedMessage.tenant_id == tenant_id)
                .where(HarvestedMessage.creator_id == user_id)
                .order_by(HarvestedMessage.date.desc())
                .limit(120)
            )
        ).scalars().all()

    groups: dict[str, list] = {}
    for r in rows:
        groups.setdefault(_sender_key(r.from_name, r.from_addr), []).append(r)
    top = sorted(groups.items(), key=lambda kv: len(kv[1]), reverse=True)[:max_senders]

    async with router_db.session_for(tenant_id) as session:
        ex = (
            await session.execute(
                select(ContactMemory)
                .where(ContactMemory.tenant_id == tenant_id)
                .where(ContactMemory.creator_id == user_id)
            )
        ).scalars().all()
        existing = {c.sender_key: c for c in ex}

    todo = []
    for key, msgs in top:
        h = hashlib.sha1("|".join(sorted(m.external_id for m in msgs)).encode()).hexdigest()[:16]
        e = existing.get(key)
        if e and e.content_hash == h:
            continue
        todo.append((key, msgs, h))
    if not todo:
        return 0

    sem = asyncio.Semaphore(2)

    async def _one(key, msgs, h):
        async with sem:
            name = msgs[0].from_name or key
            emails = [
                {"date": m.date, "subject": m.subject, "snippet": m.snippet} for m in msgs
            ]
            summary = await summarize_contact(name, emails)
            last = max((m.date or "") for m in msgs)
            return (key, name, msgs[0].from_addr or "", len(msgs), last, h, summary)

    results = await asyncio.gather(*[_one(k, m, h) for k, m, h in todo], return_exceptions=True)

    now = datetime.now(timezone.utc)
    async with router_db.session_for(tenant_id) as session:
        for res in results:
            if isinstance(res, Exception) or not res:
                continue
            key, name, addr, cnt, last, h, summary = res
            if not summary:
                continue
            row = (
                await session.execute(
                    select(ContactMemory)
                    .where(ContactMemory.tenant_id == tenant_id)
                    .where(ContactMemory.creator_id == user_id)
                    .where(ContactMemory.sender_key == key)
                )
            ).scalars().first()
            if row:
                row.sender_name, row.sender_addr = name, addr
                row.message_count, row.last_date = cnt, last
                row.content_hash, row.summary, row.computed_at = h, summary, now
            else:
                session.add(ContactMemory(
                    tenant_id=tenant_id, creator_id=user_id, sender_key=key,
                    sender_name=name, sender_addr=addr, message_count=cnt,
                    last_date=last, content_hash=h, summary=summary, computed_at=now,
                ))
        await session.commit()

    # Phase 2: also embed each contact summary into semantic memory so chat can
    # recall "what's the latest with X" semantically. Best-effort, dedup inside.
    try:
        from app.services.semantic_memory import add_memory
        for res in results:
            if isinstance(res, Exception) or not res:
                continue
            key, name, addr, cnt, last, h, summary = res
            if summary:
                await add_memory(user_id, tenant_id, f"{name}: {summary}",
                                 kind="contact", ref=key)
    except Exception as exc:  # noqa: BLE001
        logger.debug("semantic contact memory skipped: %s", exc)

    return len(todo)


async def recall_contacts(
    user_id: str, tenant_id: str, query: str, top_k: int = 3
) -> list[dict]:
    """Search per-sender memory by name/address. Returns matching summaries."""
    q = (query or "").lower().strip()
    router_db = get_tenant_router()
    async with router_db.session_for(tenant_id) as session:
        rows = (
            await session.execute(
                select(ContactMemory)
                .where(ContactMemory.tenant_id == tenant_id)
                .where(ContactMemory.creator_id == user_id)
            )
        ).scalars().all()
    scored = []
    terms = [t for t in re.findall(r"[a-z0-9]+", q) if len(t) > 2]
    for r in rows:
        hay = f"{r.sender_name} {r.sender_addr} {r.sender_key}".lower()
        score = sum(1 for t in terms if t in hay)
        if q and q in hay:
            score += 2
        if score > 0:
            scored.append((score, r))
    scored.sort(key=lambda x: (x[0], x[1].message_count), reverse=True)
    out, seen = [], set()
    for _, r in scored:
        nk = (r.sender_name or r.sender_key).lower()
        if nk in seen:
            continue
        seen.add(nk)
        out.append({
            "name": r.sender_name, "addr": r.sender_addr,
            "message_count": r.message_count, "last_date": r.last_date,
            "summary": r.summary,
        })
        if len(out) >= top_k:
            break
    return out


async def cached_suggestion(
    user_id: str, tenant_id: str, external_id: str
) -> dict | None:
    """Return the pre-computed {suggestion, action} for one message, or None."""
    router_db = get_tenant_router()
    async with router_db.session_for(tenant_id) as session:
        row = (
            await session.execute(
                select(MessageEnrichment)
                .where(MessageEnrichment.tenant_id == tenant_id)
                .where(MessageEnrichment.creator_id == user_id)
                .where(MessageEnrichment.external_id == external_id)
            )
        ).scalars().first()
    if not row or not row.suggestion:
        return None
    return {"suggestion": row.suggestion, "action": row.action}


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
        # Attach pre-computed AI suggestions (instant inbox).
        enr: dict[str, MessageEnrichment] = {}
        ext_ids = [r.external_id for r in rows]
        if ext_ids:
            erows = (
                await session.execute(
                    select(MessageEnrichment)
                    .where(MessageEnrichment.tenant_id == tenant_id)
                    .where(MessageEnrichment.creator_id == user_id)
                    .where(MessageEnrichment.external_id.in_(ext_ids))
                )
            ).scalars().all()
            enr = {e.external_id: e for e in erows}
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
            "suggestion": enr[r.external_id].suggestion if r.external_id in enr else None,
            "suggestion_action": enr[r.external_id].action if r.external_id in enr else None,
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
