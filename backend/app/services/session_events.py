"""One `RunOutcome` → one lifecycle event, for both hosts."""

from __future__ import annotations

from research_engine.events import make_event
from research_engine.runner import RunOutcome

#: `RunOutcome.status` → the event type a client branches on. Both stream stop-lists are
#: written in terms of these names.
EVENT_TYPES: dict[str, str] = {
    "awaiting_plan": "PLAN_READY",
    "awaiting_approval": "HITL_READY",
    "completed": "COMPLETED",
    "failed": "FAILED",
}


def lifecycle_event(outcome: RunOutcome) -> dict:
    """The event to publish once this outcome has been committed.

    Every number is measured off the outcome rather than defaulted. A payload of zeroes
    would satisfy a shape check and tell a client nothing — and on a run that reached a
    gate having spent money, a `cost_usd` of `0.0` would be the unmeasured-vs-zero lie this
    repository treats as a P0 class. `cost_usd` is reported even at the design gate, where
    it is normally zero, because a *resumed* run reaching that gate has a real number.
    """
    event_type = EVENT_TYPES[outcome.status]

    if outcome.status == "awaiting_plan":
        data = {
            "task_count": len(outcome.plan_tasks),
            "outline_section_count": len(outcome.plan_outline),
            "cost_usd": round(outcome.cost_usd, 4),
        }
    elif outcome.status == "awaiting_approval":
        data = {
            "word_count": len((outcome.draft_report or "").split()),
            "source_count": len(outcome.sources),
            "cost_usd": round(outcome.cost_usd, 4),
        }
    elif outcome.status == "failed":
        # In `data`, not `message`: the server has always put it here, and a client
        # reading `data.reason` found nothing on the desktop.
        data = {"reason": outcome.error}
    else:
        data = {
            "elapsed_s": float(outcome.elapsed_seconds or 0),
            "cost_usd": round(outcome.cost_usd, 4),
        }

    return make_event(event_type, data=data)
