"""Server readiness (`GET /models/readiness`, docs/12 M8)."""

from __future__ import annotations

import httpx
import pytest

from app.api.v1.models import get_readiness
from app.config import settings
from app.models.user import User

#: Every deployment-wide key `get_readiness` consults. Listed once here so a sixth
#: provider added to `Settings` fails this file loudly rather than leaking ambience back
#: into the assertions.
_DEPLOYMENT_KEY_SETTINGS = (
    "google_api_key",
    "anthropic_api_key",
    "openai_api_key",
    "openrouter_api_key",
    "custom_api_key",
)

READINESS_KEYS = {"ready", "has_cloud_key", "local_reachable", "local_chat_models"}


def _mock_probe_transport(handler):
    """An `httpx.AsyncClient` that answers from `handler` instead of the network.

    Built from the real class at call time, so it must be constructed *before* the
    `monkeypatch.setattr` that installs it.
    """

    class _Client(httpx.AsyncClient):
        def __init__(self, *a, **kw):
            kw["transport"] = httpx.MockTransport(handler)
            super().__init__(*a, **kw)

    return _Client


def _ollama_serving(*tags: str):
    """A handler answering Ollama's `/api/tags` with exactly these model tags."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": [{"name": t, "size": 1} for t in tags]})

    return handler


def _ollama_down(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


@pytest.fixture
def keyless_deployment(monkeypatch):
    """A deployment that supplies no provider key of its own."""
    for name in _DEPLOYMENT_KEY_SETTINGS:
        monkeypatch.setattr(settings, name, "")


def _probe(monkeypatch, handler) -> None:
    monkeypatch.setattr(httpx, "AsyncClient", _mock_probe_transport(handler))


async def test_no_key_and_no_local_server_is_not_ready(keyless_deployment, monkeypatch):
    """The honest answer for a fresh deployment: nothing can serve an agent role, so the
    first-run notice must show. `FirstRunNotice` renders on exactly this verdict.
    """
    _probe(monkeypatch, _ollama_down)

    result = await get_readiness(current_user=User())

    assert result.ready is False
    assert result.has_cloud_key is False
    assert result.local_reachable is False
    assert result.local_chat_models == 0


async def test_a_reachable_chat_model_is_ready_without_any_key(keyless_deployment, monkeypatch):
    """Local inference needs no key, so a pulled chat model is sufficient on its own."""
    _probe(monkeypatch, _ollama_serving("qwen2.5:14b"))

    result = await get_readiness(current_user=User())

    assert result.has_cloud_key is False
    assert result.local_reachable is True
    assert result.local_chat_models == 1
    assert result.ready is True


async def test_an_embedding_only_server_is_reachable_but_not_ready(keyless_deployment, monkeypatch):
    """`available_providers()` lists ollama unconditionally and `reachable` only says a
    server answered; neither can tell whether anything there could fill an agent role. An
    embedding model cannot, which is why the route counts chat models — pinned here so a
    future collapse to `ready=local_reachable` fails.
    """
    _probe(monkeypatch, _ollama_serving("nomic-embed-text:latest"))

    result = await get_readiness(current_user=User())

    assert result.local_reachable is True
    assert result.local_chat_models == 0
    assert result.ready is False


async def test_a_users_own_key_is_ready_even_with_the_probe_dead(keyless_deployment, monkeypatch):
    """`ready` is an `or`, and this is the half that must not depend on the probe at all.
    A BYOK user on a deployment with no keys of its own and no local server is ready.
    """
    _probe(monkeypatch, _ollama_down)

    result = await get_readiness(current_user=User(api_key_provider="google"))

    assert result.has_cloud_key is True
    assert result.local_reachable is False
    assert result.ready is True


async def test_a_deployment_key_alone_is_ready(monkeypatch):
    """The other source of `has_cloud_key`: a deployment that supplies the key for every
    user. Deliberately not stacked on `keyless_deployment` — this is the case that fixture
    exists to suppress everywhere else.
    """
    for name in _DEPLOYMENT_KEY_SETTINGS:
        monkeypatch.setattr(settings, name, "")
    monkeypatch.setattr(settings, "google_api_key", "a-real-looking-key")
    _probe(monkeypatch, _ollama_down)

    result = await get_readiness(current_user=User())

    assert result.has_cloud_key is True
    assert result.ready is True


async def test_the_response_carries_exactly_the_four_documented_fields(
    keyless_deployment, monkeypatch
):
    """`docs/34` and `frontend/lib/types.ts` both name these four. A fifth field would
    reach the settings page as dead weight; a missing one is a render crash.
    """
    _probe(monkeypatch, _ollama_down)

    result = await get_readiness(current_user=User())

    assert set(result.model_dump()) == READINESS_KEYS
