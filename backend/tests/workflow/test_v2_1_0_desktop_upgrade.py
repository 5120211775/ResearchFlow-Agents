"""Test v2 1 0 desktop upgrade."""
from __future__ import annotations
import hashlib
import json
import shutil
import sqlite3
from pathlib import Path
import pytest
from research_engine.bundle import BundleManifest
from research_engine.verify_bundle import verify, verify_file
from tests.server_driver import _await_run_status
FIXTURE = Path(__file__).resolve().parent.parent / 'fixtures' / 'upgrade' / 'v2.1.0'
MANIFEST = json.loads((FIXTURE / 'MANIFEST.json').read_text(encoding='utf-8'))
V3_COLUMNS = {'research_runs': {'effective_prompt_overrides', 'prompt_overrides_status', 'effective_prompt_provenance'}, 'sessions': {'prompt_overrides_not_applied'}}
OVERRIDE = 'Plan exactly three research tasks, each answerable from one primary source.'

def _columns(db: Path, table: str) -> set[str]:
    with sqlite3.connect(db) as conn:
        return {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}

def _row_counts(db: Path) -> dict[str, int]:
    with sqlite3.connect(db) as conn:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}

@pytest.fixture()
def install(tmp_path) -> Path:
    """A copy of the v2.1.0 data directory — the test never writes to the committed one."""
    data = tmp_path / 'research-engine'
    shutil.copytree(FIXTURE, data, ignore=shutil.ignore_patterns('MANIFEST.json', '*.bundle.json'))
    return data

def test_the_fixture_files_are_the_ones_v2_1_0_wrote():
    """A hand-edited fixture would upgrade a database v2.1.0 never produced."""
    assert MANIFEST['sidecar'] == {'version': '2.1.0', 'git_sha': '4580ac06e7e5ef214b55fcad580e4a40b3689f30'}
    for name, digest in MANIFEST['files'].items():
        assert hashlib.sha256((FIXTURE / name).read_bytes()).hexdigest() == digest, name

def test_the_fixture_predates_every_v3_column():
    """Otherwise the sync below would have nothing to do and the test would prove nothing."""
    for table, added in V3_COLUMNS.items():
        assert not added & _columns(FIXTURE / 'desktop.sqlite', table), table

@pytest.mark.parametrize('name', ['run-completed.bundle.json', 'session-completed.bundle.json'])
def test_bundles_v2_1_0_exported_still_verify_unchanged(name):
    """AC-9 against the artifacts a v2.1.0 user already holds, not ones regenerated today."""
    result = verify_file(FIXTURE / name)
    assert result.passed, [(c.name, c.detail) for c in result.checks if not c.passed]
    assert json.loads((FIXTURE / name).read_text())['bundle_version'] == 1
    assert result.prompt_provenance == []
    assert result.prompt_overrides_status is None

async def _finish(driver, run_id: str) -> dict:
    """Take a run through whichever gates it still has, and return its bundle."""
    at = await _await_run_status(driver, run_id, {'AWAITING_PLAN', 'AWAITING_REVIEW', 'FAILED'})
    if at.json()['run']['status'] == 'AWAITING_PLAN':
        resp = await driver.request('POST', f'/runs/{run_id}/plan-review', json={'decision': 'APPROVED'})
        assert resp.status_code == 201, resp.text
        at = await _await_run_status(driver, run_id, {'AWAITING_REVIEW', 'FAILED'})
    assert at.json()['run']['status'] == 'AWAITING_REVIEW', at.text
    resp = await driver.request('POST', f'/runs/{run_id}/report-review', json={'decision': 'APPROVED'})
    assert resp.status_code == 201, resp.text
    done = await _await_run_status(driver, run_id, {'COMPLETED', 'FAILED'})
    assert done.json()['run']['status'] == 'COMPLETED', done.text
    bundle = await driver.request('GET', f'/runs/{run_id}/bundle.json')
    assert bundle.status_code == 200, bundle.text
    return bundle.json()

def _verified(bundle: dict):
    result = verify(BundleManifest.model_validate(bundle))
    assert result.passed, [(c.name, c.detail) for c in result.checks if not c.passed]
    return result

async def _new_run(driver) -> str:
    resp = await driver.request('POST', '/runs', json={'project_id': MANIFEST['project_id'], 'question': 'How do retrieval-augmented agents keep long-term memory consistent?', 'depth': 'fast', 'skip_plan_gate': False})
    assert resp.status_code == 201, resp.text
    return resp.json()['run_id']
