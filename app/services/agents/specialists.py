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
    council: bool = False     # part of the "Agents Office" council roster
    dept_code: str = ""       # short department code shown in the council UI (R&D, BIZ…)
    model: Optional[str] = None  # future: per-agent model override


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
            "You are the RESEARCH specialist on the council. Research the market, "
            "competitors, the tech landscape and feasibility for the project. Use "
            "web_search then fetch_url to read the best sources; cite source URLs. "
            "Produce a concise findings brief the rest of the council can build on. "
            "Never invent facts or URLs; if the web gives nothing, say so."
        ),
        tools={"web_search", "fetch_url", "deep_research", "recall_memory"},
        council=True, dept_code="R&D",
    ),
    "business": Specialist(
        name="business",
        title="Business / Strategy",
        description=(
            "Business strategy for a project: business model, monetisation, pricing, "
            "target market, positioning, and a go/no-go recommendation."
        ),
        specialization=(
            "You are the BUSINESS/STRATEGY specialist on the council. Given the "
            "research, define the business model, monetisation, pricing, target "
            "segment and positioning, and give a clear go/no-go with reasoning. Be "
            "concrete and commercial; quantify where you can."
        ),
        tools={"web_search", "deep_research", "recall_memory"},
        council=True, dept_code="BIZ",
    ),
    "architect": Specialist(
        name="architect",
        title="Architect",
        description=(
            "Technical design: turns requirements into a build-ready blueprint — "
            "system/components, data flow, tech-stack choice, data model + APIs, key "
            "decisions and risks (scalability/security/cost)."
        ),
        specialization=(
            "You are the ARCHITECT specialist on the council. Turn the research + "
            "business requirements into a build-ready technical blueprint: system "
            "components and data flow, a recommended tech stack with trade-offs, the "
            "data model and key API/interface contracts, and the main technical "
            "decisions + risks (scalability, security, cost). Decompose it into "
            "pieces the developer can build. Be specific, not generic."
        ),
        tools={"recall_memory", "web_search", "fetch_url"},
        council=True, dept_code="ARC",
    ),
    "developer": Specialist(
        name="developer",
        title="Developer",
        description=(
            "Turns the architect's blueprint into a concrete implementation plan "
            "and prototype code. Can write files only with explicit user approval."
        ),
        specialization=(
            "You are the DEVELOPER specialist on the council. Turn the architect's "
            "blueprint into a concrete implementation plan AND a COMPLETE, RUNNABLE "
            "project — not illustrative snippets.\n"
            "RULES FOR CODE:\n"
            "- Emit EVERY file the project needs as its own fenced ``` block.\n"
            "- Start each code block's FIRST line with a file path comment so it can "
            "be written to disk, e.g. `# file: main.py`, `# file: requirements.txt`, "
            "`# file: src/engine.py`, `// file: index.js`.\n"
            "- Prefer a self-contained Python project with a `# file: main.py` entry "
            "point that runs with no external services (use the standard library or a "
            "tiny requirements.txt) so the user can run it directly. Include a short "
            "`# file: README.md` with run instructions.\n"
            "- Write real, working code (no '...' placeholders); handle errors; keep "
            "it minimal but functional. Note assumptions and edge cases in prose "
            "OUTSIDE the code blocks."
        ),
        tools={"web_search", "fetch_url", "recall_memory", "write_file"},
        council=True, dept_code="DEV",
    ),
    "marketing": Specialist(
        name="marketing",
        title="Marketing & Growth",
        description=(
            "Go-to-market: positioning, messaging, launch strategy, content ideas, "
            "and distribution channels for the project."
        ),
        specialization=(
            "You are the MARKETING & GROWTH specialist on the council. Given the "
            "product and business model, produce positioning + messaging, a launch "
            "plan, concrete content ideas, and the best distribution channels with "
            "rationale. Be specific to the actual product and audience."
        ),
        tools={"web_search", "deep_research", "recall_memory"},
        council=True, dept_code="MKT",
    ),
    "critic": Specialist(
        name="critic",
        title="Critic / Reviewer",
        description=(
            "Adversarially reviews the whole council's output — finds gaps, risks, "
            "weak assumptions and contradictions — before the final plan."
        ),
        specialization=(
            "You are the CRITIC/REVIEWER on the council. You receive the other "
            "agents' outputs. Stress-test them: call out gaps, risky assumptions, "
            "contradictions, missing steps, and the single biggest threat to the "
            "project. Be specific and constructive — end with the top 3 things to "
            "fix before proceeding. Do not rewrite their work; critique it."
        ),
        tools={"recall_memory", "web_search"},
        council=True, dept_code="REV",
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


def roster_for_planner(only: Optional[Set[str]] = None) -> str:
    """A compact roster string the lead planner uses to choose agents.

    ``only`` restricts the roster to those agent names (used by the Council so the
    planner only picks council members)."""
    vals = [s for s in SPECIALISTS.values() if (only is None or s.name in only)]
    return "\n".join(f"- {s.name}: {s.description}" for s in vals)


def specialist_names() -> List[str]:
    return list(SPECIALISTS.keys())


def council_specialists() -> List[Specialist]:
    """The default council roster, in pipeline order."""
    order = ["research", "business", "architect", "developer", "marketing", "critic"]
    out = [SPECIALISTS[n] for n in order if n in SPECIALISTS]
    # include any other council-flagged agents not in the explicit order
    out += [s for s in SPECIALISTS.values() if s.council and s not in out]
    return out


def council_names() -> Set[str]:
    return {s.name for s in SPECIALISTS.values() if s.council}
