"""Tenant DB router.

Hands out an SQLAlchemy async engine + session-maker per tenant. If the tenant
has its own ``database_url`` configured we use that; otherwise we fall back to
the global database (per-row tenant isolation).

Either way, callers must still scope queries by ``tenant_id`` + ``creator_id``
- physical isolation is a defence-in-depth bonus, not a replacement.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Dict

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings
from app.tenants.registry import get_tenant_registry

logger = logging.getLogger(__name__)


class TenantRouter:
    def __init__(self) -> None:
        self._engines: Dict[str, AsyncEngine] = {}
        self._sessions: Dict[str, async_sessionmaker[AsyncSession]] = {}
        self._global_engine: AsyncEngine | None = None
        self._global_sessionmaker: async_sessionmaker[AsyncSession] | None = None

    # ---- global (shared) DB --------------------------------------------------

    def _ensure_global(self) -> async_sessionmaker[AsyncSession]:
        if self._global_sessionmaker is None:
            url = get_settings().resolved_database_url
            logger.info("Creating shared async engine: %s", _safe_url(url))
            self._global_engine = create_async_engine(url, echo=False, future=True)
            self._global_sessionmaker = async_sessionmaker(
                self._global_engine, expire_on_commit=False, class_=AsyncSession
            )
        return self._global_sessionmaker

    def global_session(self) -> AsyncSession:
        return self._ensure_global()()

    # ---- per-tenant DB -------------------------------------------------------

    def session_for(self, tenant_id: str) -> AsyncSession:
        """Return an async session for the given tenant.

        Falls back to the shared global engine if the tenant has no dedicated DB.
        """
        if tenant_id in self._sessions:
            return self._sessions[tenant_id]()

        registry = get_tenant_registry()
        cfg = registry.get(tenant_id)
        if cfg is not None and cfg.database_url:
            logger.info("Creating dedicated engine for tenant %s", tenant_id)
            engine = create_async_engine(cfg.database_url, echo=False, future=True)
            sm = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
            self._engines[tenant_id] = engine
            self._sessions[tenant_id] = sm
            return sm()

        # Fallback: shared DB, scoped by tenant_id column
        return self._ensure_global()()

    async def dispose(self) -> None:
        if self._global_engine is not None:
            await self._global_engine.dispose()
        for engine in self._engines.values():
            try:
                await engine.dispose()
            except Exception:
                logger.exception("Failed to dispose tenant engine")


def _safe_url(url: str) -> str:
    if "@" not in url:
        return url
    head, tail = url.split("@", 1)
    return f"***@{tail}"


@lru_cache
def get_tenant_router() -> TenantRouter:
    return TenantRouter()
