"""Request / response Pydantic schemas shared across the API surfaces."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ----------------------------------------------------------------------------
# Dashboard
# ----------------------------------------------------------------------------


class DashboardKPI(BaseModel):
    label: str
    value: float | int | str
    delta: Optional[float] = None
    unit: Optional[str] = None
    trend: Optional[str] = None  # up | down | flat


class DashboardKPIs(BaseModel):
    period: str = "last_7_days"
    kpis: List[DashboardKPI] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=datetime.utcnow)


class RetentionCustomer(BaseModel):
    customer_id: str
    name: str
    risk_score: float
    last_contact: Optional[datetime] = None
    reason: Optional[str] = None
    recommended_action: Optional[str] = None


# ----------------------------------------------------------------------------
# Inbox
# ----------------------------------------------------------------------------


class InboxTaskCreate(BaseModel):
    title: str
    summary: Optional[str] = None
    priority: str = "normal"
    source: str = "manual"
    payload: Optional[Dict[str, Any]] = None


class InboxTaskOut(BaseModel):
    id: int
    title: str
    summary: Optional[str] = None
    source: str
    priority: str
    status: str
    payload: Optional[Dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime
    # Lifecycle fields
    due_at: Optional[datetime] = None
    sla_minutes: Optional[int] = None
    assignee_id: Optional[str] = "me"
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    escalated_at: Optional[datetime] = None
    escalation_count: int = 0


class InboxTaskUpdate(BaseModel):
    status: Optional[str] = None
    priority: Optional[str] = None
    summary: Optional[str] = None
    due_at: Optional[datetime] = None
    sla_minutes: Optional[int] = None
    assignee_id: Optional[str] = None


# ----------------------------------------------------------------------------
# Copilot
# ----------------------------------------------------------------------------


class ChatMessage(BaseModel):
    role: str = Field(..., description="user | assistant | system | tool")
    content: str


class ChatRequest(BaseModel):
    message: str
    history: List[ChatMessage] = Field(default_factory=list)
    persona: Optional[str] = None
    stream: bool = False


class ChatResponse(BaseModel):
    reply: str
    tool_calls: List[Dict[str, Any]] = Field(default_factory=list)
    model: Optional[str] = None
    used_fallback: bool = False
    elapsed_ms: int = 0


# ----------------------------------------------------------------------------
# Connectors
# ----------------------------------------------------------------------------


class ConnectorStatus(BaseModel):
    provider: str
    status: str  # connected | pending | disconnected | error
    account_email: Optional[str] = None
    connected_at: Optional[datetime] = None
    last_synced_at: Optional[datetime] = None
    scope: Optional[str] = None


class ConnectorConnectStart(BaseModel):
    provider: str
    authorization_url: str
    state: str


# ----------------------------------------------------------------------------
# Files
# ----------------------------------------------------------------------------


class FileUploadResponse(BaseModel):
    id: int
    filename: str
    content_type: Optional[str]
    size_bytes: int
    status: str
    summary: Optional[str] = None


# ----------------------------------------------------------------------------
# Audit log
# ----------------------------------------------------------------------------


class AuditLogEntry(BaseModel):
    id: int
    event_type: str
    severity: str
    message: str
    payload: Optional[Dict[str, Any]] = None
    creator_id: str
    tenant_id: str
    created_at: datetime
