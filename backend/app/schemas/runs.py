"""Request bodies for the run routes."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field


class CorpusSelectionRequest(BaseModel):
    """Which corpus files a selected-files run may read (internal/rfcs/V3.1-corpus-scope.md §8.1).

    Logical documents (`doc_keys`) and folders, resolved to exact document versions when
    the run is created and frozen on the row. Capped so one request cannot ask the store to
    resolve an unbounded list.
    """

    folder_ids: list[str] = Field(default_factory=list, max_length=5000)
    doc_keys: list[str] = Field(default_factory=list, max_length=5000)


class CreateRunRequest(BaseModel):
    """The domain entry point for a run."""

    project_id: uuid.UUID
    question: str = Field(min_length=1, max_length=2000)
    depth: str = Field("balanced", description="fast | balanced | comprehensive")
    corpus_mode: bool = False
    #: A selected-files run. Setting it implies corpus mode; sending it with an explicit
    #: `corpus_mode: false` is refused rather than guessed at.
    corpus_selection: CorpusSelectionRequest | None = None
    #: Offline: every model call must stay on this machine, or the run refuses to start.
    #: Separate from the selection, which limits evidence, not where model calls go. Implies
    #: corpus mode — an Offline run never reads the web.
    offline: bool = False
    skip_plan_gate: bool = True
    topic_seeds: list | None = None
    outline_template: str | None = None
    #: Per-run routing, one `provider:model` per agent role. `None` means "use whatever
    #: this user's saved preference and the deployment resolve to", which is the existing
    #: behaviour and stays the default — a caller that does not care must not be forced to
    #: name five models.
    #:
    #: Validated in the route rather than here: the check needs the catalog *and*
    #: `app.config`, and nothing in this module may import either (the packaged sidecar
    #: dies at import if it does — issue #50). Typed loosely for the same reason; the
    #: route's `model_routing.validate()` is the authority on shape and providers.
    model_routing: dict[str, str] | None = None
    #: Dispatch the run to the worker. False creates the domain row only — used by tests
    #: that drive the engine in-process, and by any caller that wants to stage a run.
    dispatch: bool = True


class PlanReviewRequest(BaseModel):
    decision: str = Field("APPROVED", description="APPROVED | REWORK_REQUESTED | REJECTED")
    feedback: str | None = None
    #: The reviewer's edited task list, or `None` for "unedited — use what the planner
    #: proposed". `None` and `[]` are deliberately different: the second is a reviewer who
    #: excluded everything, which the route refuses rather than silently treating as "no
    #: edit". An edit is recorded as its own plan version, so the plan a run executed is
    #: the one a later reader sees.
    tasks: list[dict] | None = None
    dispatch: bool = True


class ReportReviewRequest(BaseModel):
    """A decision about a revision. `dispatch` drives the rework resume."""

    revision_version: int | None = Field(
        None, description="Which revision was reviewed. Defaults to the latest."
    )
    decision: str = Field(..., description="APPROVED | REWORK_REQUESTED | REJECTED")
    feedback: str | None = None
    dispatch: bool = True
