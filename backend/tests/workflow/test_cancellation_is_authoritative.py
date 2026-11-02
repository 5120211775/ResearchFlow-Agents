"""Cancellation is authoritative: a stopped run cannot be un-stopped by its own outcome."""
from __future__ import annotations
import asyncio
import contextlib
import uuid
from datetime import UTC, datetime
import httpx
import pytest
from sqlalchemy import insert, select
from app import run_execution, run_lifecycle, session_lifecycle
from app.models.project import Project
from app.models.research import ResearchRun
from app.models.session import Session as SessionRow
from app.models.session import SessionStatus
from app.models.user import User
from research_engine.runner import RunOutcome
from tests.sqlite_support import open_db
TOKEN = 'test-cancel-token'
COMPLETING_OUTCOME = RunOutcome(status='awaiting_approval', draft_report='# Findings\n\nThe retriever improved recall [1].', sources=[{'index': 1, 'title': 'A paper', 'url': 'https://example.invalid/a'}], cost_usd=0.5, tokens_input=1200, tokens_output=340)

@pytest.fixture
async def v2_run(tmp_path):
    """A real ResearchRun on a real database, mid-flight.

    A real DB rather than a fake session because the guard's absence used to be caught by
    `ck_run_cancelled` — the CHECK constraint that ties CANCELLED to `cancelled_at` — and a
    fake would not have that constraint. Reproducing the original failure needs the schema
    that produced it.
    """
    async with open_db(tmp_path / 'v2.sqlite') as maker, maker() as db:
        now = datetime(2026, 8, 24, tzinfo=UTC)
        uid, pid = (uuid.uuid4(), uuid.uuid4())
        await db.execute(insert(User).values(id=uid, email=f'{uid}@x.invalid', hashed_pw='x', is_active=True, created_at=now))
        await db.execute(insert(Project).values(id=pid, user_id=uid, name='P', created_at=now, updated_at=now))
        await db.commit()
        run = await run_lifecycle.create_run(db, owner_id=uid, project_id=pid, question='q', depth='fast')
        await run_lifecycle.set_status(db, run, 'RUNNING')
        await db.commit()
        yield (db, run, uid)

async def _reload(db, run_id) -> ResearchRun:
    """Re-read the row, never a cached attribute. What a user's reload at t2 would see."""
    return (await db.execute(select(ResearchRun).where(ResearchRun.id == run_id))).scalars().one()

async def test_v2_a_cancelled_run_survives_a_completing_outcome(v2_run):
    """t0 → t1 → t2 on the run adapter.

    Negative control: delete the `run.status == "CANCELLED"` guard at the top of
    `persist_outcome` and this fails — not with AWAITING_REVIEW, but with an IntegrityError
    on `ck_run_cancelled`, because the schema refuses the status change the code attempted.
    That accident is what kept the data correct before this fix; it is not a decision, and
    it cost the spend (see the next test).
    """
    db, run, _ = v2_run
    await run_lifecycle.request_cancel(db, run, by=run.owner_id)
    await db.commit()
    assert (await _reload(db, run.id)).status == 'CANCELLED'
    result = await run_execution.persist_outcome(db, await _reload(db, run.id), COMPLETING_OUTCOME, state={'evidence': []})
    await db.commit()
    assert result.status == 'CANCELLED', 'the adapter reported a status the user did not choose'
    after = await _reload(db, run.id)
    assert after.status == 'CANCELLED', f'a stopped run came back as {after.status}'
    assert after.cancelled_at is not None

async def test_v2_a_cancelled_run_keeps_the_spend_it_incurred(v2_run):
    """Tokens burned between the stop and the pipeline noticing are real money.

    This is the half the CHECK constraint could never have saved: before the guard the whole
    transaction rolled back, so a run cancelled after $0.50 recorded $0.00 and the spend
    vanished from usage totals.
    """
    db, run, _ = v2_run
    await run_lifecycle.request_cancel(db, run, by=run.owner_id)
    await db.commit()
    await run_execution.persist_outcome(db, await _reload(db, run.id), COMPLETING_OUTCOME, state={'evidence': []})
    await db.commit()
    after = await _reload(db, run.id)
    assert float(after.cost_usd) == pytest.approx(0.5)
    assert after.tokens_input == 1200
    assert after.tokens_output == 340

async def test_v2_a_cancelled_run_writes_no_evidence_and_no_revision(v2_run):
    """The run's conclusion is withheld — only the money is kept.

    A revision is what a reviewer approves, so writing one for a run the user abandoned is
    the whole defect restated one table down.
    """
    db, run, _ = v2_run
    await run_lifecycle.request_cancel(db, run, by=run.owner_id)
    await db.commit()
    result = await run_execution.persist_outcome(db, await _reload(db, run.id), COMPLETING_OUTCOME, state={'evidence': [{'source_url': 'https://example.invalid/a', 'snippet': 'recall improved'}]})
    await db.commit()
    assert result.revision_version is None
    assert result.evidence_count == 0
    assert result.evidence_outcome == 'NOT_READ'

def test_v2_a_cancelled_result_does_not_announce_completed():
    """`lifecycle_event` falls through to COMPLETED for anything it does not name.

    So the guard returning a new status was only half a fix: without a branch here the run
    the user stopped would publish COMPLETED to the workspace. FAILED is the right event —
    it is already on both hosts' `_TERMINAL_EVENTS`, so the stream closes, and `CANCELLED`
    is on neither.
    """
    event = run_execution.lifecycle_event(run_execution.PersistResult(status='CANCELLED', evidence_outcome='NOT_READ'))
    assert event['type'] == 'FAILED'
    assert 'stopped by user' in event['data']['reason'].lower()

async def test_v2_cancellation_survives_a_worker_restart(tmp_path):
    """Durability, which is why this is a column and not the Redis key it replaced.

    The old server flag was `session:{id}:cancelled` with a 1h TTL and no reader anywhere.
    Here the process that cancels and the process that later writes the outcome share
    nothing but the database — which is the actual production topology, where the API
    container cancels and a Celery worker (restarted or not) delivers the outcome.
    """
    db_path = tmp_path / 'restart.sqlite'
    async with open_db(db_path) as maker, maker() as db:
        now = datetime(2026, 8, 24, tzinfo=UTC)
        uid, pid = (uuid.uuid4(), uuid.uuid4())
        await db.execute(insert(User).values(id=uid, email=f'{uid}@x.invalid', hashed_pw='x', is_active=True, created_at=now))
        await db.execute(insert(Project).values(id=pid, user_id=uid, name='P', created_at=now, updated_at=now))
        await db.commit()
        run = await run_lifecycle.create_run(db, owner_id=uid, project_id=pid, question='q', depth='fast')
        run_id = run.id
        await run_lifecycle.set_status(db, run, 'RUNNING')
        await run_lifecycle.request_cancel(db, run, by=uid)
        await db.commit()
    async with open_db(db_path) as maker, maker() as db:
        reloaded = await _reload(db, run_id)
        assert reloaded.status == 'CANCELLED'
        result = await run_execution.persist_outcome(db, reloaded, COMPLETING_OUTCOME, state={'evidence': []})
        await db.commit()
        assert result.status == 'CANCELLED'
        assert (await _reload(db, run_id)).status == 'CANCELLED'

async def test_v2_a_later_failure_cannot_overwrite_a_cancelled_run(v2_run):
    """The error path is a *separate* writer, and it had the same defect.

    `persist_outcome`'s guard covers outcomes the adapter delivers. It does not cover a
    crash: `execute_run`'s corpus guard and `tasks._mark_run_failed` both write FAILED
    directly through `record_failure`, and on a cancelled run that violates
    `ck_run_cancelled` and raises `IntegrityError` — from `_mark_run_failed`, whose own
    docstring promises it never raises, so the original error is lost too.

    Found live rather than by reading: cancelling a run mid-flight and letting its worker
    hit any error produced exactly this, and the run's own error_message ended up being the
    constraint violation.

    Negative control: drop the `run.status == "CANCELLED"` branch from
    `run_lifecycle.record_failure` and this raises IntegrityError instead of asserting.
    """
    db, run, _ = v2_run
    await run_lifecycle.request_cancel(db, run, by=run.owner_id)
    await db.commit()
    await run_lifecycle.record_failure(db, await _reload(db, run.id), 'worker crashed')
    await db.commit()
    after = await _reload(db, run.id)
    assert after.status == 'CANCELLED', f'a stopped run was moved to {after.status}'
    assert after.cancelled_at is not None
    assert after.error_message == 'worker crashed'

async def test_v2_an_uncancelled_run_still_records_failure(v2_run):
    """The control: ordinary failures must still land FAILED."""
    db, run, _ = v2_run
    await run_lifecycle.set_status(db, run, 'RUNNING')
    await run_lifecycle.record_failure(db, run, 'provider quota exhausted')
    await db.commit()
    after = await _reload(db, run.id)
    assert after.status == 'FAILED'
    assert after.error_message == 'provider quota exhausted'

async def test_v2_set_status_refuses_to_move_a_cancelled_run(v2_run):
    """A cancelled run is terminal, and no bare status write may leave it.

    The constraint already enforced this from below, but as an `IntegrityError` three layers
    down inside whatever background task attempted it. Refusing here names the caller.

    Negative control: drop the `run.status == "CANCELLED"` check from `set_status` and this
    raises IntegrityError at the flush instead of LifecycleError.
    """
    db, run, _ = v2_run
    await run_lifecycle.request_cancel(db, run, by=run.owner_id)
    await db.commit()
    with pytest.raises(run_lifecycle.LifecycleError, match='cancelled'):
        await run_lifecycle.set_status(db, await _reload(db, run.id), 'RUNNING')

async def test_v2_a_run_stopped_mid_run_keeps_its_status_and_its_spend(tmp_path, monkeypatch):
    """t0 → t1 through the real `execute_run`, with the stop made while the worker runs.

    The worker loads the run once and holds it for the whole invocation. Negative control:
    remove the re-read before `persist_outcome` and its guard misses the stop, the write
    lands on CANCELLED, `ck_run_cancelled` rejects it, and the rollback takes the $0.50 with
    it — the run keeps its status only by accident, and records no spend.
    """
    from tests.workflow.test_execute_run_records_provenance import _stage
    staged: dict = {}

    async def graph(**_kwargs):
        async with staged['maker']() as db:
            run = await db.get(ResearchRun, uuid.UUID(staged['run_id']))
            await run_lifecycle.request_cancel(db, run, by=run.owner_id)
            await db.commit()
        return COMPLETING_OUTCOME
    ctx, maker, run_id = await _stage(tmp_path, monkeypatch, graph)
    staged.update(maker=maker, run_id=run_id)
    try:
        await run_execution.execute_run(run_id)
        async with maker() as db:
            run = await db.get(ResearchRun, uuid.UUID(run_id))
    finally:
        await ctx.__aexit__(None, None, None)
    assert run.status == 'CANCELLED'
    assert run.cost_usd == pytest.approx(0.5), 'the spend was rolled back with the write'

class _CountingDb:
    """Just enough AsyncSession for `_persist_outcome`, which only ever commits.

    Same shape as `test_plan_gate.py::_FakeDb`. Faking the session is safe *here* — unlike
    the run tests above — because the session guard is a plain attribute read with no constraint
    behind it, so there is no schema behaviour to reproduce.
    """

    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1

def _cancelled_session() -> SessionRow:
    """A session the user stopped: exactly what `cancel_session` leaves behind."""
    row = SessionRow(prompt='q', research_depth='fast')
    row.status = SessionStatus.FAILED
    row.error_message = 'Research stopped by user.'
    row.cancelled_at = datetime.now(UTC)
    return row

async def _pending_session(maker) -> tuple[uuid.UUID, uuid.UUID]:
    """A user, a project, and a session as the start route leaves it: PENDING, handed over."""
    now = datetime(2026, 9, 28, tzinfo=UTC)
    uid, pid, sid = (uuid.uuid4(), uuid.uuid4(), uuid.uuid4())
    async with maker() as db:
        await db.execute(insert(User).values(id=uid, email=f'{uid}@x.invalid', hashed_pw='x', is_active=True, created_at=now))
        await db.execute(insert(Project).values(id=pid, user_id=uid, name='P', created_at=now, updated_at=now))
        db.add(SessionRow(id=sid, user_id=uid, project_id=pid, prompt='q', research_depth='fast', status=SessionStatus.PENDING, corpus_mode=False, demo=False))
        await db.commit()
    return (uid, sid)

async def _stop(maker, uid: uuid.UUID, sid: uuid.UUID) -> None:
    """The user's Stop through the real route function, on a connection of its own."""
    from app.api.v1.research import cancel_session

    async def _no_stream(*_a, **_k) -> None:
        return None
    async with maker() as db:
        await cancel_session(sid, db, await db.get(User, uid), _no_stream)

async def _session(maker, sid: uuid.UUID) -> SessionRow:
    """The row as a reload at t2 reads it, never a cached object."""
    async with maker() as db:
        return (await db.execute(select(SessionRow).where(SessionRow.id == sid))).scalars().one()

async def test_v1_mark_running_moves_a_session_nobody_stopped(tmp_path):
    """The control: the stop check must not swallow an ordinary start."""
    async with open_db(tmp_path / 'start.sqlite') as maker:
        _uid, sid = await _pending_session(maker)
        async with maker() as db:
            assert await session_lifecycle.mark_running(db, sid) is True
            await db.commit()
        assert (await _session(maker, sid)).status == SessionStatus.RUNNING

async def test_v1_a_stop_after_the_drivers_read_beats_its_start_write(tmp_path):
    """The interleaving a check-then-write cannot survive, forced every time.

    The driver has loaded the row — which says nothing of a stop — when the Stop commits.
    Its write has to lose. Negative control: make `mark_running` an unconditional UPDATE and
    the row comes back RUNNING with `cancelled_at` set, which no outcome writer will ever
    move again.
    """
    async with open_db(tmp_path / 'start.sqlite') as maker:
        uid, sid = await _pending_session(maker)
        async with maker() as driver:
            assert (await driver.get(SessionRow, sid)).is_cancelled is False
            await _stop(maker, uid, sid)
            assert await session_lifecycle.mark_running(driver, sid) is False
            await driver.commit()
        row = await _session(maker, sid)
    assert row.status == SessionStatus.FAILED, f'a stopped session came back as {row.status}'
    assert row.is_cancelled

def _auth() -> dict:
    return {'Authorization': f'Bearer {TOKEN}'}

def test_the_write_only_redis_cancellation_key_is_gone():
    """`session:{id}:cancelled` was set by the server and read by nothing, with a 1h TTL.

    Asserted against the source because the defect was an *absence* of a reader, and no
    behavioural test can observe a key nobody consults. Issue #54 required it be given a
    reader or removed; `sessions.cancelled_at` is the reader's replacement, so the key is
    removed and this keeps it from drifting back in.

    Read through the AST rather than as text, so only a **string literal** counts. The first
    draft of this test grepped the file and failed on its own explanatory comments — which
    is the "a guard cannot tell a use from a mention" trap `AGENTS.md` records against the
    frontend's CI greps, reproduced here. A key name in prose is documentation; a key name
    in a literal is the defect.
    """
    import ast
    from pathlib import Path
    backend = Path(__file__).resolve().parents[2]
    offenders: list[str] = []
    for package in (backend / 'app', backend / 'desktop', backend / 'research_engine'):
        for source in package.rglob('*.py'):
            tree = ast.parse(source.read_text(), filename=str(source))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if ':cancelled' in node.value:
                        offenders.append(f'{source.relative_to(backend)}:{node.lineno}')
    assert not offenders, "the TTL'd cancellation key that nothing reads is back in a string literal: " + ', '.join(offenders)
