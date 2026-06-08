"""Learning loop — the agent grows with use (hermes-agent inspired).

After a successful *multi-tool* turn (a non-trivial task the agent actually
worked at), we distill a reusable **procedural skill**: which tools, in what
order, worked for this kind of request. Next time a similar request arrives,
the skill is retrieved and injected as a hint (via the Life-Harness H5 layer),
so the agent reuses what worked instead of rediscovering it — and skills that
keep working are reinforced (usage count) and ranked higher.

Skills capture *how* to do something (the tool strategy), never the user's
content, so they are safe to reuse across a deployment. Persisted as JSON at
``data/learned_skills.json``. Pure stdlib, fail-soft (never raises into a turn).
"""

from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_MAX_SKILLS = 300
_MIN_TOOLS_TO_LEARN = 1  # learn from any successful tool-using turn (reinforced on repeat)

_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "to", "of", "in", "on", "for", "with",
    "me", "my", "you", "your", "is", "are", "can", "could", "please", "show",
    "tell", "give", "what", "whats", "how", "do", "does", "i", "it", "this",
    "that", "about", "from", "get", "find", "some", "any", "then", "results",
    "result", "into", "go", "come", "back", "want", "need", "would", "should",
}


def _path() -> Path:
    try:
        from app.config import settings
        d = settings.data_dir
    except Exception:
        d = Path("data")
        d.mkdir(parents=True, exist_ok=True)
    return d / "learned_skills.json"


def _load() -> List[Dict[str, Any]]:
    p = _path()
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8")) or []
    except Exception:
        return []


def _save(skills: List[Dict[str, Any]]) -> None:
    try:
        _path().write_text(json.dumps(skills, indent=2), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        logger.debug("could not persist learned skills: %s", exc)


def _keywords(text: str) -> List[str]:
    words = re.findall(r"[a-z0-9]{3,}", (text or "").lower())
    seen, out = set(), []
    for w in words:
        if w in _STOPWORDS or w in seen:
            continue
        seen.add(w)
        out.append(w)
    return out[:8]


def _gist(message: str) -> str:
    return " ".join((message or "").strip().split()[:10])


def _jaccard(a: List[str], b: List[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def get_learned_skills(message: str, top_k: int = 2) -> List[Dict[str, Any]]:
    """Return up to ``top_k`` learned skills relevant to ``message``.

    Each dict has at least a ``hint`` key (what the Life-Harness H5 layer reads).
    """
    kw = _keywords(message)
    if not kw:
        return []
    skills = _load()
    scored = []
    for s in skills:
        overlap = _jaccard(kw, s.get("keywords", []))
        if overlap <= 0:
            continue
        # Rank by relevance, lightly boosted by how often the skill has helped.
        score = overlap * (1.0 + min(s.get("uses", 1), 10) * 0.05)
        scored.append((score, s))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [s for _, s in scored[:top_k]]


def try_extract_skill(message: str, tools_used: List[str], reply: str) -> bool:
    """Learn (or reinforce) a procedural skill from a successful turn.

    Returns True if a skill was created or reinforced. Best-effort; never raises.
    """
    try:
        # Only learn from genuinely multi-step turns that produced a real answer.
        chain = [t for t in (tools_used or []) if t and t.replace("_", "").isalnum()]
        if len(chain) < _MIN_TOOLS_TO_LEARN or not (reply or "").strip():
            return False

        kw = _keywords(message)
        if not kw:
            return False

        try:
            from app.agent.harness import classify_task
            task_type = classify_task(message)
        except Exception:
            task_type = "general"

        seq = " → ".join(chain)
        hint = (f"For requests like “{_gist(message)}” ({task_type}), "
                f"an approach that worked before: {seq}.")

        with _LOCK:
            skills = _load()
            # Reinforce an existing similar skill instead of duplicating.
            for s in skills:
                if s.get("task_type") == task_type and _jaccard(kw, s.get("keywords", [])) >= 0.6:
                    s["uses"] = int(s.get("uses", 1)) + 1
                    s["tools"] = chain
                    s["hint"] = hint
                    _save(skills)
                    return True
            skills.append({
                "task_type": task_type,
                "keywords": kw,
                "tools": chain,
                "hint": hint,
                "uses": 1,
            })
            # Cap: drop least-used when over the limit.
            if len(skills) > _MAX_SKILLS:
                skills.sort(key=lambda s: s.get("uses", 1), reverse=True)
                skills = skills[:_MAX_SKILLS]
            _save(skills)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("skill extraction skipped: %s", exc)
        return False


def stats() -> Dict[str, Any]:
    skills = _load()
    return {
        "count": len(skills),
        "total_uses": sum(int(s.get("uses", 1)) for s in skills),
        "top": sorted(skills, key=lambda s: s.get("uses", 1), reverse=True)[:5],
    }
