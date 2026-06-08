"""Microsoft 365 / Outlook tools — parallel to gmail/calendar tools.

Uses the Microsoft Graph v1.0 API with the per-user OAuth token from
``connector_manager``.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from app.services.connector_manager import get_connector_manager

logger = logging.getLogger(__name__)

GRAPH = "https://graph.microsoft.com/v1.0"


async def _token(user_id: str, tenant_id: str) -> tuple[str | None, str | None]:
    try:
        tok = await get_connector_manager().get_token("microsoft_graph", user_id, tenant_id)
    except Exception as exc:
        return None, f"Outlook token error: {exc}"
    if not tok:
        return None, "Outlook is not connected. Connect Microsoft 365 on the Connectors page."
    return tok, None


async def outlook_messages_structured(
    user_id: str,
    query: str = "",
    limit: int = 15,
    unread_only: bool = True,
    tenant_id: str = "nexgai",
) -> list[dict]:
    """Return Outlook messages as structured records."""
    tok, err = await _token(user_id, tenant_id)
    if err:
        return []
    headers = {"Authorization": f"Bearer {tok}"}
    filters = []
    if unread_only:
        filters.append("isRead eq false")
    params: dict[str, Any] = {
        "$top": str(max(1, min(int(limit), 100))),
        "$orderby": "receivedDateTime desc",
        "$select": "id,subject,from,receivedDateTime,bodyPreview,isRead,conversationId",
    }
    if filters:
        params["$filter"] = " and ".join(filters)
    if query:
        params["$search"] = f'"{query}"'
        # $search requires removal of $filter (Graph quirk)
        params.pop("$filter", None)
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(
                f"{GRAPH}/me/mailFolders/Inbox/messages",
                headers=headers,
                params=params,
            )
            if r.status_code >= 400:
                logger.warning("outlook list failed: %s %s", r.status_code, r.text[:200])
                return []
            items = r.json().get("value", []) or []
        out: list[dict] = []
        for m in items:
            sender = (m.get("from") or {}).get("emailAddress", {}) or {}
            out.append(
                {
                    "id": m.get("id"),
                    "thread_id": m.get("conversationId"),
                    "subject": m.get("subject") or "(no subject)",
                    "from": f'{sender.get("name","")} <{sender.get("address","")}>'.strip(" <>"),
                    "from_name": sender.get("name") or sender.get("address") or "Unknown",
                    "date": m.get("receivedDateTime", ""),
                    "snippet": (m.get("bodyPreview") or "")[:200],
                    "label_ids": [],
                    "unread": not m.get("isRead", False),
                }
            )
        return out
    except Exception as exc:
        logger.warning("outlook_messages_structured failed: %s", exc)
        return []


async def outlook_get_full(
    user_id: str, message_id: str, tenant_id: str = "nexgai"
) -> dict:
    tok, err = await _token(user_id, tenant_id)
    if err:
        return {"error": err}
    headers = {"Authorization": f"Bearer {tok}"}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(
                f"{GRAPH}/me/messages/{message_id}",
                headers=headers,
                params={"$select": "subject,from,toRecipients,receivedDateTime,body,bodyPreview"},
            )
            if r.status_code >= 400:
                return {"error": f"Outlook error {r.status_code}"}
            m = r.json()
        sender = (m.get("from") or {}).get("emailAddress", {}) or {}
        body_obj = m.get("body") or {}
        # Strip HTML if needed
        body = body_obj.get("content") or m.get("bodyPreview", "")
        if body_obj.get("contentType") == "html":
            body = re.sub(r"<[^>]+>", " ", body)
            body = re.sub(r"\s+", " ", body).strip()
        return {
            "id": m.get("id"),
            "subject": m.get("subject") or "(no subject)",
            "from": f'{sender.get("name","")} <{sender.get("address","")}>'.strip(" <>"),
            "to": ", ".join(
                f"{r['emailAddress'].get('name','')} <{r['emailAddress'].get('address','')}>"
                for r in m.get("toRecipients", [])
            ),
            "date": m.get("receivedDateTime", ""),
            "snippet": m.get("bodyPreview", ""),
            "body": body[:6000],
            "label_ids": [],
        }
    except Exception as exc:
        return {"error": str(exc)}


async def outlook_mark_read(user_id: str, message_id: str, tenant_id: str = "nexgai") -> str:
    tok, err = await _token(user_id, tenant_id)
    if err:
        return err
    headers = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.patch(
                f"{GRAPH}/me/messages/{message_id}",
                headers=headers,
                json={"isRead": True},
            )
            if r.status_code >= 400:
                return f"Outlook mark-read failed ({r.status_code})"
        return "OK"
    except Exception as exc:
        return f"Outlook mark-read failed: {exc}"


async def outlook_archive(user_id: str, message_id: str, tenant_id: str = "nexgai") -> str:
    """Move the message to the Archive folder."""
    tok, err = await _token(user_id, tenant_id)
    if err:
        return err
    headers = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                f"{GRAPH}/me/messages/{message_id}/move",
                headers=headers,
                json={"destinationId": "archive"},
            )
            if r.status_code >= 400:
                return f"Outlook archive failed ({r.status_code}): {r.text[:200]}"
        return "OK"
    except Exception as exc:
        return f"Outlook archive failed: {exc}"


async def outlook_send(
    user_id: str,
    to: str,
    subject: str,
    body: str,
    tenant_id: str = "nexgai",
) -> str:
    """Send an email via the user's Outlook (Microsoft Graph sendMail)."""
    tok, err = await _token(user_id, tenant_id)
    if err:
        return err
    if not to or not subject:
        return "Cannot send: 'to' and 'subject' are required."
    recipients = [
        {"emailAddress": {"address": addr.strip()}}
        for addr in re.split(r"[,;]", to)
        if addr.strip()
    ]
    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "Text", "content": body or ""},
            "toRecipients": recipients,
        },
        "saveToSentItems": True,
    }
    headers = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.post(f"{GRAPH}/me/sendMail", headers=headers, json=payload)
            if r.status_code >= 400:
                return f"Outlook send failed ({r.status_code}): {r.text[:200]}"
        return f"Email sent to {to}."
    except Exception as exc:
        logger.exception("outlook_send failed")
        return f"Outlook send failed: {exc}"


async def outlook_delete(user_id: str, message_id: str, tenant_id: str = "nexgai") -> str:
    """Delete an Outlook message (moves to Deleted Items, recoverable)."""
    tok, err = await _token(user_id, tenant_id)
    if err:
        return err
    headers = {"Authorization": f"Bearer {tok}"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.delete(f"{GRAPH}/me/messages/{message_id}", headers=headers)
            if r.status_code >= 400:
                return f"Outlook delete failed ({r.status_code}): {r.text[:200]}"
        return "OK"
    except Exception as exc:
        return f"Outlook delete failed: {exc}"


async def outlook_calendar_events(
    user_id: str, days_ahead: int = 7, tenant_id: str = "nexgai"
) -> list[dict]:
    """Read upcoming events from the user's Outlook calendar."""
    tok, err = await _token(user_id, tenant_id)
    if err:
        return []
    days_ahead = max(1, min(int(days_ahead), 366))
    now = datetime.now(timezone.utc)
    end = now + timedelta(days=days_ahead)
    headers = {
        "Authorization": f"Bearer {tok}",
        "Prefer": 'outlook.timezone="UTC"',
    }
    params = {
        "startDateTime": now.isoformat().replace("+00:00", "Z"),
        "endDateTime": end.isoformat().replace("+00:00", "Z"),
        "$select": "id,subject,start,end,location,attendees,webLink",
        "$orderby": "start/dateTime",
        "$top": "250",
    }
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            r = await client.get(
                f"{GRAPH}/me/calendarView", headers=headers, params=params
            )
            if r.status_code >= 400:
                return []
            items = r.json().get("value", []) or []
        def _zulu(s: str) -> str:
            # Graph returns UTC dateTimes as naive strings (no offset) because we
            # send Prefer: outlook.timezone="UTC". Mark them as UTC ('Z') and trim
            # the 7-digit fraction so the browser converts to the user's local time
            # (else "15:00" UTC renders as 15:00 instead of 8:30pm IST).
            if not s:
                return s
            t = s.split("T")[-1]
            if "Z" in t or "+" in t or "-" in t:
                return s
            return s.split(".")[0] + "Z"
        out: list[dict] = []
        for ev in items:
            start = _zulu((ev.get("start") or {}).get("dateTime", ""))
            end_ = _zulu((ev.get("end") or {}).get("dateTime", ""))
            loc = (ev.get("location") or {}).get("displayName", "")
            out.append({
                "id": ev.get("id"),
                "title": ev.get("subject") or "(untitled)",
                "start": start,
                "end": end_,
                "location": loc,
                "html_link": ev.get("webLink", ""),
                "attendee_count": len(ev.get("attendees") or []),
                "all_day": False,
            })
        return out
    except Exception as exc:
        logger.warning("outlook_calendar_events failed: %s", exc)
        return []


async def outlook_search_summary(
    user_id: str, query: str, limit: int = 10, tenant_id: str = "nexgai"
) -> str:
    """LLM-friendly text summary of Outlook search results."""
    msgs = await outlook_messages_structured(
        user_id, query=query, limit=limit, unread_only=False, tenant_id=tenant_id
    )
    if not msgs:
        return f"No Outlook messages found for '{query}'."
    lines = [f"Found {len(msgs)} Outlook message(s) for '{query}':", ""]
    for i, m in enumerate(msgs, 1):
        lines.append(
            f"{i}. {m['subject']}\n   From: {m['from']}\n   Date: {m['date']}\n   Snippet: {m['snippet']}"
        )
    return "\n".join(lines)
