"""Deep Research — bounded, code-driven multi-round web research with a cited report.

Borrowed in spirit from Odysseus's deep-research subsystem, but adapted to MyAi's
design rule: orchestration is CODE-driven and hard-bounded, never an open LLM loop
(the local qwen2.5:7b is unreliable at running long autonomous loops — see
feedback_tool_calling). The model is used for three narrow jobs only:

  1. PLAN      — one call → {sub_questions, queries}
  2. INTEGRATE — one call per round → {done, follow_up_queries} (a small decision)
  3. REPORT    — one final call → markdown with inline [title](url) citations

Heavy lifting (fan-out search, fetch+extract, dedup) is plain Python. Everything
is fail-soft and returns a partial result on timeout, matching the agent loop's
TIME_BUDGET discipline. Reuses websearch (provider chain) + grounding JSON parse.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from app.services.grounding import _extract_json_obj
from app.services.websearch import fetch_page_text, web_search

logger = logging.getLogger(__name__)


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%A, %d %B %Y")


def _this_year() -> str:
    return datetime.now(timezone.utc).strftime("%Y")


_RECENCY_RE = re.compile(r"\b(recent|latest|newest|current|new|2020|2021|2022|2023|2024|2025|2026)\b", re.I)


def _add_year(q: str) -> str:
    """Append the current year to recency queries that don't already name a year,
    so stale pages rank lower."""
    if re.search(r"\b20\d\d\b", q):
        return q
    if _RECENCY_RE.search(q):
        return f"{q} {_this_year()}"
    return q

# Hard bounds (consistent with agent_loop's TIME_BUDGET_S discipline).
TIME_BUDGET_S = 180.0
_SEARCH_CONCURRENCY = 4
_FETCH_CONCURRENCY = 4
_FETCHES_PER_ROUND = 5
_MAX_TOTAL_FETCHES = 14
_MAX_QUERIES_PER_ROUND = 4
_FETCH_CHARS = 2400
_REPORT_EVIDENCE_CHARS = 18000
_MIN_REPORT_CHARS = 400

ProgressFn = Callable[[str, str], None]


@dataclass
class Source:
    idx: int
    title: str
    url: str
    snippet: str = ""
    text: str = ""


@dataclass
class ResearchResult:
    query: str
    report: str = ""
    sources: List[Dict[str, str]] = field(default_factory=list)
    sub_questions: List[str] = field(default_factory=list)
    rounds_done: int = 0
    partial: bool = False
    elapsed_s: float = 0.0
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "report": self.report,
            "sources": self.sources,
            "sub_questions": self.sub_questions,
            "rounds_done": self.rounds_done,
            "partial": self.partial,
            "elapsed_s": round(self.elapsed_s, 1),
            "error": self.error,
        }


def _noop(stage: str, detail: str) -> None:  # default progress sink
    pass


async def _ask(llm: Any, system: str, user: str, *, temperature: float = 0.3,
               max_tokens: Optional[int] = None) -> str:
    """One LLM call → text content. Never raises (returns '')."""
    try:
        r = await llm.chat(
            [{"role": "system", "content": system},
             {"role": "user", "content": user}],
            temperature=temperature, max_tokens=max_tokens,
        )
        return (((r or {}).get("message") or {}).get("content") or "").strip()
    except Exception as exc:  # noqa: BLE001
        logger.warning("deep_research LLM call failed: %s", exc)
        return ""


# ---------------------------------------------------------------------------
# Stage 1 — plan
# ---------------------------------------------------------------------------

_PLAN_SYSTEM = (
    "You are a research planner. Break the user's goal into 3-5 SUBSTANTIVE "
    "sub-questions and 3-5 web search queries.\n"
    "Sub-questions: cover the real substance — what the things ARE, who makes them, "
    "key features/capabilities, comparisons, notable examples. Do NOT slice by "
    "narrow time windows (avoid 'released specifically in June?' style questions — "
    "they lead to dead ends); ask broadly (e.g. 'which models were released "
    "recently and by whom', 'what are their key capabilities').\n"
    "Queries:\n"
    "- EXPAND ambiguous abbreviations to the full term (e.g. 'LLM' -> 'large "
    "language model'; 'LMS' is a DIFFERENT topic; 'EV' -> 'electric vehicle').\n"
    "- Include the current year for 'recent/latest' goals; keep them precise.\n"
    'Respond with ONLY JSON: {"sub_questions": ["..."], "queries": ["..."]}. No prose.'
)


async def _plan(llm: Any, query: str) -> Dict[str, List[str]]:
    user = (
        f"Today is {_today_str()}. The user wants CURRENT information — frame "
        f"sub-questions and queries around {_this_year()} (and the months just "
        f"before it), NOT older years, unless the goal explicitly asks about the "
        f"past.\n\nResearch goal: {query}\n\nJSON plan:"
    )
    raw = await _ask(llm, _PLAN_SYSTEM, user, temperature=0.2, max_tokens=400)
    obj = _extract_json_obj(raw) or {}
    # If the goal isn't explicitly about a past year, rewrite stale years the 7B
    # injected (training-cutoff bias) to the current year so the report doesn't
    # frame "latest" around 2023.
    goal_has_year = bool(re.search(r"\b20\d\d\b", query))
    def _freshen(s: str) -> str:
        return s if goal_has_year else re.sub(r"\b20(1\d|2[0-4])\b", _this_year(), s)
    subs = [_freshen(str(s).strip()) for s in (obj.get("sub_questions") or []) if str(s).strip()][:5]
    queries = [_add_year(_disambiguate(_freshen(str(q).strip()))) for q in (obj.get("queries") or []) if str(q).strip()][:5]
    if not queries:  # fail-soft: at least search the raw goal
        queries = [query]
    if not subs:
        subs = [query]
    return {"sub_questions": subs, "queries": queries}


# Expand ambiguous abbreviations in a query so the search engine doesn't match a
# different topic (the classic failure: LLM vs LMS / learning-management-system).
_ABBREV = {
    "llm": "large language model",
    "vlm": "vision language model",
    "slm": "small language model",
    "rag": "retrieval augmented generation",
    "ev": "electric vehicle",
}


def _disambiguate(q: str) -> str:
    low = q.lower()
    out = q
    for ab, full in _ABBREV.items():
        if re.search(rf"\b{ab}s?\b", low) and full.split()[0] not in low:
            out = f"{out} {full}"
    return out


# Tokens that are too generic to signal topic relevance (shared by many topics).
_GENERIC = {
    "open", "source", "best", "top", "new", "newest", "latest", "guide", "list",
    "the", "for", "and", "with", "using", "what", "how", "review", "reviews",
    "compared", "comparison", "platforms", "tools", "free", "blog", "about",
    "2022", "2023", "2024", "2025", "2026", "recent", "popular", "good",
}


def _relevance_tokens(text: str) -> List[str]:
    return [t for t in re.findall(r"[a-z0-9]+", (text or "").lower())
            if len(t) >= 3 and t not in _GENERIC]


def _filter_relevant_sources(query: str, subs: List[str], sources: List[Source]) -> List[Source]:
    """Drop sources that share no DISTINCTIVE term with the goal — kills off-topic
    keyword collisions (e.g. 'LMS' results for an 'LLM' query). Substring match so
    'llm' still matches 'llms'. Fail-safe: never reduce below 3 sources."""
    key = list(dict.fromkeys(_relevance_tokens(query + " " + " ".join(subs))))
    if not key:
        return sources
    kept = [s for s in sources
            if any(t in f"{s.title} {s.snippet} {s.text}".lower() for t in key)]
    return kept if len(kept) >= 3 else sources


# ---------------------------------------------------------------------------
# Stage 2 — a round: fan-out search + fetch
# ---------------------------------------------------------------------------


async def _run_queries(queries: List[str], time_filter: Optional[str]) -> List[Dict[str, str]]:
    sem = asyncio.Semaphore(_SEARCH_CONCURRENCY)

    async def one(q: str) -> List[Dict[str, str]]:
        async with sem:
            return await web_search(q, max_results=6, time_filter=time_filter)

    batches = await asyncio.gather(*(one(q) for q in queries), return_exceptions=True)
    flat: List[Dict[str, str]] = []
    for b in batches:
        if isinstance(b, list):
            flat.extend(b)
    return flat


async def _fetch_texts(urls: List[str]) -> Dict[str, str]:
    sem = asyncio.Semaphore(_FETCH_CONCURRENCY)

    async def one(u: str) -> tuple[str, str]:
        async with sem:
            return u, await fetch_page_text(u, max_chars=_FETCH_CHARS)

    pairs = await asyncio.gather(*(one(u) for u in urls), return_exceptions=True)
    out: Dict[str, str] = {}
    for p in pairs:
        if isinstance(p, tuple):
            out[p[0]] = p[1]
    return out


_INTEGRATE_SYSTEM = (
    "You are a research progress auditor. Given the research goal, its sub-questions, "
    "and a list of sources gathered so far (titles + snippets), decide whether the "
    "sub-questions are now answered with multi-sourced evidence. If gaps remain, "
    "propose up to 3 NEW, more specific follow-up search queries to close them. "
    'Respond with ONLY JSON: {"done": true|false, "follow_up_queries": ["..."]}. '
    "Set done=true when coverage is solid or no useful new query exists. No prose."
)


async def _integrate(llm: Any, query: str, subs: List[str],
                     sources: List[Source]) -> Dict[str, Any]:
    listing = "\n".join(
        f"[{s.idx}] {s.title} — {s.url}\n    {(s.snippet or s.text)[:160]}"
        for s in sources[-20:]
    )
    user = (
        f"Research goal: {query}\n\nSub-questions:\n"
        + "\n".join(f"- {s}" for s in subs)
        + f"\n\nSources gathered so far ({len(sources)}):\n{listing}\n\nJSON decision:"
    )
    raw = await _ask(llm, _INTEGRATE_SYSTEM, user, temperature=0.2, max_tokens=300)
    obj = _extract_json_obj(raw) or {}
    follow = [str(q).strip() for q in (obj.get("follow_up_queries") or []) if str(q).strip()]
    return {"done": bool(obj.get("done", False)), "follow_up_queries": follow[:_MAX_QUERIES_PER_ROUND]}


# ---------------------------------------------------------------------------
# Stage 3 — report
# ---------------------------------------------------------------------------

_REPORT_SYSTEM = (
    "You are a precise research writer. Using ONLY the numbered SOURCES, write a "
    "clear markdown report answering the goal and sub-questions. Rules:\n"
    "- Be COMPREHENSIVE: extract EVERY relevant named item across ALL sources. For "
    "a 'latest models' goal, produce a full list of each distinct model named in "
    "any source, with its maker, version and date — aim for breadth (10+ items if "
    "the sources support it), not 2-3. Read the source TITLES; they often name the "
    "answer directly.\n"
    "- Extract SPECIFIC, CONCRETE facts: exact names, versions, dates, orgs, numbers.\n"
    "- IGNORE noise: search-trend listicles, growth-percentage rankings, 'trending' "
    "keyword lists, ads, and anything that isn't an actual answer. Never present a "
    "trending keyword or unrelated product as if it were the topic.\n"
    "- Do NOT hedge, pad, or write meta-sentences like 'the sources do not provide "
    "specific information'. If ANY source names relevant items, LIST them. Only note "
    "a genuine gap if no source covers it at all.\n"
    "- Prefer the most RECENT information; flag clearly-old items as dated.\n"
    "- Use ## section headings; cite claims inline as [title](https://...) with the "
    "real URLs. Do NOT invent facts/URLs. No placeholders like [Name]/[Date].\n"
    "- End with a '## Sources' section listing the sources you used.\n"
    "Write the report only — no preamble, no meta commentary about your process."
)


def _evidence_block(sources: List[Source]) -> str:
    parts: List[str] = []
    used = 0
    for s in sources:
        # Lead with the TITLE + the clean snippet (Tavily's extracted content),
        # then a CAPPED slice of full-page text. Full pages add noise (listicles,
        # trending widgets), so bound their contribution per source.
        snippet = (s.snippet or "").strip()
        page = (s.text or "").strip()[:900]
        body = (snippet + ("\n" + page if page else "")).strip()
        if not body:
            continue
        chunk = f"[{s.idx}] TITLE: {s.title}\nURL: {s.url}\n{body}\n"
        if used + len(chunk) > _REPORT_EVIDENCE_CHARS:
            break
        parts.append(chunk)
        used += len(chunk)
    return "\n".join(parts)


_NUM_REF_RE = re.compile(r"(?<!\])\[(\d{1,2})\](?!\()")


def _linkify_citations(report: str, sources: List[Source]) -> str:
    """Make citations clickable regardless of the model's formatting.

    A 7B model inconsistently writes markdown links vs. bare numeric refs like
    ``[3]``. We map any bare ``[N]`` to a real markdown link to source N's URL,
    and guarantee a ``## Sources`` section. Existing ``[title](url)`` links are
    left untouched (the lookbehind/lookahead skip them).
    """
    by_idx = {s.idx: s for s in sources}

    def repl(m: "re.Match") -> str:
        s = by_idx.get(int(m.group(1)))
        return f"[{m.group(1)}]({s.url})" if s else m.group(0)

    report = _NUM_REF_RE.sub(repl, report or "")
    if "## sources" not in report.lower():
        listing = "\n".join(f"{s.idx}. [{s.title}]({s.url})" for s in sources[:20])
        report = f"{report.rstrip()}\n\n## Sources\n{listing}"
    return report


async def _write_report(llm: Any, query: str, subs: List[str],
                        sources: List[Source]) -> str:
    evidence = _evidence_block(sources)
    user = (
        f"Today is {_today_str()}. Treat information older than ~18 months as "
        f"potentially outdated and prefer the most recent releases/figures; if the "
        f"sources are clearly old, say so rather than presenting them as current.\n\n"
        f"Research goal: {query}\n\nSub-questions:\n"
        + "\n".join(f"- {s}" for s in subs)
        + f"\n\nSOURCES:\n{evidence}\n\nWrite the markdown report now. When you use a "
          "fact from a source, cite it inline as a markdown link to that source's URL:"
    )
    report = await _ask(llm, _REPORT_SYSTEM, user, temperature=0.3, max_tokens=2400)
    if len(report) < _MIN_REPORT_CHARS:
        # One retry with a firmer nudge (Odysseus technique against thin reports).
        report = await _ask(
            llm, _REPORT_SYSTEM,
            user + "\n\nThe report must be thorough (several paragraphs with "
                   "inline citations). Write it in full now:",
            temperature=0.35, max_tokens=1800,
        )
    return _linkify_citations(report, sources)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


async def run_deep_research(
    query: str,
    *,
    on_progress: Optional[ProgressFn] = None,
    max_rounds: int = 3,
    time_filter: Optional[str] = None,
    time_budget: float = TIME_BUDGET_S,
    llm: Any = None,
) -> ResearchResult:
    """Plan → bounded multi-round search/fetch → cited markdown report.

    Bounded: ≤ (2 + max_rounds) LLM calls, ≤ _MAX_TOTAL_FETCHES page fetches, and a
    `time_budget` wall-clock cap (default ~180s) after which it returns a partial
    result. Callers on a tighter turn budget (e.g. the in-chat agent tool) pass a
    smaller value. Never raises — errors surface on ResearchResult.error.
    """
    emit = on_progress or _noop
    t0 = time.monotonic()
    res = ResearchResult(query=query)
    if not query or not query.strip():
        res.error = "empty query"
        return res
    if llm is None:
        from app.services.llm_client import get_llm_client
        llm = get_llm_client()

    def over_budget() -> bool:
        return (time.monotonic() - t0) > time_budget

    sources: List[Source] = []
    seen_urls: set[str] = set()

    def add_results(items: List[Dict[str, str]]) -> int:
        added = 0
        for r in items:
            url = r.get("url", "")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            sources.append(Source(idx=len(sources) + 1, title=r.get("title") or url,
                                   url=url, snippet=r.get("snippet") or ""))
            added += 1
        return added

    try:
        # ---- Plan ----
        emit("planning", "Breaking the question into sub-questions and queries")
        plan = await _plan(llm, query)
        res.sub_questions = plan["sub_questions"]
        next_queries = plan["queries"]

        # ---- Rounds ----
        empty_rounds = 0
        for rnd in range(1, max_rounds + 1):
            if over_budget() or not next_queries:
                break
            res.rounds_done = rnd
            emit("searching", f"Round {rnd}: searching {len(next_queries)} queries")
            found = await _run_queries(next_queries[:_MAX_QUERIES_PER_ROUND], time_filter)
            new_n = add_results(found)

            # Fetch full text for the newest unfetched URLs (bounded).
            to_fetch = [s for s in sources if not s.text and s.url][-_FETCHES_PER_ROUND:]
            already = sum(1 for s in sources if s.text)
            if already < _MAX_TOTAL_FETCHES and to_fetch and not over_budget():
                emit("reading", f"Round {rnd}: reading {len(to_fetch)} pages")
                texts = await _fetch_texts([s.url for s in to_fetch])
                for s in to_fetch:
                    s.text = texts.get(s.url, "")

            if new_n == 0:
                empty_rounds += 1
                if empty_rounds >= 2:
                    break
            else:
                empty_rounds = 0

            if over_budget() or rnd == max_rounds:
                break

            # ---- Integrate: decide done + next queries ----
            emit("integrating", f"Round {rnd}: assessing coverage")
            decision = await _integrate(llm, query, res.sub_questions, sources)
            if decision["done"]:
                break
            next_queries = decision["follow_up_queries"]

        # ---- Report ----
        # Drop off-topic keyword collisions (e.g. LMS for an LLM query), then
        # renumber so inline [N] citations line up with the kept sources.
        sources = _filter_relevant_sources(query, res.sub_questions, sources)
        for i, s in enumerate(sources, 1):
            s.idx = i
        res.sources = [{"title": s.title, "url": s.url} for s in sources]
        if not sources:
            res.error = "no sources found"
            res.report = (f"I couldn't find live web sources for **{query}** right now. "
                          "The search providers may be unreachable — try again shortly.")
            res.partial = True
            res.elapsed_s = time.monotonic() - t0
            emit("done", "no sources")
            return res

        res.partial = over_budget()
        emit("writing", "Synthesizing the cited report")
        res.report = await _write_report(llm, query, res.sub_questions, sources)
        if not res.report:
            res.report = ("I gathered sources but couldn't synthesize a report in time. "
                          "Sources are listed below.\n\n## Sources\n"
                          + "\n".join(f"- [{s.title}]({s.url})" for s in sources))
            res.partial = True
        res.elapsed_s = time.monotonic() - t0
        emit("done", f"{len(sources)} sources, {res.rounds_done} round(s)")
        return res
    except Exception as exc:  # noqa: BLE001 — never raise to the caller
        logger.exception("deep_research failed")
        res.error = str(exc)
        res.elapsed_s = time.monotonic() - t0
        res.sources = [{"title": s.title, "url": s.url} for s in sources]
        emit("error", str(exc)[:200])
        return res
