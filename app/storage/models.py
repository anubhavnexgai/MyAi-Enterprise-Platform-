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

    # ---- Service request lifecycle ----
    # When this task should be done by; the lifecycle ticker watches breaches.
    due_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    # SLA window from creation (set explicitly OR inferred from priority).
    sla_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # 'me' = the owner; otherwise an agent name like 'myai' or another user id.
    assignee_id: Mapped[str] = mapped_column(String(128), default="me")
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    escalated_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    escalation_count: Mapped[int] = mapped_column(Integer, default=0)

    payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_inbox_tenant_user", "tenant_id", "creator_id"),
        Index("ix_inbox_due", "tenant_id", "due_at"),
    )


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


class UserPreference(Base):
    """Per-user UI / behavior preferences (one row per (tenant, user))."""

    __tablename__ = "user_preferences"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    creator_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)

    autonomy_level: Mapped[int] = mapped_column(Integer, default=1)  # 1-5 (L1-L5)
    data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ix_prefs_tenant_user", "tenant_id", "creator_id", unique=True),
    )


class ChatThread(Base):
    """A copilot chat thread."""

    __tablename__ = "chat_threads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    creator_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)

    title: Mapped[str] = mapped_column(String(256), default="New chat")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), index=True
    )

    __table_args__ = (
        Index("ix_threads_tenant_user", "tenant_id", "creator_id"),
    )


class ChatMessage(Base):
    """A single message in a chat thread."""

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    creator_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    thread_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)

    role: Mapped[str] = mapped_column(String(16), nullable=False)  # user|assistant|system
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class HarvestedMessage(Base):
    """Cached email row from a connector (Gmail / Outlook).

    Rebuilt every harvester tick; queries read from this table instead of
    hitting the upstream API on every page load.
    """

    __tablename__ = "harvested_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    creator_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    account: Mapped[str] = mapped_column(String(16), nullable=False)  # gmail|outlook
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    thread_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    subject: Mapped[str] = mapped_column(String(1024), default="")
    from_addr: Mapped[str] = mapped_column(String(512), default="")
    from_name: Mapped[str] = mapped_column(String(256), default="")
    date: Mapped[str] = mapped_column(String(64), default="")
    snippet: Mapped[str] = mapped_column(Text, default="")
    label_ids: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    priority: Mapped[str] = mapped_column(String(16), default="medium")
    unread: Mapped[bool] = mapped_column(Integer, default=1)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)

    __table_args__ = (
        Index("ix_harvm_tenant_user_account", "tenant_id", "creator_id", "account"),
        Index("ix_harvm_external", "tenant_id", "creator_id", "external_id", unique=True),
    )


class HarvestedEvent(Base):
    """Cached calendar event row from a connector (Google / Outlook)."""

    __tablename__ = "harvested_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    creator_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    account: Mapped[str] = mapped_column(String(16), nullable=False)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(512), default="")
    start: Mapped[str] = mapped_column(String(64), default="")
    end: Mapped[str] = mapped_column(String(64), default="")
    location: Mapped[str] = mapped_column(String(512), default="")
    html_link: Mapped[str] = mapped_column(String(1024), default="")
    attendee_count: Mapped[int] = mapped_column(Integer, default=0)
    all_day: Mapped[bool] = mapped_column(Integer, default=0)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)

    __table_args__ = (
        Index("ix_harve_tenant_user_account", "tenant_id", "creator_id", "account"),
        Index("ix_harve_external", "tenant_id", "creator_id", "external_id", unique=True),
    )


class HarvestState(Base):
    """Last successful crawl per (user, account, kind=messages|events)."""

    __tablename__ = "harvest_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    creator_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    account: Mapped[str] = mapped_column(String(16), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # messages|events
    last_run_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    items_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index(
            "ix_harvs_unique",
            "tenant_id", "creator_id", "account", "kind",
            unique=True,
        ),
    )


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
