"""`GET /models/routing` — the verb that was missing from both hosts."""
from __future__ import annotations
import httpx
import pytest
import pytest_asyncio
TOKEN = 'test-sidecar-token'
ROUTING = {'planner': 'ollama:qwen2.5', 'executor': 'ollama:qwen2.5', 'critic': 'ollama:qwen2.5', 'synthesizer': 'ollama:qwen2.5', 'chat': 'ollama:qwen2.5'}

def _auth() -> dict:
    return {'Authorization': f'Bearer {TOKEN}'}

def test_the_server_declares_a_get_for_routing():
    """A route-table assertion rather than a request, because the server path needs a
    database this test does not have — but "the verb exists" is exactly the bug, and the
    route table is where a missing verb is visible."""
    from app.api.v1.models import router
    verbs = {method for route in router.routes if str(getattr(route, 'path', '')).endswith('/routing') for method in getattr(route, 'methods', set())}
    assert verbs, 'no /routing route found at all — did the path change?'
    assert verbs >= {'GET', 'PUT', 'DELETE'}, f'/routing is missing a verb: {sorted(verbs)}'
