"""Saving a replacement prompt over the real routes, on both hosts."""
from __future__ import annotations
import httpx
import pytest
from app.schemas.auth import MAX_PROMPT_OVERRIDE_CHARS
TOKEN = 'test-prompt-override-token'
VALID = 'You are a meticulous planner. Prefer primary sources over summaries.'
AUTH = {'Authorization': f'Bearer {TOKEN}'}

async def _patch(client, overrides):
    return await client.patch('/api/v1/auth/me', headers=AUTH, json={'preferences': {'prompt_overrides': overrides}})

async def _stored(client):
    resp = await client.get('/api/v1/auth/me', headers=AUTH)
    assert resp.status_code == 200, resp.text
    return (resp.json().get('preferences') or {}).get('prompt_overrides')

async def test_an_override_round_trips(desktop):
    assert (await _patch(desktop, {'planner': VALID})).status_code == 200
    assert await _stored(desktop) == {'planner': VALID}

async def test_setting_a_second_role_keeps_the_first(desktop):
    """The merge, over the real route: preferences arrive a section at a time."""
    await _patch(desktop, {'planner': VALID})
    await _patch(desktop, {'critic': 'Grade strictly.'})
    assert await _stored(desktop) == {'planner': VALID, 'critic': 'Grade strictly.'}

async def test_null_resets_one_role_and_leaves_the_rest(desktop):
    await _patch(desktop, {'planner': VALID, 'critic': 'Grade strictly.'})
    assert (await _patch(desktop, {'critic': None})).status_code == 200
    assert await _stored(desktop) == {'planner': VALID}

async def test_resetting_the_last_override_leaves_nothing_behind(desktop):
    await _patch(desktop, {'planner': VALID})
    await _patch(desktop, {'planner': None})
    assert await _stored(desktop) in (None, {})

async def test_an_unrelated_preference_does_not_disturb_the_overrides(desktop):
    await _patch(desktop, {'planner': VALID})
    resp = await desktop.patch('/api/v1/auth/me', headers=AUTH, json={'preferences': {'retrieval_k': 9}})
    assert resp.status_code == 200, resp.text
    assert await _stored(desktop) == {'planner': VALID}

async def test_a_refusal_leaves_an_existing_override_untouched(desktop):
    """A rejected request must not be a partial write."""
    await _patch(desktop, {'planner': VALID})
    assert (await _patch(desktop, {'planner': ''})).status_code == 422
    assert await _stored(desktop) == {'planner': VALID}

async def test_exactly_the_maximum_is_accepted_over_the_route(desktop):
    text = 'x' * MAX_PROMPT_OVERRIDE_CHARS
    assert (await _patch(desktop, {'synthesizer': text})).status_code == 200
    assert (await _stored(desktop))['synthesizer'] == text

async def test_an_absent_field_changes_nothing(desktop):
    """Requirement 18: an account that never opens the editor is unaffected."""
    resp = await desktop.patch('/api/v1/auth/me', headers=AUTH, json={'preferences': {'density': 'compact'}})
    assert resp.status_code == 200, resp.text
    assert await _stored(desktop) is None
PREEXISTING_VALID = [('retrieval_k', 1), ('retrieval_k', 20), ('min_sources_per_task', 0), ('min_sources_per_task', 20), ('snippet_max_chars', 100), ('snippet_max_chars', 500), ('density', 'comfortable'), ('density', 'compact'), ('tavily_api_key', 'tvly-' + 'x' * 190), ('brave_api_key', 'brave-key')]
PREEXISTING_NOW_REFUSED = [('retrieval_k', 0, 'below ge=1'), ('retrieval_k', 21, 'above le=20'), ('retrieval_k', 'nine', 'not an int'), ('min_sources_per_task', -1, 'below ge=0'), ('min_sources_per_task', 21, 'above le=20'), ('snippet_max_chars', 99, 'below ge=100'), ('snippet_max_chars', 501, 'above le=500'), ('density', 'roomy', 'not in the Literal'), ('tavily_api_key', 'x' * 201, 'over max_length=200'), ('brave_api_key', 'x' * 201, 'over max_length=200')]

@pytest.mark.parametrize(('field', 'value'), PREEXISTING_VALID)
async def test_a_previously_valid_preference_is_still_accepted(desktop, field, value):
    """The compatibility half: nothing a working client sent before is refused now."""
    resp = await desktop.patch('/api/v1/auth/me', headers=AUTH, json={'preferences': {field: value}})
    assert resp.status_code == 200, resp.text
    stored = (resp.json().get('preferences') or {}).get(field)
    assert stored == value

@pytest.mark.parametrize(('field', 'value', 'because'), PREEXISTING_NOW_REFUSED)
async def test_a_previously_unvalidated_preference_is_now_refused(desktop, field, value, because):
    """**Intentional behaviour change.** Each of these was stored by this host before PR-5
    and refused by the server. The desktop now answers the same way."""
    resp = await desktop.patch('/api/v1/auth/me', headers=AUTH, json={'preferences': {field: value}})
    assert resp.status_code == 422, f'{field}={value!r} ({because}): got {resp.status_code}'
    me = await desktop.get('/api/v1/auth/me', headers=AUTH)
    assert (me.json().get('preferences') or {}).get(field) is None, f'{field} was stored despite the refusal'

async def test_a_refused_body_does_not_disturb_preferences_already_stored(desktop):
    """A rejected request is not a partial write, for the pre-existing fields either."""
    ok = await desktop.patch('/api/v1/auth/me', headers=AUTH, json={'preferences': {'retrieval_k': 9}})
    assert ok.status_code == 200
    bad = await desktop.patch('/api/v1/auth/me', headers=AUTH, json={'preferences': {'retrieval_k': 999}})
    assert bad.status_code == 422
    me = await desktop.get('/api/v1/auth/me', headers=AUTH)
    assert me.json()['preferences']['retrieval_k'] == 9

async def test_profile_fields_still_update_alongside_preferences(desktop):
    """The route does more than preferences; validating the body must not break the rest."""
    resp = await desktop.patch('/api/v1/auth/me', headers=AUTH, json={'display_name': 'Local Researcher', 'preferences': {'retrieval_k': 7}})
    assert resp.status_code == 200, resp.text
    assert resp.json()['display_name'] == 'Local Researcher'
    assert resp.json()['preferences']['retrieval_k'] == 7

async def test_a_request_with_no_preferences_key_is_unaffected(desktop):
    """Requirement 18: the path that never mentions preferences behaves exactly as before."""
    resp = await desktop.patch('/api/v1/auth/me', headers=AUTH, json={'display_name': 'Solo'})
    assert resp.status_code == 200, resp.text
    assert resp.json()['display_name'] == 'Solo'
