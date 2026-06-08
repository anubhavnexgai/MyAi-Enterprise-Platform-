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


class SendEmailRequest(BaseModel):
    to: str
    subject: str
    body: str = ""
    confirm: bool = False  # explicit human approval (satisfies L2-L4 gate)


class CreateEventRequest(BaseModel):
    title: str
    start: str  # ISO-8601, e.g. 2026-05-29T14:00:00-07:00
    duration_min: int = 30
    attendees: List[str] = Field(default_factory=list)
    description: Optional[str] = None
    confirm: bool = False


class SnoozeRequest(BaseModel):
    until: Optional[str] = None  # ISO-8601; when the item should resurface
    confirm: bool = False


class WriteActionResponse(BaseModel):
    status: str  # ok | blocked
    action: str
    detail: Optional[str] = None
    result: Optional[str] = None
    needs_confirmation: bool = False
    autonomy_level: Optional[int] = None


class MailSuggestRequest(BaseModel):
    subject: str = ""
    sender: str = ""
    body: str = ""
    message_id: Optional[str] = None  # if set, use/refresh the pre-computed cache
    account: Optional[str] = None     # gmail | outlook


class MailSuggestResponse(BaseModel):
    suggestion: str
    action: Optional[str] = None  # reply | archive | schedule | pay | ignore | none


class ChatResponse(BaseModel):
    reply: str
    tool_calls: List[Dict[str, Any]] = Field(default_factory=list)
    model: Optional[str] = None
    used_fallback: bool = False
    elapsed_ms: int = 0
    # Correctness spine (Pillar 1): grounding verdict + block-level citations.
    # grounded ∈ not_required | grounded | no_context | partial | ungrounded | unverified
    grounded: Optional[str] = None
    citations: List[Dict[str, str]] = Field(default_factory=list)
    unsupported_claims: List[str] = Field(default_factory=list)
    # Multi-agent: which specialists the lead orchestrator used (empty for a
    # normal single-agent turn).
    agents_used: List[str] = Field(default_factory=list)
    orchestrated: bool = False


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
