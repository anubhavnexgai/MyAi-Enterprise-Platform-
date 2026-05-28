"""ORM models.

Every row has ``tenant_id`` + ``creator_id`` columns. The harvester gateway
adds those to the WHERE clause of every query - the FK isn't strictly enforced
at the DB layer, but it's universal at the application layer.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.storage.database import Base


class InboxTask(Base):
    __tablename__ = "inbox_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    creator_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)

    title: Mapped[str] = mapped_column(String(512), nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(64), default="agent")  # agent | rule | manual
    priority: Mapped[str] = mapped_column(String(16), default="normal")
    status: Mapped[str] = mapped_column(String(32), default="open", index=True)

    payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (Index("ix_inbox_tenant_user", "tenant_id", "creator_id"),)


# NOTE: connector accounts are managed by ``app.services.connector_manager`` via
# its own aiosqlite table ``user_connections`` (so OAuth tokens stay isolated
# from the SQLAlchemy ORM). See that module for the schema.


class UploadedFile(Base):
    __tablename__ = "uploaded_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    creator_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)

    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    content_type: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="uploaded")  # uploaded|parsed|failed
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    extra: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    creator_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)

    event_type: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), default="info")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    ip_address: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
