"""Search retriever chain."""

from __future__ import annotations

import asyncio
import json

import httpx
import structlog

from research_engine.cache import get_cache
from research_engine.corpus import get_corpus
from research_engine.runconfig import get_run_config

logger = structlog.get_logger()

SearchResult = dict  # {"title": str, "url": str, "snippet": str}
_CACHE_TTL = 86_400


async def _tavily(query: str, max_results: int) -> list[SearchResult]:
    api_key = get_run_config().tavily_api_key
    if not api_key:
        return []
    async with httpx.AsyncClient(
        timeout=20.0, proxy=get_run_config().research_api_proxy or None
    ) as client:
        resp = await client.post(
            "https://api.tavily.com/search",
            json={
                "query": query,
                "max_results": max_results,
            },
            headers={"Authorization": f"Bearer {api_key}"},
        )
        resp.raise_for_status()
        data = resp.json()
    return [
        {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")}
        for r in data.get("results", [])
    ]


async def _brave(query: str, max_results: int) -> list[SearchResult]:
    api_key = get_run_config().brave_api_key
    if not api_key:
        return []
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": max_results},
            headers={"X-Subscription-Token": api_key},
        )
        resp.raise_for_status()
        data = resp.json()
    return [
        {"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("description", "")}
        for r in data.get("web", {}).get("results", [])
    ]


async def _duckduckgo(query: str, max_results: int) -> list[SearchResult]:
    from ddgs import DDGS

    def _run() -> list[dict]:
        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=max_results))

    raw = await asyncio.to_thread(_run)
    return [
        {"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")}
        for r in raw
    ]


_CHAIN = (("tavily", _tavily), ("brave", _brave), ("duckduckgo", _duckduckgo))


async def search(query: str, max_results: int = 5) -> list[SearchResult]:
    """Run the retriever chain with Redis caching. Raises on total exhaustion."""
    if get_run_config().corpus_mode:
        # Corpus-only: the port answers exclusively. NoCorpus raises, and the raise is
        # deliberate — an airgapped run with no corpus must fail visibly, not fall
        # through to the web chain (docs/12 M10 DoD: no retrieval egress).
        return await get_corpus().search(query, max_results)

    cfg = get_run_config()
    if cfg.llm_mode == "fake":
        if cfg.demo:
            from research_engine.demo_fixtures import demo_search

            return demo_search(query, max_results)
        from research_engine.fakes import fake_search

        return fake_search(query, max_results)

    # Cache lookup (best-effort; cache failures never break search). The backend is a
    # host-supplied Cache port — Redis on the server, SQLite on the desktop, null by
    # default (docs/13 §4).
    cache = get_cache()
    cache_key = f"search:{max_results}:{query.strip().lower()}"
    try:
        cached = await cache.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception:  # noqa: BLE001 — an unavailable cache is not a search failure
        pass

    errors: list[str] = []
    for name, fn in _CHAIN:
        try:
            results = await fn(query, max_results)
            if results:
                try:
                    await cache.set(cache_key, json.dumps(results), _CACHE_TTL)
                except Exception:  # noqa: BLE001 — see above
                    pass
                logger.info("retriever_hit", retriever=name, count=len(results))
                return results
        except Exception as e:  # noqa: BLE001 — try the next retriever
            errors.append(f"{name}: {e}")
            logger.warning("retriever_failed", retriever=name, error=str(e))

    raise RuntimeError(
        f"All retrievers failed or returned nothing: {'; '.join(errors) or 'no keys'}"
    )
