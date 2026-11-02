"""How a research run gets driven, per host."""

from __future__ import annotations

from typing import Protocol


class RunDispatcher(Protocol):
    """Starts and resumes research runs. One implementation per host."""

    async def start(self, run_id: str, user_id: str) -> None:
        """Drive a freshly created run from the beginning."""
        ...

    async def resume_plan(self, run_id: str, user_id: str, plan: dict) -> None:
        """Resume a run suspended at the design gate, with the approved plan."""
        ...

    async def rework(self, run_id: str, user_id: str, feedback: str | None) -> None:
        """Resume a run whose draft was rejected, back into synthesis."""
        ...
