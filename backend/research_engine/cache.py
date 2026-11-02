"""Search-cache indirection."""

from __future__ import annotations

from contextvars import ContextVar

from research_engine.ports import Cache


class NullCache:
    """Caches nothing. The default, so no host is required."""

    async def get(self, key: str) -> str | None:  # noqa: ARG002 - protocol shape
        return None

    async def set(self, key: str, value: str, ttl: int) -> None:  # noqa: ARG002
        return None


_NULL: Cache = NullCache()

_cache: ContextVar[Cache] = ContextVar("engine_cache", default=_NULL)


def set_cache(cache: Cache):
    """Install a cache for the current context. Returns a token for `reset_cache`."""
    return _cache.set(cache)


def reset_cache(token) -> None:
    _cache.reset(token)


def get_cache() -> Cache:
    return _cache.get()
