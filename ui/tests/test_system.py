"""Real API, Celery, Redis, PostgreSQL and UI with explicitly Fake models only."""

import os
import time
import uuid

import pytest
from streamlit.testing.v1 import AppTest

from ui.api_client import APIClient, APIError


def wait_for(client, run_id, expected):
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        data = client.get(f"/runs/{run_id}")
        status = data["run"]["status"]
        if status == expected:
            return data
        assert status not in {"FAILED", "CANCELLED"}, data["run"].get("error_message")
        time.sleep(0.5)
    raise AssertionError(f"run did not reach {expected}")


def test_complete_fake_stack_and_interface():
    from pathlib import Path

    assert os.environ["API_BASE_URL"] == "http://test-api:8000"
    c = APIClient(os.environ["API_BASE_URL"])
    other = APIClient(os.environ["API_BASE_URL"])
    try:
        # Reuse isolated-test identities so repeated runs do not consume the
        # production-strength five-registrations-per-hour allowance each time.
        email = "lifecycle-owner@example.com"
        password = "Isolated-Test-Only-2026!Graph"
        try:
            user = c.login(email, password)
        except APIError as exc:
            assert exc.status == 401
            c.post("/auth/register", email=email, password=password)
            user = c.login(email, password)
        assert c.get("/runtime")["llm_mode"] == "fake", "Never run this test against paid models"
        with pytest.raises(APIError) as no_auth:
            other.get("/projects")
        assert no_auth.value.status == 401
        project = c.post(
            "/projects",
            name="Interface lifecycle " + uuid.uuid4().hex[:8],
            description="Isolated Fake verification",
        )
        pid = project["id"]
        doc = c.upload(
            pid,
            "checkpoint.txt",
            b"LangGraph checkpoints persist graph state. Human approval resumes saved work.",
        )
        assert doc["chunks"] > 0
        assert c.get(f"/projects/{pid}/corpus/status")["chunks"] > 0
        result = c.post(
            "/runs",
            project_id=pid,
            question="Compare RAG and fine tuning with evidence",
            depth="fast",
            skip_plan_gate=False,
        )
        rid = result["run_id"]
        data = wait_for(c, rid, "AWAITING_PLAN")
        tasks = data["plans"][-1]["tasks"]
        with pytest.raises(APIError) as empty_plan:
            c.post(f"/runs/{rid}/plan-review", decision="APPROVED", tasks=[])
        assert empty_plan.value.status == 422
        c.post(f"/runs/{rid}/plan-review", decision="APPROVED", tasks=tasks)
        data = wait_for(c, rid, "AWAITING_REVIEW")
        assert data["evidence"] and data["sources"]
        first_revision = data["revisions"][-1]["version"]
        first_events = c.get(f"/runs/{rid}/events")["events"]
        assert any(e["payload"]["type"] == "PLAN_READY" for e in first_events)
        c.post(
            f"/runs/{rid}/report-review",
            decision="REWORK_REQUESTED",
            feedback="Keep a short comparison and the existing evidence.",
        )
        data = wait_for(c, rid, "AWAITING_REVIEW")
        assert data["revisions"][-1]["version"] == first_revision + 1
        assert data["run"]["id"] == rid
        c.post(f"/runs/{rid}/report-review", decision="APPROVED")
        data = wait_for(c, rid, "COMPLETED")
        assert data["artifact"]
        assert c.get(f"/runs/{rid}/verification")["passed"]
        assert c.post(f"/runs/{rid}/memory-index")["skipped"]
        assert c.download(rid, "pdf").startswith(b"%PDF")
        assert len(c.download(rid, "md")) > 100
        assert c.download(rid, "json").startswith(b"{")
        memory = c.get(f"/projects/{pid}/memory/status")
        assert memory["indexed_reports"] >= 1 and memory["chunk_count"] > 0
        thread = c.post(f"/projects/{pid}/threads")
        events = list(c.chat(thread["id"], "What does the approved research say?", "report"))
        assert any(e["type"] == "done" for e in events)
        assert len(c.get(f"/threads/{thread['id']}/messages")) == 2
        # Render all four authenticated screens against the actual API.
        at = AppTest.from_file(
            str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=30
        )
        at.session_state["api"] = c
        at.session_state["user"] = user
        at.session_state["project"] = pid
        at.session_state["current_run"] = rid
        at.run()
        assert not at.exception
        for page in ["项目与知识库", "研究工作台", "项目记忆与问答"]:
            at.sidebar.radio(key="page").set_value(page).run()
            assert not at.exception
        # A second account cannot see the first owner's run or events.
        stranger = "lifecycle-stranger@example.com"
        try:
            other.login(stranger, password)
        except APIError as exc:
            assert exc.status == 401
            other.post("/auth/register", email=stranger, password=password)
            other.login(stranger, password)
        for suffix in ["", "/events"]:
            with pytest.raises(APIError) as refused:
                other.get(f"/runs/{rid}{suffix}")
            assert refused.value.status == 404
    finally:
        c.close()
        other.close()
