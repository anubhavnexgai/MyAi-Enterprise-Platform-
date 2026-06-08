"""Employee directory — persist a row per user so the super-admin can list
everyone and attribute usage.

Provisioned on first SSO login (and for the dev user / demo seed). ``user_id``
mirrors the JWT ``sub`` and is the join key against ``creator_id`` everywhere
else. Pattern borrowed from EAP's ``agenthub_users`` + ``user_provisioning``.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import select

from app.storage.models import Employee
from app.tenants.router import get_tenant_router

logger = logging.getLogger(__name__)


def _roles_to_str(roles) -> str:
    if isinstance(roles, str):
        return roles
    return ",".join(r for r in (roles or []) if r) or "user"


def _roles_to_list(roles: Optional[str]) -> List[str]:
    return [r.strip() for r in (roles or "").split(",") if r.strip()]


def _to_dict(e: Employee) -> dict:
    return {
        "user_id": e.user_id,
        "email": e.email,
        "full_name": e.full_name,
        "roles": _roles_to_list(e.roles),
        "is_active": bool(e.is_active),
        "department": e.department,
        "manager_id": e.manager_id,
        "created_at": e.created_at.isoformat() if e.created_at else None,
        "last_login_at": e.last_login_at.isoformat() if e.last_login_at else None,
    }


async def upsert_employee(
    tenant_id: str,
    user_id: str,
    email: str,
    full_name: Optional[str] = None,
    roles=None,
    *,
    touch_login: bool = False,
    department: Optional[str] = None,
) -> None:
    """Insert or update an employee. Best-effort (never raises to the caller)."""
    try:
        router = get_tenant_router()
        async with router.session_for(tenant_id) as session:
            row = (
                await session.execute(
                    select(Employee)
                    .where(Employee.tenant_id == tenant_id)
                    .where(Employee.user_id == user_id)
                )
            ).scalars().first()
            now = datetime.now(timezone.utc)
            if row is None:
                row = Employee(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    email=email or user_id,
                    full_name=full_name,
                    roles=_roles_to_str(roles),
                    is_active=1,
                    department=department,
                    last_login_at=now if touch_login else None,
                )
                session.add(row)
            else:
                # Keep identity fields fresh; only update roles when provided.
                if email:
                    row.email = email
                if full_name:
                    row.full_name = full_name
                if roles is not None:
                    row.roles = _roles_to_str(roles)
                if department is not None:
                    row.department = department
                if touch_login:
                    row.last_login_at = now
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("upsert_employee failed for %s/%s: %s", tenant_id, user_id, exc)


async def list_employees(tenant_id: str) -> List[dict]:
    router = get_tenant_router()
    async with router.session_for(tenant_id) as session:
        rows = (
            await session.execute(
                select(Employee)
                .where(Employee.tenant_id == tenant_id)
                .order_by(Employee.full_name.asc())
            )
        ).scalars().all()
    return [_to_dict(r) for r in rows]


async def get_employee(tenant_id: str, user_id: str) -> Optional[dict]:
    router = get_tenant_router()
    async with router.session_for(tenant_id) as session:
        row = (
            await session.execute(
                select(Employee)
                .where(Employee.tenant_id == tenant_id)
                .where(Employee.user_id == user_id)
            )
        ).scalars().first()
    return _to_dict(row) if row else None
