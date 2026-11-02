"""A selected-files run: request → frozen row → RunConfig → scoped port, on both hosts."""
from __future__ import annotations
import asyncio
import uuid
from datetime import UTC, datetime
import httpx
import pytest
from sqlalchemy import insert
from app import run_execution
from app.api.v1.runs import create_run
from app.errors import NotFound, Unprocessable
from app.models.project import Project
from app.models.research import ResearchRun
from app.models.user import User
from app.schemas.runs import CreateRunRequest
from research_engine.corpus import CorpusStore, ScopedCorpus
from tests.dataflow.test_corpus_store import SOLAR_TEXT, VENTS_TEXT, FakeEmbeddings
from tests.sqlite_support import open_db
NOW = datetime(2026, 10, 25, tzinfo=UTC)
TOKEN = 'test-selection-token'

class _NoDispatch:

    async def start(self, *_a, **_k):
        raise AssertionError('these tests create with dispatch=False')

class _Locator:
    """The host port that says where a project's corpus lives — here, one real store."""

    def __init__(self, store: CorpusStore | None) -> None:
        self._store = store

    async def for_project(self, project_id, *, keys=None):
        return self._store

@pytest.fixture
async def server(tmp_path):
    store = CorpusStore(tmp_path / 'corpus.sqlite', FakeEmbeddings())
    solar = await store.ingest('solar.txt', SOLAR_TEXT.encode())
    vents = await store.ingest('vents.txt', VENTS_TEXT.encode())
    async with open_db(tmp_path / 'runs.sqlite') as maker, maker() as db:
        uid, pid = (uuid.uuid4(), uuid.uuid4())
        await db.execute(insert(User).values(id=uid, email=f'{uid}@x.invalid', hashed_pw='x', is_active=True, created_at=NOW))
        await db.execute(insert(Project).values(id=pid, user_id=uid, name='P', created_at=NOW, updated_at=NOW))
        await db.commit()
        user = await db.get(User, uid)
        yield {'db': db, 'user': user, 'pid': pid, 'store': store, 'solar': solar, 'vents': vents}

async def _create(server, locator=None, **body) -> ResearchRun:
    request = CreateRunRequest(project_id=server['pid'], question='q', depth='fast', dispatch=False, **body)
    out = await create_run(request, server['db'], server['user'], _NoDispatch(), locator or _Locator(server['store']))
    await server['db'].commit()
    return await server['db'].get(ResearchRun, uuid.UUID(out['run_id']))

async def test_a_selection_is_frozen_to_exact_versions_and_reaches_the_engine(server):
    row = await _create(server, corpus_selection={'doc_keys': [server['solar'].doc_key]})
    assert row.corpus_mode is True, 'a selection is corpus-only research'
    resolved = row.corpus_selection['resolved']
    assert [item['document_id'] for item in resolved] == [server['solar'].doc_id]
    config = await run_execution.run_config_for_run(server['db'], row)
    assert config.corpus_mode is True
    assert config.corpus_allowed_ids == frozenset({server['solar'].doc_id})
    port = run_execution.corpus_port_for(server['store'], config)
    assert isinstance(port, ScopedCorpus)
    assert port.allowed_ids == frozenset({server['solar'].doc_id})

async def test_the_selection_does_not_move_when_the_document_is_superseded(server):
    row = await _create(server, corpus_selection={'doc_keys': [server['solar'].doc_key]})
    newer = await server['store'].ingest('solar.txt', VENTS_TEXT.encode(), doc_key=server['solar'].doc_key)
    config = await run_execution.run_config_for_run(server['db'], row)
    assert config.corpus_allowed_ids == frozenset({server['solar'].doc_id})
    assert newer.doc_id not in config.corpus_allowed_ids

async def test_corpus_mode_without_a_selection_freezes_the_whole_corpus(server):
    row = await _create(server, corpus_mode=True)
    assert row.corpus_selection['requested']['whole_corpus'] is True
    config = await run_execution.run_config_for_run(server['db'], row)
    assert config.corpus_allowed_ids == frozenset({server['solar'].doc_id, server['vents'].doc_id})

async def test_a_web_run_carries_no_selection(server):
    row = await _create(server)
    assert row.corpus_selection is None
    config = await run_execution.run_config_for_run(server['db'], row)
    assert config.corpus_allowed_ids is None
    assert run_execution.corpus_port_for(server['store'], config) is server['store']

async def test_an_empty_selection_is_refused_before_the_run_exists(server):
    with pytest.raises(Unprocessable, match='Select at least one'):
        await _create(server, corpus_selection={'doc_keys': [], 'folder_ids': []})
    assert (await server['db'].execute(ResearchRun.__table__.select())).first() is None

async def test_a_selection_that_resolves_to_nothing_is_refused(tmp_path, server):
    empty = CorpusStore(tmp_path / 'empty.sqlite', FakeEmbeddings())
    with pytest.raises(Unprocessable, match='no documents'):
        await _create(server, locator=_Locator(empty), corpus_mode=True)
    with pytest.raises(Unprocessable, match='no documents'):
        await _create(server, locator=_Locator(None), corpus_mode=True)

async def test_an_unknown_file_or_folder_is_not_found_and_not_named(server):
    with pytest.raises(NotFound) as err:
        await _create(server, corpus_selection={'doc_keys': [server['solar'].doc_key, 'someone-elses']})
    assert 'someone-elses' not in str(err.value)
    with pytest.raises(NotFound):
        await _create(server, corpus_selection={'folder_ids': ['folder-1']})

async def test_a_selection_with_corpus_mode_false_is_refused(server):
    with pytest.raises(Unprocessable, match='corpus_mode cannot be false'):
        await _create(server, corpus_mode=False, corpus_selection={'doc_keys': [server['solar'].doc_key]})

