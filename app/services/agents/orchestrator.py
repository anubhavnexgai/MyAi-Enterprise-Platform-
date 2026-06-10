"""Lead orchestrator — decompose a goal, run specialists in parallel, synthesize.

Pattern (ported from EAP's module_09 orchestrator + parallel_agent_executor):
  1. PLAN   — an LLM planner breaks the goal into 1-4 sub-tasks, each assigned to
              one specialist, with explicit dependencies.
  2. EXECUTE— independent sub-tasks run concurrently (asyncio.gather, capped by a
              semaphore); dependent ones get their inputs as seed context.
  3. SYNTH  — an LLM call merges the specialist outputs into one grounded answer.

Degrades gracefully: if planning fails or yields a single step, it just runs that
one specialist (or the normal all-tools agent), so it never does worse than the
single-agent path.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any, Callable, Dict, List, Optional

from app.services.agent_loop import run_agent
from app.services.agents.specialists import SPECIALISTS, get_specialist, roster_for_planner
from app.services.llm_client import get_llm_client

logger = logging.getLogger(__name__)

MAX_STEPS = 4
MAX_CONCURRENCY = 3
_PLAN_BUDGET_S = 45

# Capability keywords used to detect multi-domain goals worth orchestrating.
_DOMAIN_WORDS = (
    "email", "inbox", "reply", "draft", "send",
    "calendar", "meeting", "schedule", "standup", "invite",
    "research", "look up", "find out", "compare", "latest",
    "code", "script", "function", "bug", "debug", "implement",
    "file", "drive", "document", "doc",
    "screen", "open ", "click", "type ", "notepad", "app",
)
_CONNECTORS = re.compile(r"\b(and|then|also|plus|after that|as well as)\b", re.IGNORECASE)


def should_orchestrate(message: str) -> bool:
    """Heuristic: route compound / multi-domain goals to the lead orchestrator.

    Conservative — clearly-simple turns stay on the fast single-agent path. Even
    if this fires on a borderline case, the planner falls back to a single
    specialist, so the worst case is one extra planner call.
    """
    m = (message or "").strip()
    if len(m) < 28:
        return False
    low = m.lower()
    domain_hits = sum(1 for w in _DOMAIN_WORDS if w in low)
    has_connector = bool(_CONNECTORS.search(low))
    # Need a join word AND at least two distinct capability domains.
    return has_connector and domain_hits >= 2


def _extract_json_array(text: str) -> Optional[list]:
    """Pull the first JSON array out of a (possibly chatty) model response."""
    if not text:
        return None
    # Prefer a fenced block, else the first [...] span.
    m = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
    raw = m.group(1) if m else None
    if raw is None:
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end > start:
            raw = text[start:end + 1]
    if raw is None:
        return None
    try:
        data = json.loads(raw)
        return data if isinstance(data, list) else None
    except Exception:
        return None


async def _plan(message: str, roster: Optional[set] = None) -> List[dict]:
    """Ask the planner for a list of {agent, task, depends_on}. May be empty.

    ``roster`` (a set of agent names) restricts which specialists the planner may
    use — the Council passes its 6 members so the plan stays on-roster."""
    llm = get_llm_client()
    max_steps = len(roster) if roster else MAX_STEPS
    sys = (
        "You are the LEAD agent for MyAi. Break the user's goal into the FEWEST "
        "sub-tasks that fully achieve it (1-" + str(max_steps) + " steps), each "
        "handled by exactly ONE specialist.\n\nSpecialists:\n" + roster_for_planner(roster) +
        "\n\nRules:\n"
        "- Independent steps run in PARALLEL — only add a dependency when a step "
        "truly needs an earlier step's output.\n"
        "- 'depends_on' is a list of earlier step numbers (0-based); [] if independent.\n"
        "- If the goal needs only one specialist, return a single step.\n"
        "- Output ONLY a JSON array, no prose:\n"
        '[{"agent":"research","task":"...","depends_on":[]}, ...]'
    )
    try:
        r = await llm.chat(
            [{"role": "system", "content": sys},
             {"role": "user", "content": f"User goal: {message}"}],
            temperature=0.2,
        )
        content = ((r.get("message", {}) or {}).get("content") or "")
    except Exception as exc:  # noqa: BLE001
        logger.warning("planner call failed: %s", exc)
        return []

    raw = _extract_json_array(content) or []
    steps: List[dict] = []
    for item in raw[:max_steps]:
        if not isinstance(item, dict):
            continue
        agent = str(item.get("agent", "")).strip().lower()
        task = str(item.get("task", "")).strip()
        if agent not in SPECIALISTS or not task or (roster and agent not in roster):
            continue
        deps_in = item.get("depends_on") or []
        deps: List[int] = []
        if isinstance(deps_in, list):
            for d in deps_in:
                try:
                    di = int(d)
                    if 0 <= di < len(raw):
                        deps.append(di)
                except (ValueError, TypeError):
                    pass
        steps.append({"agent": agent, "task": task, "depends_on": deps})
    # Drop self/forward dependencies (only earlier steps are valid).
    for i, s in enumerate(steps):
        s["depends_on"] = [d for d in s["depends_on"] if d < i]
    return steps


async def _synthesize(message: str, steps: List[dict], results: Dict[int, str],
                      extra: str = "") -> str:
    llm = get_llm_client()
    blocks = "\n\n".join(
        f"[{steps[i]['agent']}] {results[i]}" for i in sorted(results) if results[i]
    )
    sys = (
        "You are MyAi. Combine the specialist results below into ONE clear, "
        "complete answer to the user's goal. Preserve any source URLs/citations. "
        "Do NOT mention 'specialists', 'agents', or the planning — just answer the "
        "user directly and honestly (if a part failed, say so)."
    )
    if extra:
        sys += "\n\n" + extra
    try:
        r = await llm.chat(
            [{"role": "system", "content": sys},
             {"role": "user", "content": f"User goal: {message}\n\nResults:\n{blocks}"}],
            temperature=0.3,
        )
        return ((r.get("message", {}) or {}).get("content") or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("synthesis failed: %s", exc)
        # Fall back to concatenating the raw results.
        return blocks


async def run_orchestrator(
    message: str,
    history: List[Dict[str, str]],
    *,
    user,
    autonomy_label: str,
    autonomy_level: int,
    today_iso: str,
    seed_context: str = "",
    progress: Optional[Callable[[str], None]] = None,
    roster: Optional[set] = None,
    on_event: Optional[Callable[[dict], None]] = None,
    synth_extra: str = "",
    models: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """Returns {answer, plan, agents_used, steps, orchestrated, elapsed_ms}.

    ``roster`` restricts the planner to a subset of specialists (Council use).
    ``on_event`` receives structured progress dicts: {type:"plan",steps},
    {type:"state",agent,state,task[,report]}, {type:"synthesizing"} — used to drive
    the Council's live node graph and to persist per-agent reports.
    ``models`` maps agent name -> model id, overriding each specialist's default."""
    t0 = time.monotonic()

    def _emit(msg: str) -> None:
        if progress:
            try:
                progress(msg)
            except Exception:  # noqa: BLE001
                pass

    def _event(ev: dict) -> None:
        if on_event:
            try:
                on_event(ev)
            except Exception:  # noqa: BLE001
                pass

    def _run_specialist(task: str, sp, seed: str):
        return run_agent(
            task, history, user=user, autonomy_label=autonomy_label,
            autonomy_level=autonomy_level, today_iso=today_iso,
            seed_context=seed, extra_system=sp.specialization, allowed_tools=sp.tools,
            model=(models or {}).get(sp.name) or sp.model,
        )

    _emit("Planning…")
    if roster and len(roster) == 1:
        # Single-agent run (e.g. one council member): no planner needed — the
        # whole goal IS that agent's task. Deterministic and one LLM call cheaper.
        steps = [{"agent": next(iter(roster)), "task": message, "depends_on": []}]
    else:
        steps = await _plan(message, roster)
    _event({"type": "plan", "steps": steps})

    # --- Fallback: no usable plan -> single all-tools agent (today's behavior) ---
    if not steps:
        _emit("Working on it…")
        answer, tools = await run_agent(
            message, history, user=user, autonomy_label=autonomy_label,
            autonomy_level=autonomy_level, today_iso=today_iso, seed_context=seed_context,
        )
        return {"answer": answer, "plan": [], "agents_used": [], "steps": [],
                "orchestrated": False, "tools_used": tools,
                "elapsed_ms": int((time.monotonic() - t0) * 1000)}

    # --- Single step: run that one specialist directly (no synthesis call) ---
    if len(steps) == 1:
        sp = get_specialist(steps[0]["agent"])
        _emit(f"{sp.title} working…")
        _event({"type": "state", "agent": sp.name, "state": "working", "task": steps[0]["task"]})
        answer, tools = await _run_specialist(steps[0]["task"], sp, seed_context)
        _event({"type": "state", "agent": sp.name, "state": "ready", "task": steps[0]["task"], "report": answer})
        return {
            "answer": answer, "plan": steps, "agents_used": [sp.name],
            "steps": [{"agent": sp.name, "task": steps[0]["task"], "result": answer}],
            "orchestrated": True, "tools_used": tools,
            "elapsed_ms": int((time.monotonic() - t0) * 1000),
        }

    # --- Phased parallel execution ---
    sem = asyncio.Semaphore(MAX_CONCURRENCY)
    results: Dict[int, str] = {}
    step_tools: Dict[int, list] = {}
    done: set = set()
    remaining = set(range(len(steps)))
    agents_used: List[str] = []

    async def _do(i: int) -> None:
        sp = get_specialist(steps[i]["agent"])
        dep_ctx = "\n\n".join(
            f"Result from the {steps[d]['agent']} step:\n{(results.get(d) or '')[:1800]}"
            for d in steps[i]["depends_on"]
        )
        seed = "\n\n".join(x for x in (seed_context, dep_ctx) if x)
        async with sem:
            _emit(f"{sp.title}: {steps[i]['task'][:60]}")
            _event({"type": "state", "agent": sp.name, "state": "working", "task": steps[i]["task"]})
            try:
                ans, tools = await _run_specialist(steps[i]["task"], sp, seed)
            except Exception as exc:  # noqa: BLE001
                logger.warning("specialist %s failed: %s", sp.name, exc)
                ans, tools = f"(The {sp.name} step could not complete: {exc})", []
        results[i] = ans
        step_tools[i] = tools
        _event({"type": "state", "agent": sp.name, "state": "ready", "task": steps[i]["task"], "report": ans})

    while remaining:
        ready = [i for i in remaining if all(d in done for d in steps[i]["depends_on"])]
        if not ready:  # broken/cyclic deps — run the rest together
            ready = list(remaining)
        await asyncio.gather(*[_do(i) for i in ready])
        for i in ready:
            done.add(i)
            remaining.discard(i)
            agents_used.append(steps[i]["agent"])

    _emit("Synthesizing…")
    _event({"type": "synthesizing"})
    answer = await _synthesize(message, steps, results, synth_extra)

    all_tools = sorted({t for ts in step_tools.values() for t in ts})
    return {
        "answer": answer,
        "plan": steps,
        "agents_used": agents_used,
        "steps": [
            {"agent": steps[i]["agent"], "task": steps[i]["task"], "result": results.get(i, "")}
            for i in range(len(steps))
        ],
        "orchestrated": True,
        "tools_used": all_tools,
        "elapsed_ms": int((time.monotonic() - t0) * 1000),
    }
