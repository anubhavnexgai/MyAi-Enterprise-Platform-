"""Agent tools that wrap the ConnectorManager so the LLM can actually USE
the user's connected services. Every call is scoped to a user_id — the agent
must pass in `user_id` from the request context.

Each tool returns a human-readable string (not JSON) because that's what the
existing tool harness expects.
"""

from __future__ import annotations

import base64
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote_plus

import httpx

from app.services.connector_manager import get_connector_manager

logger = logging.getLogger(__name__)

GMAIL_API = "https://gmail.googleapis.com/gmail/v1"
CALENDAR_API = "https://www.googleapis.com/calendar/v3"
DRIVE_API = "https://www.googleapis.com/drive/v3"

_NOT_CONNECTED_MSG = (
    "You haven't connected {svc} yet. Go to the Connectors page to connect."
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _token_or_error(provider: str, user_id: str, tenant_id: str, svc: str) -> tuple[str | None, str | None]:
    """Return (token, error_message). Exactly one is non-None."""
    if not user_id:
        return None, "Internal error: user_id missing — cannot scope connector call."
    try:
        token = await get_connector_manager().get_token(provider, user_id, tenant_id)
    except Exception as exc:
        logger.exception("token lookup failed")
        return None, f"Failed to look up {svc} connection: {exc}"
    if not token:
        return None, _NOT_CONNECTED_MSG.format(svc=svc)
    return token, None


def _strip_html(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _decode_b64url(s: str) -> str:
    s = s.replace("-", "+").replace("_", "/")
    pad = "=" * (-len(s) % 4)
    try:
        return base64.b64decode(s + pad).decode("utf-8", errors="replace")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Gmail
# ---------------------------------------------------------------------------


async def gmail_search(
    user_id: str, query: str, limit: int = 10, tenant_id: str = "nexgai"
) -> str:
    """Search the user's Gmail. Returns formatted text results."""
    token, err = await _token_or_error("google_gmail", user_id, tenant_id, "Gmail")
    if err:
        return err
    limit = max(1, min(int(limit or 10), 25))
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            list_resp = await client.get(
                f"{GMAIL_API}/users/me/messages",
                headers=headers,
                params={"q": query, "maxResults": limit},
            )
            if list_resp.status_code >= 400:
                return f"Gmail API error ({list_resp.status_code}): {list_resp.text[:200]}"
            messages = list_resp.json().get("messages", []) or []
            if not messages:
                return f"No Gmail messages found for query: {query}"

            lines: list[str] = [f"Found {len(messages)} Gmail message(s) for '{query}':", ""]
            for i, msg_ref in enumerate(messages, 1):
                detail = await client.get(
                    f"{GMAIL_API}/users/me/messages/{msg_ref['id']}",
                    headers=headers,
                    params={"format": "metadata", "metadataHeaders": "From,Subject,Date"},
                )
                if detail.status_code >= 400:
                    continue
                msg = detail.json()
                hdrs = {
                    h["name"]: h["value"]
                    for h in msg.get("payload", {}).get("headers", [])
                }
                snippet = msg.get("snippet", "")[:140]
                lines.append(
                    f"{i}. {hdrs.get('Subject', '(no subject)')}\n"
                    f"   From: {hdrs.get('From', 'unknown')}\n"
                    f"   Date: {hdrs.get('Date', '')}\n"
                    f"   Snippet: {snippet}"
                )
            return "\n".join(lines)
    except Exception as exc:
        logger.exception("gmail_search failed")
        return f"Gmail search failed: {exc}"


async def gmail_send(
    user_id: str,
    to: str,
    subject: str,
    body: str,
    tenant_id: str = "nexgai",
) -> str:
    """Send an email via the user's Gmail."""
    token, err = await _token_or_error("google_gmail", user_id, tenant_id, "Gmail")
    if err:
        return err
    if not to or not subject:
        return "Cannot send: 'to' and 'subject' are required."

    mime = (
        f"To: {to}\r\n"
        f"Subject: {subject}\r\n"
        f"Content-Type: text/plain; charset=utf-8\r\n"
        f"\r\n{body or ''}"
    )
    raw = base64.urlsafe_b64encode(mime.encode("utf-8")).decode("ascii").rstrip("=")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"{GMAIL_API}/users/me/messages/send",
                headers=headers,
                json={"raw": raw},
            )
            if resp.status_code >= 400:
                return f"Gmail send failed ({resp.status_code}): {resp.text[:200]}"
            data = resp.json()
            return f"Email sent to {to}. Message id: {data.get('id')}"
    except Exception as exc:
        logger.exception("gmail_send failed")
        return f"Gmail send failed: {exc}"


# ---------------------------------------------------------------------------
# Google Calendar
# ---------------------------------------------------------------------------


async def calendar_list_events(
    user_id: str, days_ahead: int = 7, tenant_id: str = "nexgai"
) -> str:
    """List upcoming events from the user's primary calendar."""
    token, err = await _token_or_error(
        "google_calendar", user_id, tenant_id, "Google Calendar"
    )
    if err:
        return err
    days_ahead = max(1, min(int(days_ahead or 7), 60))
    now = datetime.now(timezone.utc)
    time_min = now.isoformat().replace("+00:00", "Z")
    time_max = (now + timedelta(days=days_ahead)).isoformat().replace("+00:00", "Z")
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(
                f"{CALENDAR_API}/calendars/primary/events",
                headers=headers,
                params={
                    "timeMin": time_min,
                    "timeMax": time_max,
                    "singleEvents": "true",
                    "orderBy": "startTime",
                    "maxResults": 25,
                },
            )
            if resp.status_code >= 400:
                return f"Calendar API error ({resp.status_code}): {resp.text[:200]}"
            events = resp.json().get("items", []) or []
            if not events:
                return f"No events in the next {days_ahead} day(s)."
            lines = [f"Upcoming events (next {days_ahead} day(s)):", ""]
            for ev in events:
                start = ev.get("start", {})
                start_str = start.get("dateTime") or start.get("date", "?")
                title = ev.get("summary", "(untitled)")
                where = ev.get("location") or ""
                attendees = ev.get("attendees") or []
                att_str = (
                    f" — {len(attendees)} attendee(s)" if attendees else ""
                )
                where_str = f" @ {where}" if where else ""
                lines.append(f"- {start_str}: {title}{where_str}{att_str}")
            return "\n".join(lines)
    except Exception as exc:
        logger.exception("calendar_list_events failed")
        return f"Calendar list failed: {exc}"


async def calendar_create_event(
    user_id: str,
    title: str,
    start: str,
    duration_min: int = 30,
    attendees: list[str] | None = None,
    description: str | None = None,
    tenant_id: str = "nexgai",
) -> str:
    """Create a calendar event in the user's primary calendar.

    `start` must be an ISO-8601 timestamp (e.g. `2026-05-29T14:00:00-07:00`).
    """
    token, err = await _token_or_error(
        "google_calendar", user_id, tenant_id, "Google Calendar"
    )
    if err:
        return err
    if not title or not start:
        return "Cannot create event: 'title' and 'start' are required."
    try:
        start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
    except ValueError:
        return (
            "Invalid 'start' timestamp. Use ISO-8601, e.g. 2026-05-29T14:00:00-07:00"
        )
    end_dt = start_dt + timedelta(minutes=int(duration_min or 30))
    body = {
        "summary": title,
        "description": description or "",
        "start": {"dateTime": start_dt.isoformat()},
        "end": {"dateTime": end_dt.isoformat()},
    }
    if attendees:
        body["attendees"] = [{"email": e} for e in attendees if e]
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"{CALENDAR_API}/calendars/primary/events",
                headers=headers,
                json=body,
                params={"sendUpdates": "all" if attendees else "none"},
            )
            if resp.status_code >= 400:
                return f"Calendar create failed ({resp.status_code}): {resp.text[:200]}"
            data = resp.json()
            return (
                f"Event '{title}' created for {start_dt.isoformat()}. "
                f"Link: {data.get('htmlLink', '(no link)')}"
            )
    except Exception as exc:
        logger.exception("calendar_create_event failed")
        return f"Calendar create failed: {exc}"


# ---------------------------------------------------------------------------
# Google Drive
# ---------------------------------------------------------------------------


async def drive_search(
    user_id: str, query: str, limit: int = 10, tenant_id: str = "nexgai"
) -> str:
    """Search the user's Google Drive."""
    token, err = await _token_or_error(
        "google_drive", user_id, tenant_id, "Google Drive"
    )
    if err:
        return err
    limit = max(1, min(int(limit or 10), 25))
    # Drive query: name contains "X" OR fullText contains "X"
    safe = query.replace("'", "\\'")
    q = f"(name contains '{safe}' or fullText contains '{safe}') and trashed = false"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(
                f"{DRIVE_API}/files",
                headers=headers,
                params={
                    "q": q,
                    "pageSize": limit,
                    "fields": "files(id,name,mimeType,modifiedTime,webViewLink,owners(emailAddress))",
                },
            )
            if resp.status_code >= 400:
                return f"Drive API error ({resp.status_code}): {resp.text[:200]}"
            files = resp.json().get("files", []) or []
            if not files:
                return f"No Drive files found for '{query}'."
            lines = [f"Found {len(files)} Drive file(s) for '{query}':", ""]
            for i, f in enumerate(files, 1):
                owner = (f.get("owners") or [{}])[0].get("emailAddress", "")
                lines.append(
                    f"{i}. {f.get('name')}\n"
                    f"   Type: {f.get('mimeType', '?')}\n"
                    f"   Modified: {f.get('modifiedTime', '?')}\n"
                    f"   Owner: {owner}\n"
                    f"   Link: {f.get('webViewLink', '')}"
                )
            return "\n".join(lines)
    except Exception as exc:
        logger.exception("drive_search failed")
        return f"Drive search failed: {exc}"


# ---------------------------------------------------------------------------
# Tool registry binding helper
# ---------------------------------------------------------------------------


def register_connector_tools(registry: Any, user_id_getter) -> None:
    """Bind the connector tools to a ToolRegistry instance.

    `user_id_getter` is a zero-arg callable that returns the current request's
    user_id (typically via a ContextVar set by the auth middleware). We wrap
    each tool so the LLM never has to (and never can) pass another user's id.
    """
    async def _gmail_search(query: str, limit: int = 10) -> str:
        return await gmail_search(user_id_getter(), query, limit)

    async def _gmail_send(to: str, subject: str, body: str) -> str:
        return await gmail_send(user_id_getter(), to, subject, body)

    async def _calendar_list(days_ahead: int = 7) -> str:
        return await calendar_list_events(user_id_getter(), days_ahead)

    async def _calendar_create(
        title: str,
        start: str,
        duration_min: int = 30,
        attendees: list[str] | None = None,
        description: str | None = None,
    ) -> str:
        return await calendar_create_event(
            user_id_getter(), title, start, duration_min, attendees, description
        )

    async def _drive_search(query: str, limit: int = 10) -> str:
        return await drive_search(user_id_getter(), query, limit)

    registry._tools["gmail_search"] = _gmail_search
    registry._tools["gmail_send"] = _gmail_send
    registry._tools["calendar_list_events"] = _calendar_list
    registry._tools["calendar_create_event"] = _calendar_create
    registry._tools["drive_search"] = _drive_search
