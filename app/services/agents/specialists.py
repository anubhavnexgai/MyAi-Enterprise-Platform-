"""Specialist sub-agents — focused roles the lead orchestrator can delegate to.

Each specialist is a thin config over the shared tool-calling loop
(``agent_loop.run_agent``): a ``specialization`` prompt that sharpens its role and
an ``allowed_tools`` subset so it only reaches for the right tools. Keeping them
config-only (not separate agent classes) means every specialist inherits the
Life-Harness reliability + autonomy gating already in ``run_agent``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple


@dataclass(frozen=True)
class Specialist:
    name: str
    title: str
    description: str          # shown to the lead planner to pick the right agent
    specialization: str       # appended to the base system prompt
    tools: Set[str]


# --- Roster -----------------------------------------------------------------

SPECIALISTS: Dict[str, Specialist] = {
    "research": Specialist(
        name="research",
        title="Research",
        description=(
            "Researches topics on the live web, reads sources, and produces "
            "fact-checked, cited findings. Use for 'find out / look up / compare / "
            "latest / what is X' about the world (not the user's own data)."
        ),
        specialization=(
            "You are the RESEARCH specialist. Use web_search then fetch_url to read "
            "the best sources before answering. Cite the source URLs. Never invent "
            "facts or URLs; if the web gives nothing, say so."
        ),
        tools={"web_search", "fetch_url", "deep_research", "recall_memory"},
    ),
    "email": Specialist(
        name="email",
        title="Email",
        description=(
            "Handles the user's email: finds messages, identifies what needs a "
            "reply, drafts replies, and (if autonomy permits) sends them."
        ),
        specialization=(
            "You are the EMAIL specialist. Use search_email to read the user's "
            "inbox and recall_contact for who a sender is. Draft clearly; only send "
            "with send_email and never claim you sent something unless the tool "
            "confirmed it."
        ),
        tools={"search_email", "recall_contact", "send_email", "recall_memory"},
    ),
    "calendar": Specialist(
        name="calendar",
        title="Calendar",
        description=(
            "Manages the user's calendar: lists upcoming events, schedules and "
            "reschedules meetings, and prepares the user for meetings."
        ),
        specialization=(
            "You are the CALENDAR specialist. Use list_calendar to see events; "
            "create_calendar_event for new ones and update_calendar_event to change "
            "an existing meeting (never create a duplicate). Respect autonomy gates."
        ),
        tools={"list_calendar", "create_calendar_event", "update_calendar_event",
               "recall_contact"},
    ),
    "knowledge": Specialist(
        name="knowledge",
        title="Knowledge & Files",
        description=(
            "Finds and summarizes the user's files/documents (Drive) and recalls "
            "context from past conversations and contacts. Use for 'find my doc / "
            "what did we discuss / who is X'."
        ),
        specialization=(
            "You are the KNOWLEDGE specialist. Use search_drive for files, "
            "recall_memory for past-conversation context, and recall_contact for "
            "people the user works with. Summarize tightly and cite what you used."
        ),
        tools={"search_drive", "recall_memory", "recall_contact", "search_email"},
    ),
    "coding": Specialist(
        name="coding",
        title="Coding",
        description=(
            "Software engineering: writes, explains, reviews, refactors, and debugs "
            "code; designs technical solutions, scripts, and tests. Use for any "
            "'write code / fix this bug / how do I implement / review this' request."
        ),
        specialization=(
            "You are the CODING specialist — a senior software engineer. Write "
            "correct, clean, runnable code with a brief explanation and minimal "
            "usage example. When debugging, state the root cause then give the "
            "fixed code. Prefer standard libraries; note assumptions and edge cases. "
            "Use web_search/fetch_url to verify current APIs/syntax when unsure. "
            "Never claim code runs unless you've reasoned through it; format code in "
            "fenced ``` blocks with a language tag."
        ),
        tools={"web_search", "fetch_url", "recall_memory"},
    ),
    "computer": Specialist(
        name="computer",
        title="Computer Use",
        description=(
            "Sees and controls the user's desktop to operate apps — take a "
            "screenshot, find on-screen text, click, type, and press keys. Use ONLY "
            "when the user explicitly wants something done on their screen/an app."
        ),
        specialization=(
            "You are the COMPUTER-USE specialist. ALWAYS take_screenshot first to "
            "see the current state; use find_on_screen('label') for exact click "
            "coordinates; then computer_click/type/key/scroll; screenshot again to "
            "verify. This is a Windows PC (win key -> type app name -> enter to open "
            "an app). High-risk: only runs at autonomy L5."
        ),
        tools={"take_screenshot", "find_on_screen", "computer_click",
               "computer_type", "computer_key", "computer_scroll"},
    ),
}


def get_specialist(name: str) -> Optional[Specialist]:
    return SPECIALISTS.get((name or "").strip().lower())


def roster_for_planner() -> str:
    """A compact roster string the lead planner uses to choose agents."""
    return "\n".join(f"- {s.name}: {s.description}" for s in SPECIALISTS.values())


def specialist_names() -> List[str]:
    return list(SPECIALISTS.keys())
