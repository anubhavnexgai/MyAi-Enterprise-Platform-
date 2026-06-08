"""Web search + lightweight page fetch (keyless, via DuckDuckGo).

The local model (qwen2.5) has a stale knowledge cutoff and will confidently
invent "recent" facts and fake citations if asked about anything current. This
service grounds research/knowledge answers in REAL, current web results so the
model summarizes facts instead of hallucinating them.

All functions are async and fail-soft (return empty / best-effort on error).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Provider chain
# ---------------------------------------------------------------------------
# Order: configured providers first (SearXNG → Brave → Tavily), keyless ddgs
# LAST so search still works on a fresh install with zero config. Every
# provider normalizes to the {title, url, snippet} shape and fails soft (-> []),
# so a dead/misconfigured provider just falls through to the next one.


def _searxng_url() -> Optional[str]:
    return (os.environ.get("SEARXNG_URL") or "").strip().rstrip("/") or None


# SearXNG/Brave time-filter param values keyed off the day/week/month contract.
_SEARX_TIME = {"day": "day", "week": "week", "month": "month", "year": "year"}
_BRAVE_FRESHNESS = {"day": "pd", "week": "pw", "month": "pm", "year": "py"}


async def _searxng_search(query: str, max_results: int, time_filter: Optional[str]) -> List[Dict[str, str]]:
    base = _searxng_url()
    if not base:
        return []
    params: Dict[str, str] = {"q": query, "format": "json"}
    if time_filter in _SEARX_TIME:
        params["time_range"] = _SEARX_TIME[time_filter]
    try:
        async with httpx.AsyncClient(timeout=12.0) as c:
            r = await c.get(f"{base}/search", params=params,
                            headers={"User-Agent": "MyAi research bot"})
            if r.status_code >= 400:
                return []
            data = r.json()
    except Exception as exc:  # noqa: BLE001
        logger.debug("searxng search failed: %s", exc)
        return []
    out: List[Dict[str, str]] = []
    for item in (data.get("results") or [])[:max_results]:
        out.append({
            "title": (item.get("title") or "").strip(),
            "url": (item.get("url") or "").strip(),
            "snippet": (item.get("content") or "").strip(),
        })
    return out


async def _brave_search(query: str, max_results: int, time_filter: Optional[str]) -> List[Dict[str, str]]:
    key = (os.environ.get("BRAVE_API_KEY") or "").strip()
    if not key:
        return []
    params: Dict[str, Any] = {"q": query, "count": min(max_results, 20)}
    if time_filter in _BRAVE_FRESHNESS:
        params["freshness"] = _BRAVE_FRESHNESS[time_filter]
    try:
        async with httpx.AsyncClient(timeout=12.0) as c:
            r = await c.get(
                "https://api.search.brave.com/res/v1/web/search",
                params=params,
                headers={"Accept": "application/json", "X-Subscription-Token": key},
            )
            if r.status_code >= 400:
                return []
            data = r.json()
    except Exception as exc:  # noqa: BLE001
        logger.debug("brave search failed: %s", exc)
        return []
    out: List[Dict[str, str]] = []
    for item in ((data.get("web") or {}).get("results") or [])[:max_results]:
        out.append({
            "title": (item.get("title") or "").strip(),
            "url": (item.get("url") or "").strip(),
            "snippet": (item.get("description") or "").strip(),
        })
    return out


async def _tavily_search(query: str, max_results: int, time_filter: Optional[str]) -> List[Dict[str, str]]:
    key = (os.environ.get("TAVILY_API_KEY") or "").strip()
    if not key:
        return []
    body: Dict[str, Any] = {
        "api_key": key, "query": query, "max_results": min(max_results, 20),
        "search_depth": "advanced",  # cleaner, more relevant extracted content
    }
    if time_filter in ("day", "week", "month", "year"):
        body["time_range"] = time_filter
    try:
        async with httpx.AsyncClient(timeout=15.0) as c:
            r = await c.post("https://api.tavily.com/search", json=body)
            if r.status_code >= 400:
                return []
            data = r.json()
    except Exception as exc:  # noqa: BLE001
        logger.debug("tavily search failed: %s", exc)
        return []
    out: List[Dict[str, str]] = []
    for item in (data.get("results") or [])[:max_results]:
        out.append({
            "title": (item.get("title") or "").strip(),
            "url": (item.get("url") or "").strip(),
            "snippet": (item.get("content") or "").strip(),
        })
    return out


def _search_sync(query: str, max_results: int) -> List[Dict[str, str]]:
    """Blocking DuckDuckGo text search (run via asyncio.to_thread). Keyless fallback."""
    try:
        from ddgs import DDGS
    except Exception:  # pragma: no cover
        try:
            from duckduckgo_search import DDGS  # older package name
        except Exception:
            logger.warning("ddgs not installed — web search unavailable")
            return []
    out: List[Dict[str, str]] = []
    try:
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                out.append({
                    "title": (r.get("title") or "").strip(),
                    "url": (r.get("href") or r.get("link") or "").strip(),
                    "snippet": (r.get("body") or r.get("snippet") or "").strip(),
                })
    except Exception as exc:  # noqa: BLE001
        logger.warning("web search failed: %s", exc)
    return out


async def _ddgs_search(query: str, max_results: int, time_filter: Optional[str]) -> List[Dict[str, str]]:
    # ddgs has no reliable time filter via this API — time_filter is ignored.
    return await asyncio.to_thread(_search_sync, query, max_results)


# Provider chain: keyless ddgs is always last so search works with zero config.
_PROVIDERS = (_searxng_search, _brave_search, _tavily_search, _ddgs_search)

# In-process TTL cache — deep research issues repeat/near-repeat queries.
_CACHE_TTL_S = 600  # ~10 min
_cache: Dict[Tuple[str, int, str], Tuple[float, List[Dict[str, str]]]] = {}


def _cache_get(key: Tuple[str, int, str]) -> Optional[List[Dict[str, str]]]:
    hit = _cache.get(key)
    if hit and (time.monotonic() - hit[0]) < _CACHE_TTL_S:
        return hit[1]
    if hit:
        _cache.pop(key, None)
    return None


async def web_search(
    query: str, max_results: int = 6, *, time_filter: Optional[str] = None
) -> List[Dict[str, str]]:
    """Return up to ``max_results`` live web results: {title, url, snippet}.

    Tries configured providers (SearXNG → Brave → Tavily) then the keyless ddgs
    fallback, returning the first non-empty result set. Results are TTL-cached.
    ``time_filter`` (day/week/month/year) is honored where the provider supports
    it and ignored otherwise. Fail-soft: returns [] only if every provider fails.
    """
    if not query or not query.strip():
        return []
    q = query.strip()
    key = (q.lower(), max_results, time_filter or "")
    cached = _cache_get(key)
    if cached is not None:
        return cached
    results: List[Dict[str, str]] = []
    for provider in _PROVIDERS:
        try:
            results = await provider(q, max_results, time_filter)
        except Exception as exc:  # noqa: BLE001 — provider isolation
            logger.debug("provider %s failed: %s", getattr(provider, "__name__", "?"), exc)
            results = []
        results = [r for r in results if r.get("url")]
        if results:
            break
    if results:
        _cache[key] = (time.monotonic(), results)
    return results


# NOTE: only LINEAR regexes here. A backtracking pattern like
# `<(script|style)[^>]*>.*?</\1>` (DOTALL) can hang for seconds on a big page,
# and because Python's `re` holds the GIL while matching, that freezes the WHOLE
# event loop even from a worker thread. We strip script/style with a linear
# string scan instead, and only use the linear `<[^>]+>` tag regex.
_HTML_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_MAX_HTML_CHARS = 200_000  # bound input; readable content is near the top anyway


def _drop_blocks(html: str, tag: str) -> str:
    """Remove <tag>...</tag> blocks with a linear scan (no regex backtracking)."""
    low = html.lower()
    open_t, close_t = f"<{tag}", f"</{tag}>"
    out: list[str] = []
    i = 0
    while True:
        start = low.find(open_t, i)
        if start == -1:
            out.append(html[i:])
            break
        out.append(html[i:start])
        end = low.find(close_t, start)
        if end == -1:
            break  # unclosed → drop the rest
        i = end + len(close_t)
    return "".join(out)


def _strip_html(html: str) -> str:
    html = _drop_blocks(html, "script")
    html = _drop_blocks(html, "style")
    text = _HTML_RE.sub(" ", html)          # linear, no backtracking
    return _WS_RE.sub(" ", text).strip()


async def fetch_page_text(url: str, max_chars: int = 2500) -> str:
    """Fetch a URL and return readable-ish plain text (HTML stripped).

    The HTML→text regex pass runs in a worker thread (and on a size-capped input)
    so a large page never blocks the event loop — important under deep research,
    which fetches many pages.
    """
    if not url:
        return ""
    try:
        async with httpx.AsyncClient(
            timeout=12.0, follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (MyAi research bot)"},
        ) as client:
            r = await client.get(url)
            if r.status_code >= 400 or "text/html" not in r.headers.get("content-type", "text/html"):
                return ""
            html = r.text[:_MAX_HTML_CHARS]
    except Exception as exc:  # noqa: BLE001
        logger.debug("page fetch failed %s: %s", url, exc)
        return ""
    try:
        text = await asyncio.to_thread(_strip_html, html)
    except Exception:  # noqa: BLE001
        return ""
    return text[:max_chars]


async def build_research_context(
    query: str, *, deep: bool = False, max_results: int = 6
) -> Tuple[str, List[Dict[str, str]]]:
    """Search the web and assemble a grounding block + source list.

    ``deep=True`` also fetches the text of the top 3 pages for richer context
    (slower). Returns ``(context_block, sources)``; both empty if search failed.
    """
    results = await web_search(query, max_results=max_results)
    if not results:
        return "", []

    lines = [
        "WEB SEARCH RESULTS — these are REAL, current results from the live web. "
        "Base your answer ONLY on them and cite the URLs. Do not add facts that "
        "are not supported here.",
        "",
    ]
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] {r['title']}\n    URL: {r['url']}\n    {r['snippet']}")

    if deep:
        pages = await asyncio.gather(
            *(fetch_page_text(r["url"]) for r in results[:3]), return_exceptions=True
        )
        for i, body in enumerate(pages, 1):
            if isinstance(body, str) and body:
                lines.append(f"\n--- Full text of [{i}] {results[i-1]['url']} ---\n{body}")

    return "\n".join(lines), results
