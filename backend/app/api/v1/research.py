"""Research endpoints."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.projects import resolve_project
from app.db.base import AsyncSessionLocal, get_db
from app.db.redis import get_redis
from app.dependencies import get_current_user
from app.models.agent_log import AgentLog
from app.models.audit_log import AuditLog
from app.models.session import Session, SessionStatus
from app.models.user import User
from app.schemas.research import (
    OutlineSectionSchema,
    OutlineTemplateSchema,
    PlanResponse,
    PlanTaskSchema,
    SessionDetail,
    SessionListResponse,
    SessionSummary,
)
from app.services import checkpoints, export
from app.services.event_stream import sse_frames
from app.services.sse import SSE_HEADERS
from research_engine import bundle

#: Replay ends at a true terminal only; the live tail also ends at a gate, because a
#: suspended graph publishes nothing more until a human acts. Both hosts and both surfaces
#: use these two lists — see `app/services/event_stream.py` for why they must differ.
_REPLAY_STOP_EVENTS = ("COMPLETED", "FAILED")
_TERMINAL_EVENTS = ("COMPLETED", "FAILED", "HITL_READY", "PLAN_READY")

logger = structlog.get_logger()
router = APIRouter(prefix="/research", tags=["Research"])


@router.get("", response_model=SessionListResponse)
async def list_sessions(
    page: int = 1,
    limit: int = 20,
    archived: bool = False,
    project_id: uuid.UUID | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List this user's sessions, optionally scoped to one project.

    `archived` selects one list or the other rather than merging them: archiving exists
    to get a session *out* of the way, so the default view must never include it, and
    the archive is a deliberate destination rather than a filter people stumble into.

    `project_id` is validated for ownership before it reaches the query, so it can never
    be used to read another user's project.
    """
    limit = max(1, min(limit, 100))
    filters = [
        Session.user_id == current_user.id,
        Session.archived_at.is_not(None) if archived else Session.archived_at.is_(None),
    ]
    if project_id is not None:
        project = await resolve_project(db, current_user.id, project_id)
        filters.append(Session.project_id == project.id)
    base = select(Session).where(*filters)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (
        (
            await db.execute(
                base.order_by(Session.created_at.desc()).offset((page - 1) * limit).limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return SessionListResponse(
        sessions=[SessionSummary.model_validate(s) for s in rows],
        total=total,
        page=page,
        limit=limit,
    )


@router.get("/outline-templates", response_model=list[OutlineTemplateSchema])
async def list_outline_templates(_: User = Depends(get_current_user)):
    """The report structures offered at the design gate (internal/07 Phase 4).

    Served from `research_engine.outlines` rather than hardcoded in the picker, so the
    sections the UI previews are the sections the synthesizer is actually given. Declared
    above `/{session_id}` because that route parses its segment as a UUID and would 422
    this path rather than falling through to it.
    """
    from research_engine import outlines

    return [OutlineTemplateSchema.model_validate(t) for t in outlines.catalog()]


@router.get("/{session_id}", response_model=SessionDetail)
async def get_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    session = await _authorized_session(db, session_id, current_user.id)
    return SessionDetail.model_validate(session)


@router.get("/{session_id}/stream")
async def stream_events(
    session_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    redis=Depends(get_redis),
):
    await _authorized_session(db, session_id, current_user.id)
    last_event_id = request.headers.get("last-event-id")
    after_id = int(last_event_id) if last_event_id and last_event_id.isdigit() else 0

    async def gen() -> AsyncGenerator[str]:
        from app import adapters

        channel = f"session:{session_id}:events"
        pubsub = redis.pubsub()
        # Subscribe BEFORE snapshotting the backlog. Any event published in the gap
        # between the snapshot and the subscribe would otherwise be lost from both —
        # which stranded fast resume→COMPLETED runs on the monitor. Now such an event
        # is queued on the pubsub and deduped against the backlog by its durable id.
        await pubsub.subscribe(channel)
        try:
            # Snapshot the backlog AFTER subscribing. Uses a fresh session because the
            # request-scoped one is closed once the endpoint returns and streaming begins.
            async with AsyncSessionLocal() as sdb:
                backlog = (
                    (
                        await sdb.execute(
                            select(AgentLog)
                            .where(AgentLog.session_id == session_id, AgentLog.id > after_id)
                            .order_by(AgentLog.id.asc())
                        )
                    )
                    .scalars()
                    .all()
                )

            # The loop is `app/services/event_stream.py`, shared with the run stream and
            # both desktop streams. This route keeps only what genuinely differs: the
            # backlog query and the Redis subscription.
            async for frame in sse_frames(
                connected={"type": "connected"},
                backlog=[(row.id, row.payload) for row in backlog],
                live=adapters.redis_event_stream(pubsub),
                replay_stop=_REPLAY_STOP_EVENTS,
                terminal_stop=_TERMINAL_EVENTS,
                already_done=False,
                seen_from=after_id,
            ):
                yield frame
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


#: The demo banner and the "stamped iff demo" rule now live in `research_engine.bundle`,
#: the host-agnostic export home, so the desktop sidecar stamps `.md` identically (#52).
#: Aliased here because this module and `runs` have always referred to `_DEMO_STAMP`.
_DEMO_STAMP = bundle.DEMO_STAMP_MD


def _report_or_404(session: Session, *, stamp_demo: bool = True) -> str:
    """The report to export, stamped in place if this session was a demo.

    Every export route funnels through here on purpose, and stamping is the default:
    stamping at each call site would work until someone adds a fourth export and forgets,
    and an unstamped demo report is indistinguishable from real research the moment it
    leaves the app.

    `stamp_demo=False` exists for exactly one caller, the bundle. A bundle is a
    *verifiable* artifact: `report_hash` is checked against the `draft_hash` recorded when
    the human approved the draft, so injecting prose into the report body afterwards
    breaks the approval chain and makes every demo bundle fail verification for a reason
    that has nothing to do with its integrity. Teaching a reader that FAIL is normal for
    demos would defeat the verifier far more thoroughly than a missing banner. The bundle
    carries its provenance in the hash-covered `demo` field instead, and the verifier
    prints it above the verdict.
    """
    report = session.final_report or session.draft_report
    if not report:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No report available to export.")
    return bundle.stamp_demo_md(report, demo=bool(session.demo) and stamp_demo)


@router.get("/{session_id}/export.md")
async def export_markdown(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    session = await _authorized_session(db, session_id, current_user.id)
    report = _report_or_404(session) + export.render_model_attribution_md(session.model_routing)
    filename = f"research-{str(session.id)[:8]}.md"
    return Response(
        content=report,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{session_id}/export.pdf")
async def export_pdf(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    session = await _authorized_session(db, session_id, current_user.id)
    report = _report_or_404(session)
    title = (session.prompt or "Research Report")[:120]
    try:
        pdf = export.render_pdf(
            report, session.sources or [], title=title, model_routing=session.model_routing
        )
    except RuntimeError as e:
        # Native PDF libs unavailable in this environment (docs/09 §1 bakes them in).
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, detail=str(e)) from e
    filename = f"research-{str(session.id)[:8]}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{session_id}/export.bundle.json")
async def export_bundle_json(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    session = await _authorized_session(db, session_id, current_user.id)
    if session.status != SessionStatus.COMPLETED:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Bundle export is only available for COMPLETED sessions.",
        )

    # Unstamped on purpose — see `_report_or_404`. The bundle's `demo` field carries the
    # provenance and is covered by `bundle_hash`; prose in the report body would break the
    # approval-chain check instead.
    report = _report_or_404(session, stamp_demo=False)

    agent_logs = (
        (
            await db.execute(
                select(AgentLog)
                .where(AgentLog.session_id == session.id)
                .order_by(AgentLog.id.asc())
            )
        )
        .scalars()
        .all()
    )
    trace = [log.payload for log in agent_logs]

    audit_logs = (
        (
            await db.execute(
                select(AuditLog)
                .where(AuditLog.session_id == session.id)
                .order_by(AuditLog.id.asc())
            )
        )
        .scalars()
        .all()
    )
    approval_chain = [
        {
            "action": al.action,
            "feedback": al.feedback,
            "draft_hash": al.draft_hash,
            "timestamp": al.created_at.isoformat(),
        }
        for al in audit_logs
    ]

    state = await checkpoints.get_thread_state(str(session.id))
    evidence = state.get("evidence", [])
    contradictions = state.get("contradictions", [])

    from research_engine import bundle

    manifest = bundle.assemble(
        session_id=str(session.id),
        query=session.prompt,
        report=report,
        evidence=evidence,
        sources=session.sources or [],
        contradictions=contradictions,
        models=session.model_routing or {},
        cost_usd=float(session.total_cost_usd),
        tokens_input=session.total_tokens_input,
        tokens_output=session.total_tokens_output,
        elapsed_seconds=float(session.elapsed_seconds) if session.elapsed_seconds else None,
        research_depth=session.research_depth,
        approval_chain=approval_chain,
        trace=trace,
        trace_available=True,
        demo=session.demo,
    )

    filename = f"research-{str(session.id)[:8]}.bundle.json"
    return Response(
        content=bundle.serialize(manifest),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _plan_response(session: Session) -> PlanResponse:
    plan = session.plan_json or {}
    outline = session.outline_json or {}
    return PlanResponse(
        session_id=session.id,
        status=session.status,
        tasks=[PlanTaskSchema.model_validate(t) for t in (plan.get("tasks") or [])],
        outline=[OutlineSectionSchema.model_validate(s) for s in (outline.get("sections") or [])],
        approved_at=session.plan_approved_at,
    )


@router.get("/{session_id}/plan", response_model=PlanResponse)
async def get_plan(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """The research design this run is working from (internal/07 Phase 4).

    404 rather than an empty plan when `plan_json` is null: a run that skipped the gate,
    or has not reached it, has no design to show, and returning `{"tasks": []}` for that
    would read as "the planner proposed nothing" — a measurement that was never taken
    rendering as zero, which is the failure mode AGENTS.md opens with.
    """
    session = await _authorized_session(db, session_id, current_user.id)
    if session.plan_json is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail="This session has no research plan to review — it did not use the design gate.",
        )
    return _plan_response(session)


async def _authorized_session(
    db: AsyncSession, session_id: uuid.UUID, user_id: uuid.UUID
) -> Session:
    session = (
        await db.execute(
            select(Session).where(Session.id == session_id, Session.user_id == user_id)
        )
    ).scalar_one_or_none()
    if not session:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Session not found.")
    return session
