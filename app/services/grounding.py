"""Correctness spine: Ground-or-Abstain + Verify (Pillar 1).

The copilot chat path (``app/api/copilot.py``) already does retrieval-first
grounding — it fetches the user's real Gmail/Outlook/Calendar/Drive data and
injects it as a context block — and scrubs bracketed placeholders. What it does
NOT do is check that the model's answer is actually *supported* by that context.
A small local model can still confidently invent a sender, a meeting time, or a
subject line that never appears in the fetched data.

This module closes that hole with three pieces, all fail-open (a verifier error
never breaks chat — it just yields an ``unverified`` verdict):

1. ``grounding_need``     — does this turn require grounding in the user's data?
2. ``verify_grounding``   — a cheap second LLM pass that flags claims unsupported
                            by the provided context.
3. ``build_citations`` /  — surface which connected sources backed the answer and
   ``apply_verdict``        append an honest caveat when claims can't be verified.

``ground_and_verify`` ties them together so the API calls one function.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Intent: does this turn need grounding in the user's real data?
# ---------------------------------------------------------------------------
# Mirrors the intent hints in copilot.py::_gather_grounding. Kept here as the
# canonical definition so both the live path and the eval harness agree.

_DATA_INTENT_RE = re.compile(
    r"\b("
    r"email|emails|inbox|unread|mail|message|messages|reply|gmail|outlook|"
    r"sender|subject|thread|threads|notification|"
    r"calendar|meeting|meetings|schedule|today|tomorrow|week|event|events|"
    r"agenda|free|busy|appointment|invite|invites|standup|sync|call|conflict|"
    r"drive|file|files|doc|docs|document|sheet|sheets|slide|slides|pdf|folder|prd|"
    r"draft|drafting|status update|sprint update|weekly update|recap|summary|summarize"
    r")\b",
    re.I,
)


def grounding_need(message: str) -> bool:
    """True if the message asks about the user's own data (email/calendar/files).

    Pure knowledge questions ("what is RAG?") and creative asks ("write a
    haiku") return False — they don't need to be grounded in connector data and
    so should not pay the verification cost or trigger abstention.
    """
    if not message:
        return False
    return bool(_DATA_INTENT_RE.search(message))


# ---------------------------------------------------------------------------
# Verdict model
# ---------------------------------------------------------------------------

# Status values (most-trustworthy first):
#   not_required — turn didn't need grounding; nothing to verify
#   grounded     — every specific data claim is supported by the context
#   no_context   — grounding was needed but no connector data was available
#   partial      — some specific claims could not be verified
#   ungrounded   — claims are unsupported by the provided context
#   unverified   — the verifier could not run (fail-open); treat with caution
_DATA_STATUSES = {"grounded", "no_context", "partial", "ungrounded", "unverified"}


@dataclass
class Verdict:
    status: str = "not_required"
    unsupported: List[str] = field(default_factory=list)
    checked: bool = False  # did the LLM verifier actually run?

    @property
    def is_trustworthy(self) -> bool:
        return self.status in ("not_required", "grounded")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "unsupported": self.unsupported,
            "checked": self.checked,
        }


# ---------------------------------------------------------------------------
# Citations (block-level — honest and reliable for small models)
# ---------------------------------------------------------------------------

# Maps the internal context-block labels used by copilot.py to display names.
_BLOCK_LABELS = {
    "GMAIL_INBOX": "Gmail",
    "GMAIL_RECENT_SENT": "Gmail (sent)",
    "OUTLOOK_INBOX": "Outlook",
    "CALENDAR_UPCOMING": "Google Calendar",
    "DRIVE_RESULTS": "Google Drive",
    "WEB_SEARCH_RESULTS": "Web",
    "CONTACT_MEMORY": "Contact memory",
    "SEMANTIC_MEMORY": "Memory",
}


def build_citations(
    grounding_blocks: Dict[str, str],
    sources_used: Optional[List[str]] = None,
) -> List[Dict[str, str]]:
    """Build block-level citations from the context blocks that were injected.

    Claim-level citation is unreliable with a 7B model, so we cite at the
    granularity we can stand behind: which connected source supplied the data.
    """
    cites: List[Dict[str, str]] = []
    for label in grounding_blocks:
        cites.append({"source": _BLOCK_LABELS.get(label, label), "label": label})
    if not cites and sources_used:
        cites = [{"source": s, "label": s} for s in sources_used]
    return cites


# ---------------------------------------------------------------------------
# Verifier
# ---------------------------------------------------------------------------

_VERIFIER_SYSTEM = (
    "You are a strict grounding verifier. You are given CONTEXT (the only facts "
    "known to be true about the user's emails, calendar, files and data) and an "
    "ANSWER produced by an assistant. Decide whether every SPECIFIC factual claim "
    "in the ANSWER about the user's data is directly supported by the CONTEXT. "
    "General knowledge, advice, greetings, and clearly generic phrasing do NOT "
    "need support. Names, email subjects, senders, dates, times, and counts DO. "
    'Respond with ONLY a JSON object of the exact form '
    '{"grounded": true|false, "unsupported": ["<short claim>", ...]}. '
    "If every specific claim is supported (or the answer makes no specific data "
    "claims), return grounded=true and unsupported=[]. No prose, only JSON."
)

_VERIFIER_TEMPLATE = "CONTEXT:\n{context}\n\nANSWER:\n{answer}\n\nJSON verdict:"

# Conservative cap so the verifier pass stays cheap.
_VERIFY_MAX_TOKENS = 300


def _extract_json_obj(text: str) -> Optional[Dict[str, Any]]:
    """Pull the first balanced {...} object out of model output and parse it.

    Tolerant of code fences, trailing prose, single quotes and trailing commas.
    """
    if not text:
        return None
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if esc:
            esc = False
            continue
        if ch == "\\" and in_str:
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                blob = text[start : i + 1]
                for candidate in (blob, re.sub(r",\s*([}\]])", r"\1", blob)):
                    try:
                        return json.loads(candidate)
                    except (json.JSONDecodeError, ValueError):
                        continue
                try:
                    return json.loads(re.sub(r",\s*([}\]])", r"\1", blob).replace("'", '"'))
                except (json.JSONDecodeError, ValueError):
                    return None
    return None


async def verify_grounding(
    message: str,
    answer: str,
    context_text: str,
    *,
    llm: Any = None,
    enabled: bool = True,
) -> Verdict:
    """Run the cheap verifier pass. Fail-open: never raises.

    - If grounding isn't needed for this message  -> Verdict(not_required).
    - If grounding was needed but no context found -> Verdict(no_context).
    - If verification is disabled or errors        -> Verdict(unverified).
    - Otherwise returns grounded / ungrounded with the unsupported claims.
    """
    if not grounding_need(message):
        return Verdict(status="not_required")

    if not context_text or not context_text.strip():
        # Needed grounding, but nothing was available to ground against. The
        # system prompt already instructs the model to tell the user to connect
        # the relevant account, so we don't rewrite — we just label it.
        return Verdict(status="no_context")

    if not enabled or not answer or not answer.strip():
        return Verdict(status="unverified")

    try:
        if llm is None:
            from app.services.llm_client import get_llm_client

            llm = get_llm_client()
        result = await llm.chat(
            [
                {"role": "system", "content": _VERIFIER_SYSTEM},
                {
                    "role": "user",
                    "content": _VERIFIER_TEMPLATE.format(
                        context=context_text[:8000], answer=answer[:4000]
                    ),
                },
            ],
            temperature=0.0,
            max_tokens=_VERIFY_MAX_TOKENS,
        )
        raw = ((result or {}).get("message") or {}).get("content", "") or ""
    except Exception as exc:  # noqa: BLE001 — fail-open by design
        logger.warning("grounding verifier call failed (fail-open): %s", exc)
        return Verdict(status="unverified")

    obj = _extract_json_obj(raw)
    if obj is None:
        logger.info("grounding verifier returned unparseable output (fail-open)")
        return Verdict(status="unverified")

    grounded = bool(obj.get("grounded", True))
    unsupported_raw = obj.get("unsupported") or []
    unsupported = [str(c).strip() for c in unsupported_raw if str(c).strip()][:8]

    if grounded or not unsupported:
        return Verdict(status="grounded", checked=True)
    return Verdict(status="ungrounded", unsupported=unsupported, checked=True)


# ---------------------------------------------------------------------------
# Enforcement
# ---------------------------------------------------------------------------

_CAVEAT_HEADER = (
    "\n\n---\n⚠️ I couldn't verify the following against your connected data — "
    "please double-check before relying on it:"
)


def apply_verdict(answer: str, verdict: Verdict) -> str:
    """Append an honest transparency caveat when claims couldn't be verified.

    We only annotate (never silently delete content): trust comes from the user
    seeing *what* is unverified, not from the assistant hiding it.
    """
    if verdict.status == "ungrounded" and verdict.unsupported:
        bullets = "\n".join(f"  - {c}" for c in verdict.unsupported)
        return f"{answer}{_CAVEAT_HEADER}\n{bullets}"
    return answer


async def ground_and_verify(
    message: str,
    answer: str,
    grounding_blocks: Dict[str, str],
    sources_used: Optional[List[str]] = None,
    *,
    llm: Any = None,
    enabled: bool = True,
) -> Tuple[str, Verdict, List[Dict[str, str]]]:
    """One-call helper for the API: verify, annotate, and build citations.

    Returns ``(final_answer, verdict, citations)``.
    """
    context_text = "\n\n".join(grounding_blocks.values()) if grounding_blocks else ""
    verdict = await verify_grounding(
        message, answer, context_text, llm=llm, enabled=enabled
    )
    final_answer = apply_verdict(answer, verdict)
    citations = build_citations(grounding_blocks, sources_used)
    return final_answer, verdict, citations
