"""Polling and configuration surfaces preserve run ownership and expose no keys."""
import uuid

from app.models.agent_log import AgentLog
from tests.task.test_runs_api import api  # noqa: F401 -- shared real SQLite API fixture


async def test_events_are_durable_cursor_based_and_not_duplicated(api):
    c = api["client"]
    result = await c.post("/api/v1/runs", json={
        "project_id": str(api["project_id"]), "question": "Polling test", "dispatch": False,
    })
    rid = result.json()["run_id"]
    for n in range(3):
        api["db"].add(AgentLog(session_id=uuid.UUID(rid), event_type="agent_log",
                               payload={"type": "agent_log", "message": f"step {n}"}))
    await api["db"].commit()
    first = (await c.get(f"/api/v1/runs/{rid}/events?limit=2")).json()
    assert len(first["events"]) == 2
    second = (await c.get(f"/api/v1/runs/{rid}/events", params={"after_id": first["next_cursor"]})).json()
    assert len(second["events"]) == 1
    assert second["events"][0]["id"] > first["next_cursor"]
    empty = (await c.get(f"/api/v1/runs/{rid}/events", params={"after_id": second["next_cursor"]})).json()
    assert empty["events"] == []
    assert empty["next_cursor"] == second["next_cursor"]


async def test_unknown_run_events_are_not_exposed(api):
    result = await api["client"].get(f"/api/v1/runs/{uuid.uuid4()}/events")
    assert result.status_code == 404


async def test_runtime_identity_has_only_an_explicit_safe_field_set(api):
    from app.api.v1.runtime import router
    api["client"]._transport.app.include_router(router, prefix="/api/v1")
    result = await api["client"].get("/api/v1/runtime")
    assert result.status_code == 200
    assert set(result.json()) == {
        "llm_mode", "effective_routing", "embeddings_provider", "embeddings_model",
        "max_input_tokens", "max_parallel_tasks", "max_critic_loops", "max_wallclock_seconds",
    }
