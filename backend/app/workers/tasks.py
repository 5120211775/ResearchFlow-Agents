"""Celery wrappers for checkpointed research runs.

Tasks do not retry the entire non-idempotent workflow. Resume uses a saved
LangGraph checkpoint; unexpected failures are persisted on the research run.
"""

from __future__ import annotations

import structlog

from app.logconfig import bind_research_run_context, clear_run_context
from app.workers import event_loop
from app.workers.celery_app import celery_app

logger = structlog.get_logger()


# ── Research runs ─────────────────────────────────────────────────────────────────
#
# Separate task names from the session pair, for the reason `resume_plan_gate` is separate: a
# queued Celery message outlives a deploy, so widening an existing task's meaning would
# leave in-flight messages ambiguous about which domain they belong to.
#
# **The `name=` strings are a wire protocol and do not follow the Python identifiers.** They
# are what a message already sitting in the broker carries, so renaming one strands every
# queued run at the moment of an upgrade. Same reasoning as an Alembic revision id: the
# value exists in deployed state, not just in this file.


@celery_app.task(name="run_research_pipeline")
def run_research_pipeline(run_id: str, user_id: str) -> None:
    from app.run_execution import execute_run

    clear_run_context()
    bind_research_run_context(run_id, user_id=user_id)
    logger.info("run_pipeline_task_started")
    try:
        event_loop.run(execute_run(run_id))
        logger.info("run_pipeline_task_finished")
    except Exception as exc:  # noqa: BLE001
        logger.error("run_pipeline_task_failed", error=str(exc), exc_info=True)
        event_loop.run(_mark_run_failed(run_id, str(exc)))


@celery_app.task(name="resume_research_pipeline")
def resume_research_pipeline(
    run_id: str, user_id: str, approved: bool, feedback: str | None = None
) -> None:
    from app.run_execution import execute_run

    clear_run_context()
    bind_research_run_context(run_id, user_id=user_id, approved=approved)
    logger.info("run_resume_task_started")
    try:
        event_loop.run(execute_run(run_id, resume=(approved, feedback)))
        logger.info("run_resume_task_finished")
    except Exception as exc:  # noqa: BLE001
        logger.error("run_resume_task_failed", error=str(exc), exc_info=True)
        event_loop.run(_mark_run_failed(run_id, str(exc)))


@celery_app.task(name="resume_research_plan_gate")
def resume_research_plan_gate(run_id: str, user_id: str, plan: dict) -> None:
    from app.run_execution import execute_run

    clear_run_context()
    bind_research_run_context(run_id, user_id=user_id)
    logger.info("run_plan_resume_task_started")
    try:
        event_loop.run(execute_run(run_id, plan=plan))
        logger.info("run_plan_resume_task_finished")
    except Exception as exc:  # noqa: BLE001
        logger.error("run_plan_resume_task_failed", error=str(exc), exc_info=True)
        event_loop.run(_mark_run_failed(run_id, str(exc)))


async def _mark_run_failed(run_id: str, error: str) -> None:
    """A crash outside the adapter still has to leave the run FAILED, not RUNNING forever.

    The run counterpart of `_mark_failed`, and the same shape: its own session, its own Redis
    lifecycle, and it never raises — a failure here would lose the only record of the first
    failure.
    """
    import uuid

    from sqlalchemy import select

    from app import run_lifecycle
    from app.db.base import AsyncSessionLocal, engine
    from app.db.redis import close_redis_pool, init_redis_pool, publish_event
    from app.models.research import ResearchRun

    await init_redis_pool()
    try:
        async with AsyncSessionLocal() as db:
            run = (
                await db.execute(select(ResearchRun).where(ResearchRun.id == uuid.UUID(run_id)))
            ).scalar_one_or_none()
            if run:
                # Through `record_failure` rather than assigning status here, so this path
                # inherits the cancelled-run rule instead of being a second place that has
                # to remember it (issue #54). Writing FAILED over a CANCELLED run violates
                # `ck_run_cancelled` and raises — from the one function whose docstring
                # promises it never does.
                await run_lifecycle.record_failure(db, run, error[:500])
                await db.commit()
        await publish_event(run_id, {"type": "FAILED", "data": {"reason": error[:500]}})
    finally:
        await close_redis_pool()
        await engine.dispose()
