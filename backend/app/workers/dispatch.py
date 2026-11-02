"""The server's `RunDispatcher`: hand the run to a Celery worker over the broker."""

from __future__ import annotations

from app.run_dispatch import RunDispatcher


class CeleryDispatcher:
    """The server's mechanism: hand the run to a worker over the broker.

    Every import is deferred to the call. `app.workers.tasks` pulls in Celery and the whole
    pipeline module tree, and this module is imported by the run routes on *both* hosts — an
    import at module scope here would reintroduce the packaged-app `ModuleNotFoundError`
    this seam exists to remove.
    """

    async def start(self, run_id: str, user_id: str) -> None:
        from app.workers.tasks import run_research_pipeline

        run_research_pipeline.delay(run_id, user_id)

    async def resume_plan(self, run_id: str, user_id: str, plan: dict) -> None:
        from app.workers.tasks import resume_research_plan_gate

        resume_research_plan_gate.delay(run_id, user_id, plan)

    async def rework(self, run_id: str, user_id: str, feedback: str | None) -> None:
        from app.workers.tasks import resume_research_pipeline

        resume_research_pipeline.delay(run_id, user_id, False, feedback)


def get_run_dispatcher() -> RunDispatcher:
    """FastAPI dependency: the mechanism this deployment uses.

    The server resolves it through `Depends`. The desktop sidecar calls the same handler
    functions directly and passes its own dispatcher explicitly — the pattern it already
    uses for `db` and `user`, so nothing about the handler changes shape per host.
    """
    return CeleryDispatcher()
