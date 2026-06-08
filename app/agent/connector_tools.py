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
                    params=[
                        ("format", "metadata"),
                        ("metadataHeaders", "From"),
                        ("metadataHeaders", "Subject"),
                        ("metadataHeaders", "Date"),
                    ],
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
    days_ahead = max(1, min(int(days_ahead or 7), 366))
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


_RECURRENCE_RULES = {
    "daily": "RRULE:FREQ=DAILY",
    "everyday": "RRULE:FREQ=DAILY",
    "weekdays": "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
    "weekday": "RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
    "weekly": "RRULE:FREQ=WEEKLY",
    "weekends": "RRULE:FREQ=WEEKLY;BYDAY=SA,SU",
    "weekend": "RRULE:FREQ=WEEKLY;BYDAY=SA,SU",
}


def _parse_clock_time(s: str) -> tuple[int, int] | None:
    """Parse '8:30 PM', '20:30', '8pm', '8:30pm' -> (hour24, minute)."""
    s = (s or "").strip().lower()
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", s)
    if not m:
        return None
    hour = int(m.group(1))
    minute = int(m.group(2) or 0)
    ampm = m.group(3)
    if ampm == "pm" and hour < 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


async def calendar_update_event(
    user_id: str,
    query: str,
    new_time: str | None = None,
    recurrence: str | None = None,
    new_title: str | None = None,
    tenant_id: str = "nexgai",
) -> str:
    """Find an existing event by title and update its time and/or recurrence.

    Modifies the whole recurring SERIES when the matched event repeats.
    - `query`: words from the event title, e.g. "Daily evening meeting".
    - `new_time`: clock time like "8:30 PM" or "20:30" (keeps each event's date).
    - `recurrence`: one of daily / weekdays / weekly / weekends / none.
    - `new_title`: optional rename.
    """
    token, err = await _token_or_error(
        "google_calendar", user_id, tenant_id, "Google Calendar"
    )
    if err:
        return err
    if not query:
        return "Cannot update: tell me which event (its title)."

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    now = datetime.now(timezone.utc)
    time_min = now.isoformat().replace("+00:00", "Z")
    time_max = (now + timedelta(days=45)).isoformat().replace("+00:00", "Z")
    ql = query.lower()
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            # 1) Find a matching upcoming instance.
            r = await client.get(
                f"{CALENDAR_API}/calendars/primary/events",
                headers=headers,
                params={
                    "timeMin": time_min, "timeMax": time_max,
                    "singleEvents": "true", "orderBy": "startTime", "maxResults": 50,
                },
            )
            if r.status_code >= 400:
                return f"Calendar lookup failed ({r.status_code}): {r.text[:200]}"
            items = r.json().get("items", []) or []
            match = next((e for e in items if ql in (e.get("summary", "").lower())), None)
            if not match:
                titles = ", ".join(sorted({e.get("summary", "?") for e in items})[:8])
                return (f"No upcoming event matches '{query}'. "
                        f"Your upcoming events are: {titles or '(none)'}.")

            # 2) Resolve the master event (so we edit the whole series).
            master_id = match.get("recurringEventId") or match.get("id")
            mr = await client.get(
                f"{CALENDAR_API}/calendars/primary/events/{master_id}",
                headers=headers,
            )
            if mr.status_code >= 400:
                return f"Couldn't load the event ({mr.status_code}): {mr.text[:200]}"
            master = mr.json()

            patch: dict[str, Any] = {}

            # 3) Time change — preserve each occurrence's date + timezone, swap the clock time.
            if new_time:
                hm = _parse_clock_time(new_time)
                if not hm:
                    return f"Couldn't understand the time '{new_time}'. Try e.g. '8:30 PM'."
                hour, minute = hm
                s = master.get("start", {})
                e = master.get("end", {})
                s_dt_raw = s.get("dateTime")
                if not s_dt_raw:
                    return "That event is all-day; I can only retime timed events."
                start_dt = datetime.fromisoformat(s_dt_raw)
                dur = timedelta(minutes=30)
                if e.get("dateTime"):
                    dur = datetime.fromisoformat(e["dateTime"]) - start_dt
                new_start = start_dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
                new_end = new_start + dur
                tz = s.get("timeZone")
                patch["start"] = {"dateTime": new_start.isoformat()}
                patch["end"] = {"dateTime": new_end.isoformat()}
                if tz:
                    patch["start"]["timeZone"] = tz
                    patch["end"]["timeZone"] = e.get("timeZone", tz)

            # 4) Recurrence change.
            if recurrence is not None:
                key = recurrence.strip().lower()
                if key in ("none", "once", "off", "no", ""):
                    patch["recurrence"] = None  # clear → single event
                elif key in _RECURRENCE_RULES:
                    patch["recurrence"] = [_RECURRENCE_RULES[key]]
                else:
                    return (f"Unknown recurrence '{recurrence}'. Use one of: "
                            f"daily, weekdays, weekly, weekends, none.")

            if new_title:
                patch["summary"] = new_title

            if not patch:
                return "Nothing to change — give a new time, recurrence, or title."

            # 5) Apply (PATCH only changes the supplied fields).
            up = await client.patch(
                f"{CALENDAR_API}/calendars/primary/events/{master_id}",
                headers=headers,
                json=patch,
            )
            if up.status_code >= 400:
                return f"Calendar update failed ({up.status_code}): {up.text[:200]}"
            data = up.json()
            changed = []
            if new_time:
                changed.append(f"time → {new_time}")
            if recurrence is not None:
                changed.append(f"repeats → {recurrence}")
            if new_title:
                changed.append(f"renamed → {new_title}")
            return (f"Updated '{data.get('summary', master.get('summary','event'))}' "
                    f"({', '.join(changed)}). This applied to the whole series. "
                    f"Link: {data.get('htmlLink', '(no link)')}")
    except Exception as exc:
        logger.exception("calendar_update_event failed")
        return f"Calendar update failed: {exc}"


async def calendar_delete_event(
    user_id: str, event_id: str, tenant_id: str = "nexgai"
) -> str:
    """Delete a single calendar event by its id."""
    token, err = await _token_or_error(
        "google_calendar", user_id, tenant_id, "Google Calendar"
    )
    if err:
        return err
    if not event_id:
        return "Cannot delete: event_id is required."
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.delete(
                f"{CALENDAR_API}/calendars/primary/events/{event_id}",
                headers=headers,
            )
            # 200/204 = deleted; 410 = already gone (treat as success).
            if resp.status_code in (200, 204, 410):
                return "OK"
            return f"Calendar delete failed ({resp.status_code}): {resp.text[:200]}"
    except Exception as exc:
        return f"Calendar delete failed: {exc}"


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
# Gmail mutation helpers
# ---------------------------------------------------------------------------


async def gmail_modify_labels(
    user_id: str,
    message_id: str,
    add: list[str] | None = None,
    remove: list[str] | None = None,
    tenant_id: str = "nexgai",
) -> str:
    """Add/remove Gmail labels on a message. Returns a status string."""
    token, err = await _token_or_error("google_gmail", user_id, tenant_id, "Gmail")
    if err:
        return err
    body = {"addLabelIds": add or [], "removeLabelIds": remove or []}
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{GMAIL_API}/users/me/messages/{message_id}/modify",
                headers=headers,
                json=body,
            )
            if resp.status_code >= 400:
                return f"Gmail modify failed ({resp.status_code}): {resp.text[:200]}"
        return "OK"
    except Exception as exc:
        return f"Gmail modify failed: {exc}"


async def gmail_archive(user_id: str, message_id: str, tenant_id: str = "nexgai") -> str:
    return await gmail_modify_labels(
        user_id, message_id, remove=["INBOX"], tenant_id=tenant_id
    )


async def gmail_trash(user_id: str, message_id: str, tenant_id: str = "nexgai") -> str:
    """Move a Gmail message to Trash (recoverable for 30 days)."""
    token, err = await _token_or_error("google_gmail", user_id, tenant_id, "Gmail")
    if err:
        return err
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{GMAIL_API}/users/me/messages/{message_id}/trash",
                headers=headers,
            )
            if resp.status_code >= 400:
                return f"Gmail trash failed ({resp.status_code}): {resp.text[:200]}"
        return "OK"
    except Exception as exc:
        return f"Gmail trash failed: {exc}"


async def gmail_thread_get(
    user_id: str, thread_id: str, tenant_id: str = "nexgai"
) -> dict:
    """Fetch all messages in a Gmail thread (newest-relevant order, text bodies)."""
    token, err = await _token_or_error("google_gmail", user_id, tenant_id, "Gmail")
    if err:
        return {"error": err}
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(
                f"{GMAIL_API}/users/me/threads/{thread_id}",
                headers=headers,
                params={"format": "full"},
            )
            if r.status_code >= 400:
                return {"error": f"Gmail thread error {r.status_code}"}
            data = r.json()
    except Exception as exc:
        return {"error": str(exc)}

    messages: list[dict] = []
    for msg in data.get("messages", []) or []:
        hdrs = {
            h["name"]: h["value"]
            for h in (msg.get("payload", {}).get("headers", []) or [])
        }
        body_text = ""

        def _walk(part):
            nonlocal body_text
            if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data") and not body_text:
                body_text = _decode_b64url(part["body"]["data"])
            for sub in part.get("parts") or []:
                _walk(sub)

        _walk(msg.get("payload", {}))
        messages.append(
            {
                "id": msg.get("id"),
                "from": hdrs.get("From", ""),
                "date": hdrs.get("Date", ""),
                "subject": hdrs.get("Subject", ""),
                "snippet": msg.get("snippet", ""),
                "body": (body_text or msg.get("snippet", ""))[:4000],
            }
        )
    return {"thread_id": thread_id, "count": len(messages), "messages": messages}


async def gmail_mark_read(user_id: str, message_id: str, tenant_id: str = "nexgai") -> str:
    return await gmail_modify_labels(
        user_id, message_id, remove=["UNREAD"], tenant_id=tenant_id
    )


async def gmail_get_full(
    user_id: str, message_id: str, tenant_id: str = "nexgai"
) -> dict:
    """Fetch a single Gmail message with full headers + plain-text body."""
    token, err = await _token_or_error("google_gmail", user_id, tenant_id, "Gmail")
    if err:
        return {"error": err}
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(
                f"{GMAIL_API}/users/me/messages/{message_id}",
                headers=headers,
                params={"format": "full"},
            )
            if r.status_code >= 400:
                return {"error": f"Gmail error {r.status_code}"}
            msg = r.json()
        hdrs = {
            h["name"]: h["value"]
            for h in (msg.get("payload", {}).get("headers", []) or [])
        }
        # Walk parts: collect the plain-text body + any file attachments.
        body_text = ""
        attachments: list[dict] = []
        def _walk(part):
            nonlocal body_text
            mime = part.get("mimeType", "")
            body = part.get("body", {}) or {}
            data = body.get("data")
            fn = part.get("filename")
            att_id = body.get("attachmentId")
            if fn and att_id:
                attachments.append({
                    "filename": fn, "mime": mime,
                    "size": int(body.get("size") or 0),
                    "attachment_id": att_id,
                })
            if mime == "text/plain" and data and not body_text:
                body_text = _decode_b64url(data)
            for sub in part.get("parts") or []:
                _walk(sub)
        _walk(msg.get("payload", {}))
        if not body_text:
            body_text = msg.get("snippet", "") or ""
        return {
            "id": msg.get("id"),
            "thread_id": msg.get("threadId"),
            "subject": hdrs.get("Subject", "(no subject)"),
            "from": hdrs.get("From", ""),
            "to": hdrs.get("To", ""),
            "cc": hdrs.get("Cc", ""),
            "date": hdrs.get("Date", ""),
            "snippet": msg.get("snippet", ""),
            "body": body_text[:6000],
            "label_ids": msg.get("labelIds", []),
            "attachments": attachments,
        }
    except Exception as exc:
        return {"error": str(exc)}


async def gmail_get_attachment(
    user_id: str, message_id: str, attachment_id: str, tenant_id: str = "nexgai"
) -> tuple[bytes | None, str | None]:
    """Download a single Gmail attachment. Returns (raw_bytes, error)."""
    token, err = await _token_or_error("google_gmail", user_id, tenant_id, "Gmail")
    if err:
        return None, err
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(
                f"{GMAIL_API}/users/me/messages/{message_id}/attachments/{attachment_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if r.status_code >= 400:
                return None, f"Gmail attachment error {r.status_code}"
            data = r.json().get("data", "")
        raw = base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
        return raw, None
    except Exception as exc:  # noqa: BLE001
        return None, str(exc)


# ---------------------------------------------------------------------------
# Structured data helpers (for dashboard / inbox — not LLM-facing)
# ---------------------------------------------------------------------------


async def gmail_messages_structured(
    user_id: str,
    query: str = "is:unread in:inbox",
    limit: int = 15,
    tenant_id: str = "nexgai",
) -> list[dict]:
    """Return Gmail messages as structured records (not formatted text)."""
    token, err = await _token_or_error("google_gmail", user_id, tenant_id, "Gmail")
    if err:
        return []
    limit = max(1, min(int(limit or 15), 100))
    headers = {"Authorization": f"Bearer {token}"}
    out: list[dict] = []
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            list_resp = await client.get(
                f"{GMAIL_API}/users/me/messages",
                headers=headers,
                params={"q": query, "maxResults": limit},
            )
            if list_resp.status_code >= 400:
                return []
            messages = list_resp.json().get("messages", []) or []
            for msg_ref in messages:
                detail = await client.get(
                    f"{GMAIL_API}/users/me/messages/{msg_ref['id']}",
                    headers=headers,
                    params=[
                        ("format", "metadata"),
                        ("metadataHeaders", "From"),
                        ("metadataHeaders", "Subject"),
                        ("metadataHeaders", "Date"),
                    ],
                )
                if detail.status_code >= 400:
                    continue
                msg = detail.json()
                hdrs = {
                    h["name"]: h["value"]
                    for h in msg.get("payload", {}).get("headers", [])
                }
                snippet = msg.get("snippet", "")[:200]
                from_raw = hdrs.get("From", "")
                # Extract display name from "Name <email>"
                m = re.match(r"\s*\"?([^\"<]+?)\"?\s*<", from_raw)
                from_name = m.group(1).strip() if m else from_raw.split("@")[0]
                out.append(
                    {
                        "id": msg_ref["id"],
                        "thread_id": msg.get("threadId"),
                        "subject": hdrs.get("Subject", "(no subject)"),
                        "from": from_raw,
                        "from_name": from_name or "Unknown",
                        "date": hdrs.get("Date", ""),
                        "snippet": snippet,
                        "label_ids": msg.get("labelIds", []),
                        "unread": "UNREAD" in (msg.get("labelIds") or []),
                    }
                )
        return out
    except Exception as exc:
        logger.warning("gmail_messages_structured failed: %s", exc)
        return []


async def gmail_counts(user_id: str, tenant_id: str = "nexgai") -> dict:
    """Return quick Gmail counts for dashboard KPIs."""
    token, err = await _token_or_error("google_gmail", user_id, tenant_id, "Gmail")
    if err:
        return {"unread": 0, "drafts": 0, "available": False}
    headers = {"Authorization": f"Bearer {token}"}
    out = {"unread": 0, "drafts": 0, "available": True}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            # Inbox label gives totals
            r = await client.get(
                f"{GMAIL_API}/users/me/labels/INBOX", headers=headers
            )
            if r.status_code < 400:
                data = r.json()
                out["unread"] = int(data.get("messagesUnread", 0) or 0)
            d = await client.get(
                f"{GMAIL_API}/users/me/drafts",
                headers=headers,
                params={"maxResults": 1},
            )
            if d.status_code < 400:
                # resultSizeEstimate is approx but fine for KPI display
                out["drafts"] = int(d.json().get("resultSizeEstimate", 0) or 0)
        return out
    except Exception as exc:
        logger.warning("gmail_counts failed: %s", exc)
        return out


async def calendar_events_structured(
    user_id: str, days_ahead: int = 7, tenant_id: str = "nexgai"
) -> list[dict]:
    """Return calendar events as structured records."""
    token, err = await _token_or_error(
        "google_calendar", user_id, tenant_id, "Google Calendar"
    )
    if err:
        return []
    days_ahead = max(1, min(int(days_ahead or 7), 366))
    now = datetime.now(timezone.utc)
    time_min = now.isoformat().replace("+00:00", "Z")
    time_max = (now + timedelta(days=days_ahead)).isoformat().replace("+00:00", "Z")
    headers = {"Authorization": f"Bearer {token}"}
    out: list[dict] = []
    from urllib.parse import quote
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            # Pull events from ALL of the user's calendars (primary + secondary
            # ones like Holidays, Birthdays, Formula 1), not just primary.
            cal_ids = ["primary"]
            try:
                cl = await client.get(
                    f"{CALENDAR_API}/users/me/calendarList",
                    headers=headers, params={"maxResults": 50},
                )
                if cl.status_code < 400:
                    items = cl.json().get("items", []) or []
                    picked = [c["id"] for c in items
                              if c.get("id") and c.get("selected", True)]
                    if picked:
                        cal_ids = picked[:15]  # bound the fan-out
            except Exception:  # noqa: BLE001
                pass
            for cal_id in cal_ids:
                try:
                    resp = await client.get(
                        f"{CALENDAR_API}/calendars/{quote(cal_id, safe='')}/events",
                        headers=headers,
                        params={
                            "timeMin": time_min, "timeMax": time_max,
                            "singleEvents": "true", "orderBy": "startTime",
                            # High cap so a daily-recurring event doesn't exhaust
                            # the page before later months in the range.
                            "maxResults": 250,
                        },
                    )
                    if resp.status_code >= 400:
                        continue
                    for ev in resp.json().get("items", []) or []:
                        start = ev.get("start", {})
                        end = ev.get("end", {})
                        start_str = start.get("dateTime") or start.get("date", "")
                        end_str = end.get("dateTime") or end.get("date", "")
                        attendees = ev.get("attendees") or []
                        out.append({
                            "id": ev.get("id"),
                            "title": ev.get("summary", "(untitled)"),
                            "start": start_str,
                            "end": end_str,
                            "location": ev.get("location", ""),
                            "html_link": ev.get("htmlLink", ""),
                            "attendee_count": len(attendees),
                            "all_day": "date" in start and "dateTime" not in start,
                        })
                except Exception:  # noqa: BLE001
                    continue
        # De-dupe (an event can appear in multiple calendars) + sort.
        seen, uniq = set(), []
        for e in out:
            k = e.get("id")
            if k and k in seen:
                continue
            if k:
                seen.add(k)
            uniq.append(e)
        uniq.sort(key=lambda e: e.get("start") or "")
        return uniq
    except Exception as exc:
        logger.warning("calendar_events_structured failed: %s", exc)
        return []


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
