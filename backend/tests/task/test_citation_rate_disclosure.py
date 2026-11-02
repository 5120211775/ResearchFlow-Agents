"""Verified-citation rate on the session summary."""
from __future__ import annotations
import pytest
from research_engine.citation_rate import resolution_rate
SOURCES = [{'index': 1, 'url': 'https://a'}, {'index': 2, 'url': 'https://b'}]

def test_every_marker_resolving_is_one():
    assert resolution_rate('A claim [1]. Another [2].', SOURCES) == 1.0

def test_a_marker_with_no_source_drags_the_rate_down():
    assert resolution_rate('A [1]. B [3].', SOURCES) == 0.5

def test_a_report_with_no_citations_is_unmeasured_not_zero():
    """The rule this column exists to respect. An uncited report is not a report whose
    citations all failed — it is a report that made no citable claims, and 0.0 would
    rank it alongside a report whose every marker is broken."""
    assert resolution_rate('Prose with no markers at all.', SOURCES) is None
    assert resolution_rate('', SOURCES) is None

def test_the_sources_section_is_not_counted_as_claims():
    """A report ends with a numbered source list. Counting those markers would inflate
    every rate toward 1.0 regardless of what the body actually cited."""
    body = 'One claim [1].\n\n## Sources\n[1] https://a\n[2] https://b\n'
    assert resolution_rate(body, SOURCES) == 1.0

def test_no_sources_at_all_means_nothing_resolves():
    assert resolution_rate('A claim [1].', []) == 0.0

def test_the_eval_harness_and_the_app_share_one_implementation():
    """`evals/metrics.py::citation_stats` computed this already. Two copies of a
    measurement is how a published number and a displayed number drift apart."""
    from evals.metrics import citation_stats
    text = 'A [1]. B [3].'
    assert citation_stats(text, SOURCES)['resolution_rate'] == resolution_rate(text, SOURCES)

class _FakeDb:

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

def test_the_summary_carries_the_rate_and_the_routing():
    """History filters on both, so both have to survive the response model — the
    `snippets`/`model_routing` bug was Pydantic silently dropping an undeclared field."""
    from app.schemas.research import SessionSummary
    assert 'citation_resolution_rate' in SessionSummary.model_fields
    assert 'model_routing' in SessionSummary.model_fields
    summary = SessionSummary.model_validate({'id': '3f2504e0-4f89-11d3-9a0c-0305e82c3301', 'project_id': '6ba7b810-9dad-11d1-80b4-00c04fd430c8', 'status': 'COMPLETED', 'prompt': 'q', 'research_depth': 'fast', 'total_cost_usd': 0, 'total_tokens_input': 0, 'total_tokens_output': 0, 'created_at': '2026-08-16T00:00:00Z'})
    assert summary.citation_resolution_rate is None
    assert summary.model_routing is None
