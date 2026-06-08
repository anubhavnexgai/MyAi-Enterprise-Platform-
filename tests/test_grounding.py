"""Unit tests for the correctness spine (app/services/grounding.py).

Covers the deterministic logic only — no LLM required. The live verifier pass
is exercised by ``python -m eval.run_eval`` when a model is reachable.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.grounding import (  # noqa: E402
    Verdict,
    apply_verdict,
    build_citations,
    grounding_need,
)


def test_grounding_need_true_for_data_questions():
    assert grounding_need("summarize my unread emails")
    assert grounding_need("what meetings do I have tomorrow?")
    assert grounding_need("find the Q3 budget sheet in my drive")
    assert grounding_need("draft my weekly status update")


def test_grounding_need_false_for_generic_questions():
    assert not grounding_need("write me a haiku about the ocean")
    assert not grounding_need("what is retrieval augmented generation?")
    assert not grounding_need("explain how OAuth authorization code flow works")
    assert not grounding_need("")


def test_apply_verdict_appends_caveat_when_ungrounded():
    answer = "You have a 1:1 with Sarah at 4pm."
    v = Verdict(status="ungrounded", unsupported=["1:1 with Sarah at 4pm"], checked=True)
    out = apply_verdict(answer, v)
    assert out != answer
    assert "couldn't verify" in out.lower()
    assert "1:1 with Sarah at 4pm" in out


def test_apply_verdict_silent_when_grounded():
    answer = "You have a Standup at 9:30am."
    for status in ("grounded", "not_required", "no_context", "unverified"):
        v = Verdict(status=status)
        assert apply_verdict(answer, v) == answer


def test_build_citations_maps_block_labels():
    blocks = {
        "GMAIL_INBOX": "...",
        "CALENDAR_UPCOMING": "...",
    }
    cites = build_citations(blocks, ["Gmail", "Google Calendar"])
    sources = {c["source"] for c in cites}
    assert "Gmail" in sources
    assert "Google Calendar" in sources
    assert len(cites) == 2


def test_build_citations_falls_back_to_sources_used():
    cites = build_citations({}, ["Outlook"])
    assert cites == [{"source": "Outlook", "label": "Outlook"}]


def test_verdict_trustworthiness():
    assert Verdict(status="grounded").is_trustworthy
    assert Verdict(status="not_required").is_trustworthy
    assert not Verdict(status="ungrounded").is_trustworthy
    assert not Verdict(status="unverified").is_trustworthy
