"""`GET /models/prompt-defaults`, driven on both hosts (scope freeze §12, D-2)."""
from __future__ import annotations
import json
import httpx
import pytest
from app.schemas.auth import MAX_PROMPT_OVERRIDE_CHARS
from research_engine.prompt_composition import PROTECTED_PURPOSES, ROLES, editable_defaults
from tests.server_driver import server_driver
PATH = '/models/prompt-defaults'
FORBIDDEN_NAMES = ('citation_verify', 'contradiction_detector', 'synthesizer.repair', 'chat.project')

async def _body(host) -> dict:
    resp = await host.request('GET', PATH)
    assert resp.status_code == 200, f'{host.name}: {resp.text}'
    return resp.json()

@pytest.fixture
async def hosts(tmp_path):
    async with server_driver(tmp_path / 'server') as driver:
        yield (driver,)

async def test_repeated_requests_serve_an_identical_body(hosts):
    server, = hosts
    assert await _body(server) == await _body(server)

async def test_the_body_is_exactly_the_producer_output(hosts):
    server, = hosts
    body = await _body(server)
    assert body == {'max_chars': MAX_PROMPT_OVERRIDE_CHARS, 'roles': [{'role': d.role, 'default_prompt': d.text, 'untrusted_content_framed': d.framed} for d in editable_defaults()]}

async def test_the_body_carries_only_the_fields_the_editor_needs(hosts):
    """No hashes, no runtime state, no preference — the shape is closed."""
    for host in hosts:
        body = await _body(host)
        assert set(body) == {'max_chars', 'roles'}, host.name
        assert [r['role'] for r in body['roles']] == list(ROLES), host.name
        for entry in body['roles']:
            assert set(entry) == {'role', 'default_prompt', 'untrusted_content_framed'}, host.name

async def test_no_protected_purpose_appears_on_the_wire(hosts):
    for host in hosts:
        raw = json.dumps(await _body(host))
        for name in (*FORBIDDEN_NAMES, *PROTECTED_PURPOSES):
            assert name not in raw, f'{host.name}: {name}'

async def test_the_body_does_not_depend_on_the_callers_preferences(hosts):
    """Save an override for every role, then ask again: the defaults must not move, and the
    saved text must not appear. The editor shows a user's own text from `GET /auth/me`."""
    for host in hosts:
        before = await _body(host)
        saved = {role: f'SAVED OVERRIDE FOR {role.upper()}' for role in ROLES}
        resp = await host.request('PATCH', '/auth/me', json={'preferences': {'prompt_overrides': saved}})
        assert resp.status_code == 200, f'{host.name}: {resp.text}'
        after = await _body(host)
        assert after == before, host.name
        assert 'SAVED OVERRIDE' not in json.dumps(after), host.name

async def test_the_server_requires_authentication():
    """The drivers substitute the auth gate, so this asks the real app with no credentials."""
    from app.main import app as server_app
    transport = httpx.ASGITransport(app=server_app)
    async with httpx.AsyncClient(transport=transport, base_url='http://server.invalid') as c:
        resp = await c.get(f'/api/v1{PATH}')
    assert resp.status_code == 401
