"""A run that reached no provider must never be able to present as one that did."""
from __future__ import annotations
import uuid
from dataclasses import replace
from datetime import UTC, datetime
import httpx
import pytest
from sqlalchemy import insert, select
from app import run_execution, run_lifecycle
from app.models.project import Project
from app.models.session import Session as SessionRow
from app.models.user import User
from app.runtime import run_config_from_settings
from tests.sqlite_support import open_db
TOKEN = 'test-demo-token'

def _fake_deployment_config():
    """What `run_config_from_settings()` returns on a `LLM_MODE=fake` deployment."""
    return replace(run_config_from_settings(), llm_mode='fake')

def _real_deployment_config():
    return replace(run_config_from_settings(), llm_mode='real')

class _Db:
    """Enough AsyncSession for `_run_config_for`, which commits the routing snapshot."""

    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1

    async def execute(self, *_a, **_k):

        class _R:

            def scalar_one_or_none(self):
                return None
        return _R()

@pytest.fixture
async def v2_run(tmp_path):
    async with open_db(tmp_path / 'demo.sqlite') as maker, maker() as db:
        now = datetime(2026, 8, 24, tzinfo=UTC)
        uid, pid = (uuid.uuid4(), uuid.uuid4())
        await db.execute(insert(User).values(id=uid, email=f'{uid}@x.invalid', hashed_pw='x', is_active=True, created_at=now))
        await db.execute(insert(Project).values(id=pid, user_id=uid, name='P', created_at=now, updated_at=now))
        await db.commit()
        run = await run_lifecycle.create_run(db, owner_id=uid, project_id=pid, question='q', depth='fast')
        await db.commit()
        yield (db, run)

async def test_v2_a_fake_deployment_records_the_run_as_a_demo(monkeypatch, v2_run):
    """Same rule on the run path, which is the one the product leads with."""
    db, run = v2_run
    monkeypatch.setattr(run_execution, 'run_config_from_settings', _fake_deployment_config)
    cfg = await run_execution.run_config_for_run(db, run)
    await db.commit()
    assert (cfg.llm_mode, cfg.demo) == ('fake', True)
    from app.models.research import ResearchRun
    fresh = (await db.execute(select(ResearchRun).where(ResearchRun.id == run.id))).scalars().one()
    assert fresh.demo is True, 'a scripted run was recorded as real research'

async def test_v2_a_real_deployment_leaves_an_ordinary_run_alone(monkeypatch, v2_run):
    db, run = v2_run
    monkeypatch.setattr(run_execution, 'run_config_from_settings', _real_deployment_config)
    cfg = await run_execution.run_config_for_run(db, run)
    await db.commit()
    assert (cfg.llm_mode, cfg.demo) == ('real', False)
    assert run.demo is False
