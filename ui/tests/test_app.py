"""UI rerun regression using a controlled API transport, no external models."""

import json
from pathlib import Path

import httpx
from streamlit.testing.v1 import AppTest

from ui.api_client import APIClient


def test_research_submit_runs_once_and_refresh_does_not_resubmit():
    writes = []

    def handler(request):
        path = request.url.path
        if request.method == "POST":
            writes.append((path, json.loads(request.content)))
            return httpx.Response(201, json={"run_id": "r1", "status": "PENDING"})
        payload = {
            "/api/v1/projects": {"projects": [{"id": "p1", "name": "Learning"}]},
            "/api/v1/runtime": {"llm_mode": "fake"},
            "/api/v1/projects/p1/corpus/documents": [],
        }[path]
        return httpx.Response(200, json=payload)

    c = APIClient("http://api", httpx.MockTransport(handler))
    at = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=15)
    at.session_state["api"] = c
    at.session_state["user"] = {"email": "demo@example.com"}
    at.run()
    assert not at.exception
    at.text_area[0].input("Research durable agent checkpoints")
    next(b for b in at.button if b.label == "生成研究计划").click().run()
    assert not at.exception
    assert len(writes) == 1
    assert writes[0][1]["skip_plan_gate"] is False
    at.run()
    assert len(writes) == 1
    next(b for b in at.button if b.label == "生成研究计划").click().run()
    assert len(writes) == 1
    c.close()


def test_logged_out_page_has_login_form():
    at = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run()
    assert not at.exception
    assert {b.label for b in at.button} == {"登录", "注册账号"}
