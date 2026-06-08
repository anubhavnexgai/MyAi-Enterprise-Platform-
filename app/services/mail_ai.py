"""Shared email-triage suggestion generator.

Used both by the background harvester (to pre-compute suggestions so the inbox
is instant) and by the on-demand /api/copilot/suggest fallback. One tool-free
LLM call grounded only in the supplied email text.
"""

from __future__ import annotations

import logging
import re
from typing import Optional, Tuple

from app.services.llm_client import get_llm_client

logger = logging.getLogger(__name__)

_SYS = (
    "You are triaging the user's email. You are given ONE email. In 2-3 short "
    "sentences, say what it is and the single best next action (reply, archive, "
    "schedule, pay, ignore, unsubscribe, etc.). Be concrete and brief. Base it "
    "ONLY on the email text — never invent facts. If it is promotional or "
    "automated, say so and suggest archiving. End with a final line exactly like "
    "'ACTION: <reply|archive|schedule|pay|ignore|none>'."
)

_ACTIONS = {"reply", "archive", "schedule", "pay", "ignore", "unsubscribe", "none"}


async def generate_mail_suggestion(
    subject: str, sender: str, body: str
) -> Tuple[str, Optional[str]]:
    """Return (suggestion_text, action) for one email. Fail-soft -> ('', None)."""
    user_msg = f"From: {sender}\nSubject: {subject}\n\n{(body or '')[:2500]}"
    try:
        result = await get_llm_client().chat(
            [{"role": "system", "content": _SYS}, {"role": "user", "content": user_msg}],
            temperature=0.2, max_tokens=180,
        )
        text = ((result or {}).get("message") or {}).get("content", "") or ""
    except Exception as exc:  # noqa: BLE001
        logger.debug("mail suggestion failed: %s", exc)
        return "", None

    action: Optional[str] = None
    m = re.search(r"ACTION:\s*([a-z]+)", text, re.I)
    if m and m.group(1).lower() in _ACTIONS:
        action = m.group(1).lower()
    text = re.sub(r"\n?ACTION:\s*[a-z]+\s*$", "", text, flags=re.I).strip()
    return text, action


_CONTACT_SYS = (
    "You build a short memory note about one of the user's email contacts, from "
    "a list of recent emails (subjects + previews) from that sender. In 2-4 "
    "sentences capture: who/what this sender is (person, company, service), the "
    "recurring topics, and anything currently needs the user's action or a reply. "
    "Be factual and concise. Use ONLY the provided emails — do not invent. No preamble."
)


async def summarize_contact(sender: str, emails: list[dict]) -> str:
    """Summarize a sender from their recent emails. Fail-soft -> ''."""
    if not emails:
        return ""
    lines = []
    for e in emails[:20]:
        lines.append(f"- [{e.get('date','')}] {e.get('subject','(no subject)')}: {(e.get('snippet') or '')[:160]}")
    user_msg = f"Sender: {sender}\nRecent emails:\n" + "\n".join(lines)
    try:
        result = await get_llm_client().chat(
            [{"role": "system", "content": _CONTACT_SYS}, {"role": "user", "content": user_msg}],
            temperature=0.2, max_tokens=220,
        )
        return (((result or {}).get("message") or {}).get("content", "") or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.debug("contact summary failed: %s", exc)
        return ""
