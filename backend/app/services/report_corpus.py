"""Auto-save an approved report into its project's corpus."""

from __future__ import annotations

import structlog

from research_engine.corpus import CorpusStore, Ingested

logger = structlog.get_logger()


async def ingest_report(
    store: CorpusStore,
    *,
    report_id: str,
    report_markdown: str | None,
) -> Ingested | None:
    """Best-effort. Never raises: a failed save must not fail an already-approved run,
    only mean the report doesn't show up in the corpus (mirrors `_ingest_into_project_memory`'s
    own guarantee, and for the same reason).

    `report_id` is polymorphic — a `sessions.id` or a `research_runs.id`, whichever pipeline
    produced the report — and is named the way `app/services/memory.py::ingest_report` names
    the same thing, for the same reason. It used to be called `session_id`, and both the
    filename and the log line said so, which meant an approved *run* logged
    `report_auto_ingested session_id=<a research_runs.id>`: a session that does not exist,
    in the one field a reader uses to tell the two pipelines apart.

    Filename is keyed by that id, which makes ingestion idempotent the same way a
    re-uploaded file is: `CorpusStore._prepare_sync` dedupes on (filename, sha256), so
    calling this twice for one report — a retried Celery task, a duplicate webhook — is a
    no-op the second time rather than a duplicate document. **The value is unchanged**, so
    every document already stored keeps its name.
    """
    if not report_markdown or not report_markdown.strip():
        return None
    try:
        result = await store.ingest(
            f"report-{report_id}.md",
            report_markdown.encode("utf-8"),
            origin="generated",
        )
        logger.info(
            "report_auto_ingested",
            report_id=report_id,
            skipped=result.skipped,
            reason=result.reason,
        )
        return result
    except Exception as e:  # noqa: BLE001 — see docstring: never fail an approved run
        logger.warning("report_auto_ingest_failed", report_id=report_id, error=str(e))
        return None
