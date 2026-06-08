"""Correctness-spine evaluation runner (Pillar 2).

Scores the golden set in ``eval/golden/cases.json`` and prints a scorecard.

Case kinds:
  need    — grounding_need(message) must match expectation       (deterministic)
  scrub   — copilot._scrub_placeholders must leave no placeholder (deterministic)
  caveat  — apply_verdict must annotate iff claims are unverified (deterministic)
  verify  — the live LLM verifier must agree on grounded/ungrounded (needs an LLM)

The DETERMINISTIC kinds are a hard gate: any failure exits non-zero, so a
regression in the safety logic breaks CI. The ``verify`` kind exercises the
actual model and is reported as accuracy; it only gates when ``--strict`` is
passed AND an LLM is reachable (otherwise those cases are skipped).

Usage:
    python -m eval.run_eval            # report; gate on deterministic kinds
    python -m eval.run_eval --strict   # also gate on verifier accuracy (>= bar)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.api.copilot import _PLACEHOLDER_RE, _scrub_placeholders  # noqa: E402
from app.api.preferences import decide_write_gate  # noqa: E402
from app.services.grounding import (  # noqa: E402
    Verdict,
    _extract_json_obj,
    apply_verdict,
    grounding_need,
    verify_grounding,
)

# A real inline citation: [text](http(s)://...). Deep-research reports must carry these.
_HTTP_CITE_RE = re.compile(r"\[[^\]]+\]\(https?://[^)]+\)")

CASES_PATH = ROOT / "eval" / "golden" / "cases.json"
REPORT_PATH = ROOT / "eval" / "last_report.json"

DETERMINISTIC_KINDS = {"need", "scrub", "caveat", "autonomy", "research_plan", "research_cite"}
VERIFY_PASS_BAR = 0.75  # min verifier accuracy to pass under --strict

# Fixed values so scrub cases are reproducible regardless of the wall clock.
_FAKE_FIRST = "Alex"
_FAKE_TODAY = "Monday, 21 July 2025"


def _score_need(case: Dict[str, Any]) -> Dict[str, Any]:
    actual = grounding_need(case["message"])
    want = bool(case["expect"]["need"])
    return {"passed": actual == want, "detail": f"need={actual} want={want}"}


def _score_scrub(case: Dict[str, Any]) -> Dict[str, Any]:
    scrubbed = _scrub_placeholders(case["answer"], _FAKE_FIRST, _FAKE_TODAY)
    leak = _PLACEHOLDER_RE.search(scrubbed)
    ok = leak is None
    for token in case["expect"].get("must_include", []):
        if token not in scrubbed:
            ok = False
    detail = "clean" if leak is None else f"leak={leak.group(0)!r}"
    return {"passed": ok, "detail": detail, "output": scrubbed}


def _score_caveat(case: Dict[str, Any]) -> Dict[str, Any]:
    v = Verdict(
        status=case["verdict"]["status"],
        unsupported=case["verdict"].get("unsupported", []),
    )
    out = apply_verdict(case["answer"], v)
    appended = out != case["answer"]
    want = bool(case["expect"]["caveat"])
    return {"passed": appended == want, "detail": f"appended={appended} want={want}"}


def _score_autonomy(case: Dict[str, Any]) -> Dict[str, Any]:
    allowed, needs_conf, _reason = decide_write_gate(
        int(case["level"]), case["action"], bool(case["confirmed"])
    )
    exp = case["expect"]
    ok = allowed == bool(exp["allowed"])
    if "needs_confirmation" in exp:
        ok = ok and (needs_conf == bool(exp["needs_confirmation"]))
    return {
        "passed": ok,
        "detail": f"L{case['level']} {case['action']} confirm={case['confirmed']} "
        f"-> allowed={allowed} needs_conf={needs_conf}",
    }


def _score_research_plan(case: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministic: the plan JSON (even fenced/dirty) parses into sub-questions
    + queries — the contract deep_research._plan relies on."""
    obj = _extract_json_obj(case["raw"]) or {}
    subs = [s for s in (obj.get("sub_questions") or []) if str(s).strip()]
    queries = [q for q in (obj.get("queries") or []) if str(q).strip()]
    ok = len(subs) >= case["expect"]["sub_questions"] and len(queries) >= case["expect"]["queries"]
    return {"passed": ok, "detail": f"subs={len(subs)} queries={len(queries)}"}


def _score_research_cite(case: Dict[str, Any]) -> Dict[str, Any]:
    """Deterministic: a report's inline-http-citation presence matches expectation,
    and placeholder-scrub still holds on the report."""
    report = case["report"]
    has_cite = bool(_HTTP_CITE_RE.search(report))
    want_cite = bool(case["expect"]["http_citation"])
    scrubbed = _scrub_placeholders(report, _FAKE_FIRST, _FAKE_TODAY)
    clean = _PLACEHOLDER_RE.search(scrubbed) is None
    ok = (has_cite == want_cite) and clean
    return {"passed": ok, "detail": f"cite={has_cite} want={want_cite} scrub_clean={clean}"}


async def _score_research(case: Dict[str, Any], llm_up: bool) -> Dict[str, Any]:
    """Live: a real (capped) deep-research run returns >=1 source and a report with
    a real http citation. Needs LLM + network; skipped/soft like the verify kind."""
    if not llm_up:
        return {"passed": None, "detail": "skipped (no LLM reachable)"}
    from app.services.deep_research import run_deep_research

    r = await run_deep_research(case["query"], max_rounds=int(case.get("max_rounds", 1)))
    has_cite = bool(_HTTP_CITE_RE.search(r.report or ""))
    enough = len(r.sources) >= int(case["expect"].get("min_sources", 1))
    want_cite = bool(case["expect"].get("http_citation", True))
    ok = enough and (has_cite or not want_cite)
    return {
        "passed": ok,
        "detail": f"sources={len(r.sources)} cite={has_cite} partial={r.partial} "
        f"rounds={r.rounds_done}",
    }


async def _score_verify(case: Dict[str, Any], llm_up: bool) -> Dict[str, Any]:
    if not llm_up:
        return {"passed": None, "detail": "skipped (no LLM reachable)"}
    verdict = await verify_grounding(case["message"], case["answer"], case["context"])
    got_grounded = verdict.status == "grounded"
    want_grounded = bool(case["expect"]["grounded"])
    return {
        "passed": got_grounded == want_grounded,
        "detail": f"status={verdict.status} want_grounded={want_grounded} "
        f"unsupported={verdict.unsupported}",
    }


async def run() -> int:
    parser = argparse.ArgumentParser(description="MyAi correctness-spine eval")
    parser.add_argument("--strict", action="store_true", help="also gate on verifier accuracy")
    args = parser.parse_args()

    cases: List[Dict[str, Any]] = json.loads(CASES_PATH.read_text(encoding="utf-8"))

    # Is an LLM reachable? Verifier cases need one.
    llm_up = False
    try:
        from app.services.llm_client import get_llm_client

        llm_up = await get_llm_client().health_check()
    except Exception:
        llm_up = False

    results: List[Dict[str, Any]] = []
    for case in cases:
        kind = case["kind"]
        if kind == "need":
            r = _score_need(case)
        elif kind == "scrub":
            r = _score_scrub(case)
        elif kind == "caveat":
            r = _score_caveat(case)
        elif kind == "autonomy":
            r = _score_autonomy(case)
        elif kind == "research_plan":
            r = _score_research_plan(case)
        elif kind == "research_cite":
            r = _score_research_cite(case)
        elif kind == "research":
            r = await _score_research(case, llm_up)
        elif kind == "verify":
            r = await _score_verify(case, llm_up)
        else:
            r = {"passed": None, "detail": f"unknown kind {kind}"}
        results.append({"id": case["id"], "kind": kind, "function": case.get("function", "?"), **r})

    # ---- Scorecard ----
    by_kind: Dict[str, List[bool]] = defaultdict(list)
    by_func: Dict[str, List[bool]] = defaultdict(list)
    for r in results:
        if r["passed"] is None:
            continue
        by_kind[r["kind"]].append(r["passed"])
        by_func[r["function"]].append(r["passed"])

    print("\n" + "=" * 64)
    print("  MyAi Correctness-Spine Eval")
    print("=" * 64)
    print(f"  LLM reachable: {'yes' if llm_up else 'no (verify cases skipped)'}")

    print("\n  Per-case:")
    for r in results:
        mark = {True: "PASS", False: "FAIL", None: "skip"}[r["passed"]]
        print(f"    [{mark}] {r['kind']:<7} {r['id']:<28} {r['detail']}")

    def _summ(title: str, buckets: Dict[str, List[bool]]) -> None:
        print(f"\n  {title}:")
        for key in sorted(buckets):
            vals = buckets[key]
            p = sum(vals)
            print(f"    {key:<14} {p}/{len(vals)}  ({100 * p / len(vals):.0f}%)")

    _summ("By kind", by_kind)
    _summ("By function", by_func)

    det_results = [r["passed"] for r in results if r["kind"] in DETERMINISTIC_KINDS and r["passed"] is not None]
    ver_results = [r["passed"] for r in results if r["kind"] == "verify" and r["passed"] is not None]
    det_pass = all(det_results) if det_results else True
    ver_acc = (sum(ver_results) / len(ver_results)) if ver_results else None

    print("\n  " + "-" * 60)
    print(f"  Deterministic safety gate: {'PASS' if det_pass else 'FAIL'} "
          f"({sum(det_results)}/{len(det_results)})")
    if ver_acc is None:
        print("  Verifier accuracy:        n/a (no LLM)")
    else:
        print(f"  Verifier accuracy:        {100 * ver_acc:.0f}% "
              f"({sum(ver_results)}/{len(ver_results)}, bar {100 * VERIFY_PASS_BAR:.0f}%)")
    print("=" * 64 + "\n")

    REPORT_PATH.write_text(
        json.dumps(
            {
                "llm_reachable": llm_up,
                "deterministic_pass": det_pass,
                "verifier_accuracy": ver_acc,
                "results": results,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    # ---- Gate ----
    failed = not det_pass
    if args.strict and ver_acc is not None and ver_acc < VERIFY_PASS_BAR:
        failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
