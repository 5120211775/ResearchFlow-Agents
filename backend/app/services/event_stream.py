"""One SSE frame generator, for four streams across two hosts."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable, Sequence


def _frame(event_id: int | None, payload: dict) -> str:
    """One SSE frame. No `id:` line when the event has no durable identity.

    An event whose row could not be written is still worth delivering — a live listener
    should see it — but a client must not take it as a resume point, because a reconnect
    from that id would find nothing. Omitting the line leaves `Last-Event-ID` where it was,
    which is exactly what the protocol says it means.
    """
    body = f"data: {json.dumps(payload)}\n\n"
    return body if event_id is None else f"id: {event_id}\n{body}"


async def sse_frames(
    *,
    connected: dict,
    backlog: Iterable[tuple[int | None, dict]],
    live: AsyncIterator[tuple[int | None, dict]],
    replay_stop: Sequence[str],
    terminal_stop: Sequence[str],
    already_done: bool,
    seen_from: int = 0,
) -> AsyncIterator[str]:
    """Replay the backlog, then tail the live feed, per the two stop-lists.

    `seen_from` is the client's `Last-Event-ID`. The backlog query already excludes
    anything at or below it, but the *live* feed does not — a subscription opened before
    the backlog snapshot can carry an event the client has already seen.

    `already_done` short-circuits the tail for a run that has settled or parked at a gate:
    the backlog *is* the stream, and subscribing would hold a connection open on a feed
    nothing will publish to. The caller decides it, because only the caller knows the row.
    """
    yield _frame(None, connected)

    seen_max = seen_from
    for event_id, payload in backlog:
        if event_id is not None:
            seen_max = max(seen_max, event_id)
        yield _frame(event_id, payload)
        if payload.get("type") in replay_stop:
            return

    if already_done:
        return

    async for event_id, payload in live:
        # The subscription is opened before the backlog is read, so the two overlap by
        # construction and the same event can arrive twice.
        if event_id is not None and event_id <= seen_max:
            continue
        yield _frame(event_id, payload)
        if event_id is not None:
            seen_max = event_id
        if payload.get("type") in terminal_stop:
            return
