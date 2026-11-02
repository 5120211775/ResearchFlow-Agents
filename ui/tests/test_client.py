import json

import httpx
import pytest

from ui.api_client import APIClient, APIError
from ui.state import merge_events, submission_key


def test_sessions_do_not_share_cookies():
    def handler(request):
        if request.url.path.endswith("login"):
            email = json.loads(request.content)["email"]
            return httpx.Response(
                200,
                json={"message": "Logged in."},
                headers={"set-cookie": f"access_token={email}; Path=/"},
            )
        return httpx.Response(200, json={"cookie": request.headers.get("cookie", "")})

    a = APIClient("http://api", httpx.MockTransport(handler))
    b = APIClient("http://api", httpx.MockTransport(handler))
    a.post("/auth/login", email="alice")
    assert "alice" in a.get("/auth/me")["cookie"]
    assert not b.get("/auth/me")["cookie"]
    a.close()
    b.close()


def test_expired_session_refreshes_once_before_retry():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("refresh"):
            return httpx.Response(200)
        return httpx.Response(401 if len(calls) == 1 else 200, json={"ok": True})

    c = APIClient("http://api", httpx.MockTransport(handler))
    assert c.get("/auth/me")["ok"]
    assert calls == ["/api/v1/auth/me", "/api/v1/auth/refresh", "/api/v1/auth/me"]
    c.close()


def test_ambiguous_write_is_never_retried():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("timeout")

    c = APIClient("http://api", httpx.MockTransport(handler))
    with pytest.raises(APIError) as error:
        c.post("/runs", question="research")
    assert error.value.uncertain
    assert len(calls) == 1
    c.close()


def test_validation_error_is_readable_without_headers():
    c = APIClient(
        "http://api",
        httpx.MockTransport(
            lambda request: httpx.Response(422, json={"detail": [{"msg": "question too long"}]})
        ),
    )
    with pytest.raises(APIError, match="question too long") as error:
        c.post("/runs")
    assert not error.value.uncertain
    c.close()


def test_error_stream_is_not_reported_as_success():
    def handler(request):
        return (
            httpx.Response(200, json={})
            if request.method == "GET"
            else httpx.Response(
                200,
                text='data: {"type":"chunk","text":"partial"}\n\ndata: {"type":"error","detail":"failed"}\n\n',
            )
        )

    c = APIClient("http://api", httpx.MockTransport(handler))
    with pytest.raises(APIError, match="failed"):
        list(c.chat("thread", "question", "report"))
    c.close()


def test_event_replay_deduplicates_and_bounds_history():
    result = merge_events([{"id": i} for i in range(600)], [{"id": 599}, {"id": 600}])
    assert len(result) == 500
    assert result[-1]["id"] == 600
    assert len({r["id"] for r in result}) == 500


def test_submission_fingerprint_distinguishes_projects_and_selections():
    base = {"question": "same", "project_id": "a"}
    assert submission_key(base) == submission_key(dict(reversed(list(base.items()))))
    assert submission_key(base) != submission_key({**base, "project_id": "b"})
    assert submission_key(base) != submission_key({**base, "corpus_selection": {"doc_keys": ["x"]}})
