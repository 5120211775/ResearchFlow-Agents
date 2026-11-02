"""The engine's ports."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class EventSink(Protocol):
    """Receives one pipeline event. Installed via `events.set_emitter`.

    The server implementation writes an `agent_logs` row (durable SSE replay) and then
    publishes to Redis (live fan-out); a desktop one pushes onto an in-process queue.
    """

    async def __call__(self, session_id: str, event: dict) -> None: ...


@runtime_checkable
class Cache(Protocol):
    """Best-effort key/value cache for search results. Installed via `cache.set_cache`.

    Implementations may fail or no-op freely: `retrievers.search` treats every cache
    operation as advisory and never lets one break a search.
    """

    async def get(self, key: str) -> str | None: ...

    async def set(self, key: str, value: str, ttl: int) -> None: ...


@runtime_checkable
class Embeddings(Protocol):
    """Turns text into vectors for project memory (docs/14 §4).

    A third port rather than a direct client call, for the same reason as the other two:
    the server embeds through Ollama or a hosted provider, and the desktop build embeds
    locally with no key at all. The engine states the shape; hosts supply the client.

    `model_id` and `dimensions` are part of the contract, not decoration. Vectors from
    different models are not comparable even when their dimensions match, so every stored
    chunk records the model that produced it and retrieval filters on it — mixing them
    silently would return confident nonsense (docs/14 §4).
    """

    @property
    def model_id(self) -> str: ...

    @property
    def dimensions(self) -> int: ...

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


@runtime_checkable
class Corpus(Protocol):
    """A closed document corpus as a search source (docs/12 M10, docs/13 §8).

    Corpus-only mode's retrieval connector. `search` returns exactly the shape
    `retrievers.search` returns — `{title, url, snippet}` — so the executor needs no
    branch and the graph does not change; `read` resolves one of those URLs back to
    the text at that exact document location, replacing `read_webpage`'s fetch.

    Installed via `corpus.set_corpus` and selected per-run by `RunConfig.corpus_mode`:
    when that flag is set, `retrievers.search` delegates here exclusively and
    `read_webpage` refuses every non-corpus URL — no retrieval egress (docs/12 M10
    DoD). Model calls are routed separately and are not restricted here.
    """

    async def search(self, query: str, max_results: int) -> list[dict]: ...

    async def read(self, url: str) -> dict: ...
