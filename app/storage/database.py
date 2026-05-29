"""Async SQLAlchemy engine + schema init.

We default to the shared engine exposed by ``app.tenants.router``. This module
just adds Base/metadata management and a one-shot ``init_database()`` called at
app startup so the SQLite fallback "just works" for new contributors.
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from app.tenants.router import get_tenant_router

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """Declarative base for all MyAi-Enterprise ORM models."""


def get_engine() -> AsyncEngine:
    router = get_tenant_router()
    router._ensure_global()  # noqa: SLF001 - intentional internal call
    assert router._global_engine is not None
    return router._global_engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    router = get_tenant_router()
    return router._ensure_global()  # noqa: SLF001


async def init_database() -> None:
    """Create tables for all registered models on the shared engine.

    Also performs lightweight forward-only column migrations for SQLite.
    Idempotent. Safe to call on every boot.
    """
    # Importing models registers them on Base.metadata
    from app.storage import models  # noqa: F401
    from sqlalchemy import text

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # ---- Lightweight migrations (additive only) ----
        # Add new InboxTask lifecycle columns if missing.
        migrations = {
            "inbox_tasks": [
                ("due_at",           "DATETIME"),
                ("sla_minutes",      "INTEGER"),
                ("assignee_id",      "VARCHAR(128) DEFAULT 'me'"),
                ("started_at",       "DATETIME"),
                ("completed_at",     "DATETIME"),
                ("escalated_at",     "DATETIME"),
                ("escalation_count", "INTEGER DEFAULT 0"),
            ],
        }
        for table, cols in migrations.items():
            existing = await conn.execute(text(f"PRAGMA table_info({table})"))
            existing_names = {r[1] for r in existing.fetchall()}
            for name, ddl in cols:
                if name not in existing_names:
                    try:
                        await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
                        logger.info("Migration: added %s.%s", table, name)
                    except Exception as exc:
                        logger.warning("Migration for %s.%s failed: %s", table, name, exc)

    logger.info("Database schema ensured")


async def shutdown_database() -> None:
    router = get_tenant_router()
    await router.dispose()
