"""Lightweight semantic memory (Phase 2 RAG layer).

Recall over past chat turns + harvested contact/email summaries, WITHOUT a heavy
vector DB. Embeddings come from llm_client.embed (Ollama nomic-embed-text or the
openai_compat endpoint); vectors are stored as JSON in the ``semantic_memory``
table and scored with a pure-Python cosine at recall time. Hybrid score = vector
cosine + keyword Jaccard, so recall still degrades gracefully to keyword-only if
embeddings are unavailable (matches MyAi's fail-soft norm).

This COMPLEMENTS — does not replace — learned_skills.json, contact memory, and the
harvester cache.
"""
from __future__ import annotations

import hashlib
import logging
import math
import re
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from app.storage.models import SemanticMemory
from app.tenants.router import get_tenant_router

logger = logging.getLogger(__name__)

_DEDUP_COSINE = 0.92      # near-duplicate threshold (Odysseus's value)
_RECALL_MIN_SCORE = 0.30  # drop weak hits
_MAX_TEXT_CHARS = 1500
_DEDUP_WINDOW = 300       # near-dup cosine only vs this many recent rows
_RECALL_WINDOW = 500      # recall scans at most this many recent rows


def _hash(text: str) -> str:
    return hashlib.sha1(text.strip().lower().encode("utf-8")).hexdigest()[:16]


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set:
    return {t for t in _TOKEN_RE.findall((text or "").lower()) if len(t) > 2}


def _jaccard(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    return inter / union if union else 0.0


async def _embed_one(text: str) -> List[float]:
    from app.services.llm_client import get_llm_client
    vecs = await get_llm_client().embed([text[:_MAX_TEXT_CHARS]])
    return vecs[0] if vecs else []


async def add_memory(
    user_id: str, tenant_id: str, text: str, *, kind: str = "chat",
    ref: Optional[str] = None,
) -> bool:
    """Embed and upsert a memory snippet for this user. Returns True if stored.

    No-ops (returns False) when embeddings are unavailable or the snippet is a
    near-duplicate of something already remembered (cosine > 0.92). Best-effort:
    never raises — callers fire-and-forget.
    """
    text = (text or "").strip()
    if not text:
        return False
    try:
        h = _hash(text)
        vec = await _embed_one(text)
        router_db = get_tenant_router()
        async with router_db.session_for(tenant_id) as session:
            base = (SemanticMemory.tenant_id == tenant_id,
                    SemanticMemory.creator_id == user_id)
            # Exact-dup: indexed hash lookup (no full-table scan).
            dup = (await session.execute(
                select(SemanticMemory.id).where(*base)
                .where(SemanticMemory.content_hash == h).limit(1)
            )).first()
            if dup:
                return False
            # Near-dup: cosine only against a bounded window of recent rows.
            if vec:
                recent = (await session.execute(
                    select(SemanticMemory.embedding).where(*base)
                    .order_by(SemanticMemory.id.desc()).limit(_DEDUP_WINDOW)
                )).scalars().all()
                for emb in recent:
                    if emb and _cosine(vec, emb) > _DEDUP_COSINE:
                        return False
            session.add(SemanticMemory(
                tenant_id=tenant_id, creator_id=user_id, kind=kind, ref=ref,
                text=text[:_MAX_TEXT_CHARS], embedding=vec or None, content_hash=h,
            ))
            await session.commit()
        return True
    except Exception as exc:  # noqa: BLE001 — memory is best-effort
        logger.debug("add_memory skipped: %s", exc)
        return False


async def recall_semantic(
    user_id: str, tenant_id: str, query: str, k: int = 4
) -> List[Dict[str, Any]]:
    """Return up to k relevant past memories for the query, best-first.

    Hybrid score = 0.7*cosine + 0.3*Jaccard when embeddings are available, else
    pure keyword Jaccard. Fail-soft: returns [] on any error.
    """
    query = (query or "").strip()
    if not query:
        return []
    try:
        qvec = await _embed_one(query)
        q_tokens = _tokens(query)  # tokenize the query ONCE, not per row
        router_db = get_tenant_router()
        async with router_db.session_for(tenant_id) as session:
            # Bounded window of most-recent memories (not the whole table).
            rows = (
                await session.execute(
                    select(SemanticMemory)
                    .where(SemanticMemory.tenant_id == tenant_id)
                    .where(SemanticMemory.creator_id == user_id)
                    .order_by(SemanticMemory.id.desc())
                    .limit(_RECALL_WINDOW)
                )
            ).scalars().all()
        scored: List[Dict[str, Any]] = []
        for row in rows:
            r_tokens = _tokens(row.text)
            inter = len(q_tokens & r_tokens)
            union = len(q_tokens | r_tokens)
            kw = inter / union if union else 0.0
            if qvec and row.embedding:
                score = 0.7 * _cosine(qvec, row.embedding) + 0.3 * kw
            else:
                score = kw
            if score >= _RECALL_MIN_SCORE:
                scored.append({"text": row.text, "kind": row.kind,
                               "ref": row.ref, "score": round(score, 3)})
        scored.sort(key=lambda r: r["score"], reverse=True)
        return scored[:k]
    except Exception as exc:  # noqa: BLE001
        logger.debug("recall_semantic failed: %s", exc)
        return []
