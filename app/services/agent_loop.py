"""Agentic tool-calling loop — the LLM decides what to do to reach a goal.

Instead of hard-coded routing, the model is given a set of tools (web search,
page fetch, email/calendar/drive read, and gated send/create actions) and
iterates: think -> call tool(s) -> observe results -> repeat -> final answer.

Uses the provider's native function-calling (Ollama / OpenAI-compatible) via
``llm_client``. Read tools are always allowed; write tools are gated by the
user's L1-L5 autonomy level (and surfaced to the user when blocked) so the
agent can plan freely but cannot act beyond its permission.

Reliability (small-model safety): a per-turn round budget + wall-clock budget,
tool-name correction, and a one-shot nudge if the model describes an action
instead of calling the tool. Pre-fetched grounding context can be seeded in so
the model often already has what it needs.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from app.agent.connector_tools import (
    calendar_create_event,
    calendar_list_events,
    calendar_update_event,
    drive_search,
    gmail_search,
    gmail_send,
)
from app.agent.harness import Harness, ToolCall as HToolCall
from app.agent.outlook_tools import outlook_search_summary, outlook_send
from app.api.preferences import decide_write_gate
from app.services.harvester_worker import cached_events, cached_messages
from app.services.llm_client import get_llm_client
from app.services.websearch import fetch_page_text, web_search

logger = logging.getLogger(__name__)

MAX_ROUNDS = 6
TIME_BUDGET_S = 100

# --- Tool schemas (OpenAI/Ollama function-calling format) -------------------

TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {"type": "function", "function": {
        "name": "web_search",
        "description": "Search the live web for CURRENT information (news, prices, releases, "
                       "recent events, any fact you are unsure of or that may be newer than your "
                       "training data). Returns titles, URLs and snippets.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "the search query"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "fetch_url",
        "description": "Fetch the readable text of a web page (e.g. a URL returned by web_search) "
                       "to read it in detail before answering.",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {
        "name": "search_email",
        "description": "Search the user's connected email (Gmail/Outlook). Use for questions about "
                       "the user's own messages, senders, unread mail, etc.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Gmail-style query, e.g. 'is:unread' or 'from:priti'"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "list_calendar",
        "description": "List the user's upcoming Google Calendar events.",
        "parameters": {"type": "object", "properties": {
            "days_ahead": {"type": "integer", "description": "how many days ahead (default 7)"}}}}},
    {"type": "function", "function": {
        "name": "recall_contact",
        "description": "Recall what you already know about one of the user's email contacts "
                       "(a person, company, or service) — a precomputed summary of who they are "
                       "and recent topics/asks. Use this for questions like 'what's the latest with "
                       "X?', 'who is X?', or 'do I owe X anything?' about people/companies who email "
                       "the user. Faster and more reliable than re-reading the inbox.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "contact name, company, or email, e.g. 'HDFC' or 'Priti'"}},
            "required": ["name"]}}},
    {"type": "function", "function": {
        "name": "search_drive",
        "description": "Search the user's Google Drive for files/documents.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "send_email",
        "description": "Send an email from the user's account. This is a high-risk action and is "
                       "gated by the user's autonomy level.",
        "parameters": {"type": "object", "properties": {
            "to": {"type": "string"}, "subject": {"type": "string"}, "body": {"type": "string"},
            "provider": {"type": "string", "enum": ["gmail", "outlook"], "description": "default gmail"}},
            "required": ["to", "subject", "body"]}}},
    {"type": "function", "function": {
        "name": "create_calendar_event",
        "description": "Create a Google Calendar event. Gated by autonomy level.",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string"},
            "start": {"type": "string", "description": "ISO-8601 start, e.g. 2026-06-01T14:00:00Z"},
            "duration_min": {"type": "integer"}},
            "required": ["title", "start"]}}},
    {"type": "function", "function": {
        "name": "update_calendar_event",
        "description": (
            "Change an EXISTING calendar event's time and/or how often it repeats. "
            "Use this (NOT create) when the user says 'change/move/reschedule my <meeting>'. "
            "Edits the whole recurring series. Gated by autonomy level."),
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Words from the event title to find it, e.g. 'Daily evening meeting'"},
            "new_time": {"type": "string", "description": "New clock time, e.g. '8:30 PM' or '20:30'. Omit to keep."},
            "recurrence": {"type": "string", "enum": ["daily", "weekdays", "weekly", "weekends", "none"],
                           "description": "How often it repeats. Omit to keep current."},
            "new_title": {"type": "string", "description": "Optional rename."}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "deep_research",
        "description": (
            "Do MULTI-SOURCE web research on a topic and return a synthesized, cited "
            "summary. Use this (instead of web_search) when the user explicitly wants "
            "research, a comparison, or 'with sources'. Slower than web_search; kept "
            "short here — the full report lives on the Research panel."),
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "The research question or topic."}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "recall_memory",
        "description": (
            "Recall what you remember from EARLIER conversations or the user's data "
            "about a topic ('what did we discuss about X', 'remind me what I said'). "
            "Semantic memory across past chats."),
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "What to recall."}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "schedule_research",
        "description": (
            "Set up a RECURRING research watch: re-research a topic on a schedule and "
            "alert the user only when there's a NEW development. Use when the user says "
            "things like 'research X every day and tell me when something new comes up', "
            "'keep an eye on Y', 'watch Z for updates'."),
        "parameters": {"type": "object", "properties": {
            "topic": {"type": "string", "description": "The topic to keep researching."},
            "interval_hours": {"type": "integer", "description": "How often to re-check. Default 24 (daily)."}},
            "required": ["topic"]}}},
    # --- Computer use (see + control the local desktop) --------------------
    {"type": "function", "function": {
        "name": "take_screenshot",
        "description": (
            "Capture the user's screen and get a description of what's visible, plus the "
            "screen size in pixels. ALWAYS call this FIRST before any click/type to see the "
            "current state, and again after an action to verify it worked. Use for "
            "'what's on my screen', 'read my screen', or to begin any on-screen task."),
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "find_on_screen",
        "description": (
            "Locate visible TEXT on screen and get its exact pixel coordinates (x, y) so you "
            "can click it precisely. Call this to find a button/link/menu label before "
            "computer_click. More reliable than guessing coordinates from the description."),
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string", "description": "The on-screen text/label to find, e.g. 'Save' or 'File'."}},
            "required": ["text"]}}},
    {"type": "function", "function": {
        "name": "computer_click",
        "description": (
            "Move the mouse to (x, y) and click. HIGH-RISK: drives the real mouse — gated by "
            "autonomy. Get coordinates from find_on_screen or take_screenshot first."),
        "parameters": {"type": "object", "properties": {
            "x": {"type": "integer"}, "y": {"type": "integer"},
            "button": {"type": "string", "enum": ["left", "right"], "description": "default left"},
            "double": {"type": "boolean", "description": "true to double-click"}},
            "required": ["x", "y"]}}},
    {"type": "function", "function": {
        "name": "computer_type",
        "description": (
            "Type text at the current keyboard focus (click the target field first). "
            "HIGH-RISK: real keyboard input — gated by autonomy."),
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string"}}, "required": ["text"]}}},
    {"type": "function", "function": {
        "name": "computer_key",
        "description": (
            "Press a key or hotkey combo, e.g. 'enter', 'tab', 'esc', 'ctrl+c', 'ctrl+v', "
            "'cmd+space', 'win+r'. HIGH-RISK: gated by autonomy."),
        "parameters": {"type": "object", "properties": {
            "keys": {"type": "string", "description": "Key name or combo joined with '+'."}},
            "required": ["keys"]}}},
    {"type": "function", "function": {
        "name": "computer_scroll",
        "description": "Scroll the screen vertically. Positive = up, negative = down. HIGH-RISK: gated by autonomy.",
        "parameters": {"type": "object", "properties": {
            "amount": {"type": "integer", "description": "Scroll clicks, e.g. 500 (up) or -500 (down)."}},
            "required": ["amount"]}}},
]

_TOOL_NAMES = [t["function"]["name"] for t in TOOL_SCHEMAS]
_READ_TOOLS = {"web_search", "fetch_url", "search_email", "list_calendar", "search_drive",
               "recall_contact", "deep_research", "recall_memory", "schedule_research",
               "take_screenshot", "find_on_screen"}

# Computer-use tools are powerful but easy to misfire (e.g. taking a screenshot
# for a meeting-prep question). We only expose them to the model when the
# request actually mentions seeing/controlling the screen, an app, or the
# desktop — otherwise the model can't pick them at all.
_COMPUTER_TOOLS = {"take_screenshot", "find_on_screen", "computer_click",
                   "computer_type", "computer_key", "computer_scroll"}
_COMPUTER_INTENT = re.compile(
    r"\b(screenshot|screen|my desktop|on screen|what'?s on (my|the) screen|"
    r"take a (screen|pic)|click|double[- ]?click|right[- ]?click|type (this|that|it|into)|"
    r"press (enter|tab|esc|ctrl|cmd|win|the )|scroll|"
    r"open (the |my )?(app|notepad|notes|word|excel|powerpoint|browser|chrome|edge|"
    r"file explorer|explorer|terminal|calculator|settings)|"
    r"launch |computer use|control (my|the) (computer|screen|mouse|keyboard)|"
    r"save it (there|to|in)|paste it|move the mouse)\b",
    re.IGNORECASE,
)


def _tools_for(message: str) -> List[Dict[str, Any]]:
    """Per-turn tool list: drop computer-use tools unless the user asked for them."""
    if _COMPUTER_INTENT.search(message or ""):
        return TOOL_SCHEMAS
    return [t for t in TOOL_SCHEMAS if t["function"]["name"] not in _COMPUTER_TOOLS]


def _system_prompt(user_name: str, email: str, today_iso: str, autonomy_label: str) -> str:
    return (
        f"You are MyAi, the personal AI assistant for {user_name} ({email}). "
        f"Today is {today_iso}. Current autonomy: {autonomy_label}.\n\n"
        "You are an AGENT: given a goal, work out the steps and USE YOUR TOOLS to reach it. "
        "Rules:\n"
        "- For anything current, factual, or that could be newer than your training data "
        "(news, releases, prices, 'latest', research) you MUST call web_search, then fetch_url "
        "to read the most relevant results, before answering. Never answer such questions from "
        "memory and never invent URLs.\n"
        "- For the user's own email/calendar/files, call search_email / list_calendar / search_drive.\n"
        "- For a question about a specific person, company, or service that EMAILS the user "
        "('what's the latest with X', 'what does X want', 'do I owe X anything', 'who is X') call "
        "recall_contact(name) FIRST — it has a precomputed memory of that sender. Use web_search "
        "ONLY for general world information, never for the user's own contacts or inbox.\n"
        "- Chain tools as needed (search -> read -> maybe search again) until you can answer well.\n"
        "- To CHANGE an existing meeting (move its time, make it weekdays-only, rename), call "
        "update_calendar_event — do NOT create_calendar_event, which would make duplicates. "
        "create_calendar_event is only for brand-new events. For 'every weekday' pass recurrence='weekdays'.\n"
        "- ONLY use the computer-use tools when the user EXPLICITLY asks you to look at their "
        "screen or to open/click/type in an app. NEVER take a screenshot for an email, calendar, "
        "meeting-prep, research, or general question — answer those from your other tools/knowledge.\n"
        "- To SEE or CONTROL the user's computer (open an app, click a button, type into a field, "
        "press keys), use the computer-use tools: take_screenshot to see, find_on_screen('label') "
        "to get exact click coordinates, then computer_click / computer_type / computer_key / "
        "computer_scroll. ALWAYS take_screenshot first to see the current state, act, then "
        "take_screenshot again to verify. Mouse/keyboard control is high-risk and only runs at "
        "autonomy L5 — if it's blocked, tell the user to raise the slider to L5.\n"
        "- This is a WINDOWS PC. To open an app (Notepad, Word, Chrome, etc.): computer_key('win'), "
        "then computer_type('<app name>'), then computer_key('enter') — wait, take_screenshot to "
        "confirm it opened, then act. To save in Notepad/Word use computer_key('ctrl+s') then type "
        "the filename and computer_key('enter'). For a task like 'research X and save it to Notes', "
        "FIRST call deep_research to get the text, THEN open the app and computer_type that text.\n"
        "- When done, write a clear, well-structured answer and cite the source URLs you used.\n"
        "- NEVER fabricate emails, events, facts, names, dates, or sources. NEVER claim you did "
        "something (sent, scheduled, changed) unless the tool result actually confirmed success — "
        "if a tool returned an error or nothing, say so honestly.\n"
        "- Respect autonomy: if a write action (send_email, create/update_calendar_event) is blocked, tell "
        "the user what you would do and that they need to confirm or raise the autonomy level."
    )


_LOW_PRIORITY_HINTS = (
    "promo", "promotion", "newsletter", "low priorit", "low-priorit",
    "unimportant", "junk", "marketing", "spam", "declutter", "clutter",
    "archive", "subscription",
)


async def _email_from_cache(user, query: str, limit: int = 12) -> str:
    """Search the harvester cache (the same data the Inbox page shows).

    This is the source of truth the user sees, so 'list my promotional emails'
    returns exactly what the inbox holds — and it works regardless of live token
    scope. Falls back to live Gmail only when the cache is empty.
    """
    try:
        msgs = await cached_messages(user.sub, user.tenant_id)
    except Exception:
        msgs = []
    if not msgs:
        # Nothing harvested yet → live search so the agent still has an answer.
        return await gmail_search(user.sub, query or "is:unread", 10, user.tenant_id)

    q = (query or "").lower()
    want_low = any(h in q for h in _LOW_PRIORITY_HINTS)
    terms = [t for t in re.findall(r"[a-z0-9]+", q) if len(t) > 2
             and t not in ("the", "and", "for", "with", "what", "which", "tell",
                           "list", "show", "email", "emails", "mail", "mails", "inbox")]

    def _match(m: dict) -> bool:
        if want_low and (m.get("priority") == "low"):
            return True
        if not terms:
            return not want_low  # generic ask → show everything
        hay = f"{m.get('subject','')} {m.get('from','')} {m.get('from_name','')} {m.get('snippet','')}".lower()
        return any(t in hay for t in terms)

    hits = [m for m in msgs if _match(m)] or (msgs if not terms and not want_low else [])
    if not hits:
        return (f"No emails in your inbox match that. You have {len(msgs)} cached "
                f"message(s) total — try a different term.")
    hits = hits[:limit]
    lines = [f"Found {len(hits)} matching email(s) in your inbox:", ""]
    for i, m in enumerate(hits, 1):
        lines.append(
            f"{i}. {m.get('subject','(no subject)')}  [priority: {m.get('priority','?')}]\n"
            f"   From: {m.get('from_name') or m.get('from','unknown')}  ({m.get('account','')})\n"
            f"   Date: {m.get('date','')}\n"
            f"   {(m.get('snippet','') or '')[:160]}"
        )
    return "\n".join(lines)


async def _calendar_from_cache(user, days_ahead: int) -> str:
    """Read events from the harvester cache (7-day window, all accounts).

    Avoids the live noon-to-noon window bug that dropped tomorrow-evening events.
    The model filters by date itself, so we hand it the full upcoming window.
    """
    try:
        events = await cached_events(user.sub, user.tenant_id)
    except Exception:
        events = []
    if not events:
        return await calendar_list_events(user.sub, max(days_ahead, 7), user.tenant_id)
    lines = [f"Your upcoming events ({len(events)} in the next ~7 days, "
             f"each with its date — filter to what the user asked for):", ""]
    for ev in events:
        when = ev.get("start", "?")
        loc = f" @ {ev['location']}" if ev.get("location") else ""
        att = f" — {ev['attendee_count']} attendee(s)" if ev.get("attendee_count") else ""
        lines.append(f"- {when}: {ev.get('title','(untitled)')}{loc}{att} ({ev.get('account','')})")
    return "\n".join(lines)


async def _dispatch(name: str, args: Dict[str, Any], *, user, autonomy_level: int) -> str:
    """Execute a tool call and return a string result for the model."""
    try:
        if name == "web_search":
            results = await web_search(args.get("query", ""), max_results=6)
            if not results:
                return "No web results found."
            return "\n".join(
                f"[{i}] {r['title']}\n    {r['url']}\n    {r['snippet']}"
                for i, r in enumerate(results, 1)
            )
        if name == "fetch_url":
            text = await fetch_page_text(args.get("url", ""), max_chars=3500)
            return text or "Could not fetch that page."
        if name == "deep_research":
            # Capped single-round run so it fits the chat turn's time budget; the
            # full multi-round experience lives on the dedicated /research endpoint.
            from app.services.deep_research import run_deep_research
            # Tight time budget so an in-chat research call fits the agent's own
            # TIME_BUDGET_S and doesn't hang the turn; the full multi-round run
            # lives on the dedicated /research endpoint.
            r = await run_deep_research(args.get("query", ""), max_rounds=1, time_budget=60.0)
            if not r.sources:
                return "Deep research found no web sources right now."
            srcs = "\n".join(f"- {s['title']}: {s['url']}" for s in r.sources[:8])
            return f"{r.report[:3500]}\n\nSources:\n{srcs}"
        if name == "recall_memory":
            from app.services.semantic_memory import recall_semantic
            hits = await recall_semantic(user.sub, user.tenant_id, args.get("query", ""), k=4)
            if not hits:
                return "I don't have any earlier memory about that yet."
            return "\n".join(f"- {h['text'][:300]}" for h in hits)
        if name == "schedule_research":
            from app.services.research_scheduler import create_schedule
            topic = args.get("topic", "")
            hours = int(args.get("interval_hours", 24) or 24)
            sid = await create_schedule(user.sub, user.tenant_id, topic, hours)
            if not sid:
                return "Couldn't set up the research watch (no topic given)."
            every = "day" if hours == 24 else (f"{hours} hours" if hours != 1 else "hour")
            return (f"Done — I'll research '{topic}' every {every} and alert you on the "
                    f"Dashboard whenever there's a new development. The first baseline "
                    f"run starts shortly. (watch id {sid})")
        if name == "search_email":
            return await _email_from_cache(user, args.get("query", ""))
        if name == "list_calendar":
            return await _calendar_from_cache(user, int(args.get("days_ahead", 7) or 7))
        if name == "search_drive":
            return await drive_search(user.sub, args.get("query", ""), 8, user.tenant_id)
        if name == "recall_contact":
            from app.services.harvester_worker import recall_contacts
            res = await recall_contacts(user.sub, user.tenant_id, args.get("name", ""))
            if not res:
                return "No memory about that contact yet (they may not have emailed recently)."
            return "\n\n".join(
                f"{c['name']} <{c['addr']}> — {c['message_count']} recent emails, "
                f"last {c['last_date']}:\n{c['summary']}" for c in res
            )
        if name == "send_email":
            allowed, needs_conf, reason = decide_write_gate(autonomy_level, "send", False)
            if not allowed:
                return (f"BLOCKED ({reason}). Did NOT send. "
                        + ("Ask the user to confirm sending." if needs_conf
                           else "Tell the user to raise the autonomy slider above L1."))
            provider = args.get("provider", "gmail")
            fn = outlook_send if provider == "outlook" else gmail_send
            return await fn(user.sub, args.get("to", ""), args.get("subject", ""), args.get("body", ""), user.tenant_id)
        if name == "create_calendar_event":
            allowed, needs_conf, reason = decide_write_gate(autonomy_level, "schedule", False)
            if not allowed:
                return f"BLOCKED ({reason}). Did NOT create the event. Ask the user to confirm or raise autonomy."
            return await calendar_create_event(
                user.sub, args.get("title", ""), args.get("start", ""),
                int(args.get("duration_min", 30) or 30), None, None, user.tenant_id)
        if name == "update_calendar_event":
            allowed, needs_conf, reason = decide_write_gate(autonomy_level, "schedule", False)
            if not allowed:
                return f"BLOCKED ({reason}). Did NOT change the event. Ask the user to confirm or raise autonomy."
            return await calendar_update_event(
                user.sub, args.get("query", ""),
                new_time=args.get("new_time") or None,
                recurrence=args.get("recurrence") if args.get("recurrence") not in ("", None) else None,
                new_title=args.get("new_title") or None,
                tenant_id=user.tenant_id)

        # --- Computer use ---------------------------------------------------
        if name in ("take_screenshot", "find_on_screen", "computer_click",
                    "computer_type", "computer_key", "computer_scroll"):
            from app.services import computer_use as cu
            if not cu.is_enabled():
                return f"BLOCKED. {cu.unavailable_reason()}"
            w, h = cu.screen_size()

            # Read-only observe tools — allowed at any level.
            if name == "take_screenshot":
                from app.services.vision import describe_image
                png = await cu.capture_png()
                small = cu.downscale_png(png)
                desc, provider = await describe_image(
                    small, media_type="image/png",
                    prompt=("This is a screenshot of the user's desktop. Describe what app/window "
                            "is in focus and list the visible buttons, fields, menus, text and any "
                            "errors — be specific so an agent can decide where to click next."),
                )
                return (f"Screen is {w}x{h} px (x:0..{w-1} left-to-right, y:0..{h-1} top-to-bottom).\n"
                        f"What's visible (via {provider}):\n{desc}\n\n"
                        f"To click something, prefer find_on_screen('label') for exact coordinates.")
            if name == "find_on_screen":
                hits = await cu.find_on_screen(args.get("text", ""))
                if hits is None:
                    return ("On-screen text search needs OCR (tesseract), which isn't installed. "
                            "Use take_screenshot and estimate coordinates from the description instead.")
                if not hits:
                    return f"No on-screen text matching '{args.get('text','')}'. Try take_screenshot to see what's there."
                return "Matches (click these x,y):\n" + "\n".join(
                    f"- '{h['text']}' at ({h['x']}, {h['y']})  conf={h['conf']}" for h in hits)

            # Mutating control tools — HIGH-RISK, gated by the autonomy dial.
            allowed, needs_conf, reason = decide_write_gate(autonomy_level, "control", False)
            if not allowed:
                hint = ("Ask the user to confirm, or raise the autonomy slider to L5 (Autonomous)."
                        if needs_conf else "Tell the user to raise the autonomy slider — computer "
                        "control is off below L5.")
                return f"BLOCKED ({reason}). Did NOT touch the mouse/keyboard. {hint}"
            if name == "computer_click":
                cx, cy = await cu.click(
                    int(args.get("x", 0)), int(args.get("y", 0)),
                    button=args.get("button", "left"),
                    clicks=2 if args.get("double") else 1)
                return f"Clicked at ({cx}, {cy}). Call take_screenshot to confirm the result."
            if name == "computer_type":
                await cu.type_text(args.get("text", ""))
                return f"Typed {len(str(args.get('text','')))} character(s) at the current focus."
            if name == "computer_key":
                await cu.press_key(args.get("keys", ""))
                return f"Pressed '{args.get('keys','')}'."
            if name == "computer_scroll":
                await cu.scroll(int(args.get("amount", 0)))
                return f"Scrolled {args.get('amount', 0)}."
    except Exception as exc:  # noqa: BLE001
        logger.warning("tool %s failed: %s", name, exc)
        return f"Tool error: {exc}"
    return f"Unknown tool: {name}"


def _correct_name(name: str) -> Optional[str]:
    if name in _TOOL_NAMES:
        return name
    low = (name or "").lower().replace("-", "_")
    for n in _TOOL_NAMES:
        if n in low or low in n:
            return n
    return None


async def run_agent(
    message: str,
    history: List[Dict[str, str]],
    *,
    user,
    autonomy_label: str,
    autonomy_level: int,
    today_iso: str,
    seed_context: str = "",
    extra_system: str = "",
    allowed_tools: Optional[set] = None,
) -> Tuple[str, List[str]]:
    """Run the tool-calling loop. Returns (final_answer, tools_used).

    Specialist mode: pass ``extra_system`` (a role specialization appended to the
    base prompt) and ``allowed_tools`` (an explicit tool-name subset) to run the
    same loop as a focused sub-agent. When ``allowed_tools`` is given it is used
    verbatim (the orchestrator chose this specialist on purpose), bypassing the
    auto-hide of computer-use tools.
    """
    llm = get_llm_client()
    msgs: List[Dict[str, Any]] = [
        {"role": "system", "content": _system_prompt(
            user.full_name or user.username or user.email, user.email, today_iso, autonomy_label)},
    ]
    if extra_system:
        msgs.append({"role": "system", "content": extra_system})
    if seed_context:
        msgs.append({"role": "system", "content": seed_context})
    for m in history[-12:]:
        if m.get("role") in ("user", "assistant", "system"):
            msgs.append({"role": m["role"], "content": m["content"]})
    msgs.append({"role": "user", "content": message})

    # Life-Harness H3 (task context) + H5 (procedural + learned skills): inject
    # a hint block so the agent starts with the right tools and reuses skills it
    # learned from past successful turns.
    harness = Harness()
    hints = harness.prepare_turn(message)
    if hints.get("system_injection"):
        msgs.insert(-1, {"role": "system", "content": hints["system_injection"]})

    tools_used: List[str] = []
    nudged = False
    t0 = time.monotonic()
    content = ""
    if allowed_tools is not None:
        # Specialist: use exactly the chosen subset (computer-use included if listed).
        turn_tools = [t for t in TOOL_SCHEMAS if t["function"]["name"] in allowed_tools]
    else:
        turn_tools = _tools_for(message)  # hide computer-use tools unless asked for

    def _is_err(r: str) -> bool:
        rl = (r or "").lower()[:90]
        return any(k in rl for k in (
            "tool error", "blocked", "could not", "no web results",
            "not connected", "haven't connected", "unknown tool"))

    async def _synthesize() -> str:
        """Force a final tool-free answer from gathered context."""
        msgs.append({"role": "user", "content":
                     "Stop using tools now. Using only the information gathered above, "
                     "give the user a clear, complete answer and cite the source URLs you used."})
        try:
            r = await llm.chat(msgs, temperature=0.3)
            return ((r.get("message", {}) or {}).get("content") or "").strip()
        except Exception:
            return ""

    for _round in range(MAX_ROUNDS):
        if time.monotonic() - t0 > TIME_BUDGET_S:
            break
        # H4 (trajectory regulation): if a failure pattern (loop/empty/errors)
        # was detected, inject a recovery hint before the next model call.
        if _round > 0:
            step = harness.get_step_injection()
            if step:
                msgs.append({"role": "user", "content": step})
        try:
            resp = await llm.chat(msgs, tools=turn_tools, temperature=0.4)
        except Exception as exc:
            logger.warning("agent llm call failed: %s", exc)
            return (content or "I couldn't reach the model. Please try again."), tools_used
        m = resp.get("message", {}) or {}
        content = (m.get("content") or "").strip()
        calls = m.get("tool_calls") or []

        if not calls:
            # No tool call. If the model promised to search but didn't, nudge once.
            if not nudged and not tools_used and any(
                w in content.lower() for w in ("i will search", "let me search", "i'll look", "searching", "i can research")
            ):
                nudged = True
                msgs.append({"role": "assistant", "content": content})
                msgs.append({"role": "user", "content":
                             "Actually call the web_search tool now — don't just say you will."})
                continue
            return content or "I wasn't able to produce an answer.", tools_used

        msgs.append({"role": "assistant", "content": content, "tool_calls": calls})
        for tc in calls:
            fn = tc.get("function", {}) if isinstance(tc, dict) else {}
            raw_name = fn.get("name", "")
            name = _correct_name(raw_name) or raw_name
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args) if args else {}
                except Exception:
                    args = {}
            tools_used.append(name)
            result = await _dispatch(name, args, user=user, autonomy_level=autonomy_level)
            result_str = str(result)[:6000]
            harness.record_tool_result(
                HToolCall(name=name, args=args if isinstance(args, dict) else {}),
                result_str, _is_err(result_str),
            )
            msgs.append({"role": "tool", "content": result_str})

        # H4 budget forcing: enough rounds of tool work → synthesize and stop.
        if harness.trajectory.should_force_output():
            final = await _synthesize()
            return (final or content or "Here is what I found."), tools_used

    # Ran out of rounds/time mid-tool-use → force a final synthesized answer.
    final = await _synthesize()
    return (final or content
            or "I gathered some information but ran out of steps before finishing. "
               "Try narrowing the request."), tools_used
