"""How much of a report's citation apparatus actually resolves."""

from __future__ import annotations

from research_engine.claims import body_before_sources, extract_citations

__all__ = ["body_before_sources", "cited_indices", "resolution_rate"]


def cited_indices(text: str) -> list[int]:
    """Every cited index in the body, in order, duplicates kept.

    A thin alias for `claims.extract_citations`, kept because callers and docs refer to
    this name and because "which indices does this report cite" reads better here than in
    a module about claims.
    """
    return extract_citations(text)


def resolution_rate(report: str, sources: list[dict] | None) -> float | None:
    """Fraction of in-text markers that point at a real source, or `None` if unmeasured.

    `None` when the body carries no markers at all. Not 0.0: see the module docstring.
    """
    cites = cited_indices(body_before_sources(report))
    if not cites:
        return None
    valid = {
        s.get("index")
        for s in (sources or [])
        if isinstance(s, dict) and isinstance(s.get("index"), int)
    }
    resolved = sum(1 for n in cites if n in valid)
    return round(resolved / len(cites), 4)
