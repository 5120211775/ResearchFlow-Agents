"""One host, made drivable in-process. **Not a test module.**"""

from __future__ import annotations
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
import httpx
from research_engine.runconfig import ROLES

TOKEN = "parity-token"
EMBEDDER_NAME = "parity-embed"
SERVER_CAPABILITIES = frozenset({"project_memory", "project_chat", "server_pdf", "rate_limits"})


@dataclass
class Driver:
    """One host, ready to be driven."""

    name: str
    client: httpx.AsyncClient
    backing: str
    capabilities: frozenset[str] = field(default_factory=frozenset)
    run_driver: bool = False

    async def request(self, method: str, path: str, **kw) -> httpx.Response:
        return await self.client.request(method, f"/api/v1{path}", **kw)


def _embedder():
    from tests.dataflow.test_corpus_store import FakeEmbeddings

    return FakeEmbeddings(EMBEDDER_NAME)


def pinned_routing() -> dict[str, str]:
    """What both hosts will resolve to, read from the same place each of them reads."""
    import os

    return {role: os.environ[f"MODEL_{role.upper()}"] for role in ROLES}


def pin_local_llm_probe():
    """Make `GET /models` see one fixed local model server. Returns the previous probe.

    `_ollama_presets_from_installed()` builds `presets["ollama"]` from whatever Ollama is
    *actually reachable at request time* — a live localhost call, and the last unpinned
    input to a recorded contract. The golden's ollama preset names `llama3.2:latest` and
    `qwen2.5-coder:14b` because the machine that recorded it had exactly those pulled;
    anywhere else — CI, or any laptop without Ollama running — the helper returns `None`,
    `presets` comes back with no `ollama` key at all (`catalog.PRESETS` is static
    anthropic + google), and the step diverges for a reason that has nothing to do with
    the code under test.

    The two models below reproduce the recorded preset exactly, and are chosen to exercise
    the selection rather than hand it one answer: `fast` must pick the smallest chat model
    and `balanced`/`best` the largest *research-ready* one, so a 3B tagged underpowered and
    a 14B that is not are the smallest case where "smallest" and "strongest" differ and the
    `likely_underpowered` filter is load-bearing.

    Pinned for **both** hosts, like `_embedder`: the desktop calls this same helper now
    (plan phase 8 — its catalog used to return the static table and could offer a model
    the machine never pulled), so leaving the probe live would leave the same
    nondeterminism on that side, latent behind an `XFAIL_DIVERGENCES` entry.
    """
    from app.services import local_llm

    installed = [
        local_llm.LocalModel(
            name="llama3.2:latest",
            route="ollama:llama3.2",
            in_catalog=True,
            likely_underpowered=True,
            params_b=3.0,
        ),
        local_llm.LocalModel(
            name="qwen2.5-coder:14b",
            route="ollama:qwen2.5-coder",
            in_catalog=True,
            likely_underpowered=False,
            params_b=14.0,
        ),
    ]

    async def _probe(base_url=None):
        return local_llm.LocalLLMStatus(
            configured_base_url="http://localhost:11434/v1",
            reachable=True,
            models=installed,
            install_state="running",
        )

    previous = local_llm.probe
    local_llm.probe = _probe
    return previous


def _agent_log_sink(session_factory):
    """The `EventSink` port, writing the durable half only.

    The server's own sink (`adapters.agent_log_sink`) writes an `agent_logs` row and then
    publishes to Redis; this host has no Redis. Only the *durable* half is product
    behaviour that survives the request — it is what SSE replay reads and what a bundle's
    `trace` is assembled from — so omitting the sink entirely is not an option: the server
    would emit no trace, the desktop would emit a full one, and the harness would report
    its own gap as a product divergence.
    """
    import asyncio
    from app.models.agent_log import AgentLog

    write_lock = asyncio.Lock()

    async def sink(session_id: str, event: dict) -> None:
        async with write_lock, session_factory() as db:
            row = AgentLog(
                session_id=uuid.UUID(session_id),
                event_type=event.get("type", "agent_log"),
                agent_name=event.get("agent"),
                payload=event,
            )
            db.add(row)
            await db.flush()
            event["id"] = row.id
            await db.commit()

    return sink


class _InProcessDispatcher:
    """`RunDispatcher` for the harness — the server's own adapter, no broker.

    Deliberately not the desktop's `_SidecarDispatcher`: reusing that would make the
    "server" host literally the desktop implementation and the comparison would prove
    nothing. This drives `run_execution` — which is what the Celery task drives — against
    a saver the test process can open.
    """

    def __init__(self, session_factory, saver) -> None:
        self._sessions = session_factory
        self._saver = saver
        self._sink = _agent_log_sink(session_factory)
        self._tasks: set = set()

    def _spawn(self, coro) -> None:
        import asyncio

        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _drive(self, run_id: str, *, resume=None, plan=None) -> None:
        from app import run_execution, run_lifecycle
        from app.models.research import ResearchRun
        from research_engine import runner
        from research_engine.prompt_composition import recording_provenance

        async with self._sessions() as db:
            run = await db.get(ResearchRun, uuid.UUID(run_id))
            if run is None or run.status == "CANCELLED":
                return
            config = await run_execution.run_config_for_run(db, run)
            question, depth = (run.question, run.depth)
            await run_lifecycle.set_status(db, run, "RUNNING")
            await db.commit()
        ports = {"run_config": config, "event_sink": self._sink}
        with recording_provenance() as composed:
            if plan is not None:
                outcome = await runner.resume(
                    checkpointer=self._saver, session_id=run_id, plan=plan, **ports
                )
            elif resume is None:
                outcome = await runner.run(
                    checkpointer=self._saver,
                    session_id=run_id,
                    user_id="parity",
                    query=question,
                    depth=depth,
                    **ports,
                )
            else:
                approved, feedback = resume
                outcome = await runner.resume(
                    checkpointer=self._saver,
                    session_id=run_id,
                    approved=approved,
                    feedback=feedback,
                    **ports,
                )
        async with self._sessions() as db:
            run = await db.get(ResearchRun, uuid.UUID(run_id))
            await run_execution.persist_outcome(
                db, run, outcome, saver=self._saver, prompt_provenance=composed
            )
            await db.commit()

    async def start(self, run_id: str, user_id: str) -> None:
        self._spawn(self._drive(run_id))

    async def resume_plan(self, run_id: str, user_id: str, plan: dict) -> None:
        self._spawn(self._drive(run_id, plan=plan))

    async def rework(self, run_id: str, user_id: str, feedback: str | None) -> None:
        self._spawn(self._drive(run_id, resume=(False, feedback)))


@asynccontextmanager
async def server_driver(data_dir: Path):
    from fastapi import Depends
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from sqlalchemy import insert
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.config import settings
    from app.db.base import get_db
    from app.dependencies import (
        enforce_chat_rate_limit,
        enforce_research_rate_limit,
        get_current_user,
    )
    from app.main import app as server_app
    from app.models.user import User
    from app.workers.dispatch import get_run_dispatcher
    from tests.sqlite_support import open_db

    data_dir.mkdir(parents=True, exist_ok=True)
    async with open_db(data_dir / "server.sqlite") as session_factory:
        user_id = uuid.uuid4()
        async with session_factory() as db:
            await db.execute(
                insert(User).values(
                    id=user_id, email="parity@server.invalid", hashed_pw="!", is_active=True
                )
            )
            await db.commit()

        async def _db():
            async with session_factory() as db:
                try:
                    yield db
                    await db.commit()
                except Exception:
                    await db.rollback()
                    raise

        async def _user(db: AsyncSession = Depends(get_db)) -> User:
            return await db.get(User, user_id)

        saver_cm = AsyncSqliteSaver.from_conn_string(str(data_dir / "checkpoints.sqlite"))
        saver = await saver_cm.__aenter__()
        await saver.setup()
        previous_llm_mode = settings.llm_mode
        settings.llm_mode = "fake"
        _PINNED_KEYS = {
            "google_api_key": "parity-google-key",
            "custom_base_url": "http://custom.invalid/v1",
            "anthropic_api_key": "",
            "openai_api_key": "",
            "openrouter_api_key": "",
        }
        previous_keys = {name: getattr(settings, name) for name in _PINNED_KEYS}
        for name, value in _PINNED_KEYS.items():
            setattr(settings, name, value)
        previous_probe = pin_local_llm_probe()
        previous_corpus_dir = settings.corpus_dir
        settings.corpus_dir = str(data_dir / "corpus")
        import app.adapters as adapters

        shared_embedder = _embedder()

        async def _embeddings_for(_keys=None):
            return shared_embedder

        previous_embeddings_for = adapters.embeddings_for
        adapters.embeddings_for = _embeddings_for
        overrides = server_app.dependency_overrides
        previous_overrides = dict(overrides)
        overrides[get_db] = _db
        overrides[get_current_user] = _user
        overrides[enforce_research_rate_limit] = lambda: None
        overrides[enforce_chat_rate_limit] = lambda: None
        dispatcher = _InProcessDispatcher(session_factory, saver)
        overrides[get_run_dispatcher] = lambda: dispatcher
        try:
            transport = httpx.ASGITransport(app=server_app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://server.invalid"
            ) as client:
                yield Driver(
                    name="server",
                    client=client,
                    backing="sqlite",
                    capabilities=SERVER_CAPABILITIES,
                    run_driver=True,
                )
        finally:
            overrides.clear()
            overrides.update(previous_overrides)
            adapters.embeddings_for = previous_embeddings_for
            settings.corpus_dir = previous_corpus_dir
            settings.llm_mode = previous_llm_mode
            for name, value in previous_keys.items():
                setattr(settings, name, value)
            from app.services import local_llm

            local_llm.probe = previous_probe
            await saver_cm.__aexit__(None, None, None)


async def _await_run_status(driver, run_id, wanted, timeout=60.0):
    import asyncio

    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        response = await driver.request("GET", f"/runs/{run_id}")
        if (
            response.status_code != 200
            or response.json()["run"]["status"] in wanted
            or asyncio.get_running_loop().time() >= deadline
        ):
            return response
        await asyncio.sleep(0.1)
