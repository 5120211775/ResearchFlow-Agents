"""The Offline guarantee."""
from __future__ import annotations
import asyncio
import uuid
from datetime import UTC, datetime
import httpx
import pytest
from langgraph.checkpoint.memory import MemorySaver
from sqlalchemy import insert
from app import run_execution, run_lifecycle
from app.models.project import Project
from app.models.user import User
from research_engine import offline
from research_engine.corpus import CorpusStore
from research_engine.llm_factory import get_llm
from research_engine.runconfig import RunConfig, reset_run_config, set_run_config
from research_engine.runner import run
from tests.dataflow.test_corpus_egress import _LOOPBACK, no_egress
from tests.dataflow.test_corpus_store import SOLAR_TEXT, FakeEmbeddings
from tests.fake_provider import ROLES, FakeProvider
from tests.sqlite_support import open_db
CLOUD = {role: 'google:gemini-2.5-flash' for role in ROLES}

def _cfg(**kw) -> RunConfig:
    return RunConfig(llm_mode='real', offline=True, corpus_mode=True, **kw)

def test_a_cloud_route_is_named_by_role():
    found = offline.violations(_cfg(models=CLOUD))
    assert len(found) == len(ROLES)
    assert any((line.startswith('planner uses google:') for line in found))

@pytest.mark.parametrize(('models', 'custom_base_url', 'ollama_base_url', 'ok'), [({r: 'ollama:qwen2.5:7b' for r in ROLES}, '', 'http://localhost:11434/v1', True), ({r: 'ollama:qwen2.5:7b' for r in ROLES}, '', 'http://10.0.0.5:11434/v1', False), ({r: 'custom:m' for r in ROLES}, 'http://127.0.0.1:8080/v1', '', True), ({r: 'custom:m' for r in ROLES}, 'https://router.example.com/v1', '', False), ({r: 'custom:m' for r in ROLES}, '', '', False), ({**{r: 'ollama:q' for r in ROLES}, 'critic': 'openrouter:x/y'}, '', '', False)])
def test_locality_is_decided_by_endpoint(models, custom_base_url, ollama_base_url, ok):
    cfg = _cfg(models=models, ollama_base_url=ollama_base_url or 'http://localhost:11434/v1')
    assert (offline.violations(cfg, custom_base_url=custom_base_url) == []) is ok

def test_scripted_runs_and_runs_without_offline_are_never_refused():
    assert offline.violations(RunConfig(llm_mode='fake', offline=True, models=CLOUD)) == []
    assert offline.refusal(RunConfig(llm_mode='real', offline=False, models=CLOUD)) is None

async def test_an_offline_run_on_cloud_routes_is_refused_before_anything_leaves(tmp_path, no_egress):
    store = CorpusStore(tmp_path / 'c.sqlite', FakeEmbeddings())
    await store.ingest('solar.txt', SOLAR_TEXT.encode())
    outcome = await run(checkpointer=MemorySaver(), session_id='offline-refused', user_id='u', query='q', run_config=_cfg(models=CLOUD, skip_plan_gate=False), corpus=store)
    assert outcome.status == 'failed'
    assert outcome.error.startswith(offline.OFFLINE_UNAVAILABLE)
    assert 'planner uses google:' in outcome.error
    assert no_egress == [], f'a refused Offline run still reached for: {no_egress}'

def test_the_client_factory_refuses_too():
    """The backstop: whatever path reaches the factory, no non-local client is built."""
    token = set_run_config(_cfg(models=CLOUD))
    try:
        with pytest.raises(ValueError, match=offline.OFFLINE_UNAVAILABLE):
            get_llm('planner')
    finally:
        reset_run_config(token)

async def test_an_all_local_offline_run_talks_only_to_loopback(tmp_path, no_egress):
    provider = FakeProvider()
    try:
        store = CorpusStore(tmp_path / 'c.sqlite', FakeEmbeddings())
        await store.ingest('solar.txt', SOLAR_TEXT.encode())
        outcome = await run(checkpointer=MemorySaver(), session_id='offline-local', user_id='u', query='How do photovoltaic cells convert sunlight?', run_config=_cfg(models={role: f'custom:{role}' for role in ROLES}, provider_keys={'custom': 'k', 'custom_base_url': provider.base_url}, enforce_ssrf_guards=False, skip_plan_gate=False), corpus=store)
    finally:
        provider.close()
    assert outcome.status == 'awaiting_plan', outcome.error
    assert 'planner' in provider.requests, 'the planner really called the local model'
    assert no_egress, 'the run made model calls, so the guard must have seen them'
    assert set(no_egress) <= _LOOPBACK, f'non-loopback connection attempted: {no_egress}'

async def test_the_server_carries_offline_from_the_row(tmp_path):
    async with open_db(tmp_path / 'o.sqlite') as maker, maker() as db:
        now = datetime(2026, 10, 25, tzinfo=UTC)
        uid, pid = (uuid.uuid4(), uuid.uuid4())
        await db.execute(insert(User).values(id=uid, email=f'{uid}@x.invalid', hashed_pw='x', is_active=True, created_at=now))
        await db.execute(insert(Project).values(id=pid, user_id=uid, name='P', created_at=now, updated_at=now))
        await db.commit()
        row = await run_lifecycle.create_run(db, owner_id=uid, project_id=pid, question='q', corpus_mode=True, offline=True)
        await db.commit()
        cfg = await run_execution.run_config_for_run(db, row)
        assert cfg.offline is True and cfg.corpus_mode is True
