"""`RunOutcome` must carry the graph's sources out of **every** status branch."""

from __future__ import annotations

import pytest

from research_engine.runner import _outcome

SOURCES = [
    {"url": "https://example.invalid/a", "title": "A", "n": 1},
    {"url": "https://example.invalid/b", "title": "B", "n": 2},
]


class _Interrupt:
    """Stands in for LangGraph's interrupt object, which exposes `.value`.

    A real `Interrupt` carries scheduling internals `_outcome` never reads; the payload is
    the entire contract between the graph and this function.
    """

    def __init__(self, value: dict) -> None:
        self.value = value


#: (case, result, state) covering each branch `_outcome` can return through. The state
#: always carries the same sources, so the only thing under test is whether the branch
#: propagates them.
BRANCHES = [
    ("awaiting_plan", {"__interrupt__": [_Interrupt({"type": "PLAN_READY", "tasks": []})]}, {}),
    ("awaiting_approval", {"__interrupt__": [_Interrupt({"type": "HITL_READY"})]}, {}),
    ("failed", {}, {"error": "provider quota exhausted"}),
    ("completed", {}, {"draft_report": "d", "final_report": "f"}),
]


@pytest.mark.parametrize(("expected_status", "result", "extra_state"), BRANCHES)
def test_every_outcome_branch_preserves_sources(expected_status, result, extra_state):
    outcome = _outcome(result, {"sources": SOURCES, **extra_state})

    assert outcome.status == expected_status, "the case no longer reaches the branch it names"
    assert outcome.sources == SOURCES, (
        f"the '{expected_status}' branch dropped the graph's sources. Because "
        "RunOutcome.sources defaults to an empty list this fails silently: nothing persists "
        "Source rows, and the report renders with no evidence and no claims while "
        "reporting success."
    )


def test_absent_sources_are_an_empty_list_rather_than_none():
    """Downstream iterates `outcome.sources` directly, so `None` would be a TypeError.

    A graph that failed before its first search legitimately has no `sources` key at all;
    that is empty, not missing, and `persist_outcome` should record zero sources rather
    than crash on the way to writing the failure.
    """
    outcome = _outcome({}, {"error": "died before searching"})

    assert outcome.sources == []
    assert outcome.status == "failed"
