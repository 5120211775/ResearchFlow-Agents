"""A rework re-writes the report, not the evidence, and the run page counts the latest draft."""
from __future__ import annotations
import uuid
from datetime import UTC, datetime
import pytest
from sqlalchemy import insert
from app import run_lifecycle
from app.api.v1.runs import project_run
from app.models.project import Project
from app.models.user import User
from research_engine.graph import _number_sources
from tests.server_driver import server_driver
from tests.sqlite_support import open_db
from tests.workflow.test_metrics_recording import _run_to_approval
HOSTS = {'server': server_driver}

@pytest.mark.parametrize('host', list(HOSTS), ids=list(HOSTS))
async def test_a_rework_leaves_the_evidence_exactly_as_the_first_draft_had_it(tmp_path, host):
    async with HOSTS[host](tmp_path / host) as driver:
        state = await _run_to_approval(driver)
        first = state['at_report'].json()
        reworked = state['reworked'].json()
        approved = await driver.request('POST', f'/runs/{state['run_id']}/report-review', json={'decision': 'APPROVED'})
        assert approved.status_code == 201, approved.text
        final = (await driver.request('GET', f'/runs/{state['run_id']}')).json()
    assert first['evidence'], 'the journey must reach the gate with evidence, or this proves nothing'
    ids = [e['id'] for e in first['evidence']]
    assert [e['id'] for e in reworked['evidence']] == ids, 'the rework re-synthesized over the same evidence; it must not store a second copy'
    assert [e['id'] for e in final['evidence']] == ids
    rev_1, rev_2 = reworked['revisions']
    assert rev_2['evidence_watermark'] == rev_1['evidence_watermark'], 'revision 2 saw exactly the evidence revision 1 saw'
    rev_2_claims = {c['id'] for c in reworked['claims'] if c['revision_id'] == rev_2['id']}
    linked = {link['evidence_id'] for link in reworked['claim_evidence_links'] if link['claim_id'] in rev_2_claims}
    assert linked and linked <= set(ids), "revision 2's claims link the rows revision 1 linked"
    numbered = {s['citation_index'] for s in final['sources']}
    assert all((set(r['cited_indices']) <= numbered for r in final['revisions']))
    assert final['revisions'][-1]['cited_indices'], "the fake engine's report cites its sources"

@pytest.fixture
async def db(tmp_path):
    async with open_db(tmp_path / 'cites.sqlite') as maker, maker() as session:
        yield session
EVIDENCE = [{'task_id': 1, 'source_url': f'https://example.invalid/{name}', 'source_title': name, 'snippet': f'A verbatim sentence from {name}.'} for name in ('vendor', 'linkedin', 'jobs', 'investor')]
DRAFT = '# Findings\n\nThe vendor builds an audit platform [1]. Its staff profile lists forty engineers [2].\n\n## Sources\n\n[1] https://example.invalid/vendor\n[2] https://example.invalid/linkedin\n'
REWORKED = '# Findings\n\nThe vendor builds an audit platform [1, 3]. An investor backs it [4].\n\n## Sources\n\n[1] https://example.invalid/vendor\n[2] https://example.invalid/linkedin\n[3] https://example.invalid/jobs\n[4] https://example.invalid/investor\n'

async def test_each_revision_says_which_sources_its_own_body_cites(db):
    now = datetime(2026, 10, 5, tzinfo=UTC)
    uid, pid = (uuid.uuid4(), uuid.uuid4())
    await db.execute(insert(User).values(id=uid, email=f'{uid}@x.invalid', hashed_pw='x', is_active=True, created_at=now))
    await db.execute(insert(Project).values(id=pid, user_id=uid, name='P', created_at=now, updated_at=now))
    run = await run_lifecycle.create_run(db, owner_id=uid, project_id=pid, question='q')
    numbered, _ = _number_sources(EVIDENCE)
    written = await run_lifecycle.record_evidence(db, run, evidence=EVIDENCE, numbered_sources=numbered)
    await run_lifecycle.record_revision(db, run, report_markdown=DRAFT, evidence_index=written)
    await run_lifecycle.record_revision(db, run, report_markdown=REWORKED)
    await db.commit()
    graph = await project_run(db, run)
    assert [r['cited_indices'] for r in graph['revisions']] == [[1, 2], [1, 3, 4]]
    assert sorted((s['citation_index'] for s in graph['sources'])) == [1, 2, 3, 4]
