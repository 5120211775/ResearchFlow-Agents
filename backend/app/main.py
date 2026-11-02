from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app import metrics
from app.api.v1.router import api_router
from app.config import settings
from app.db.base import engine
from app.db.redis import close_redis_pool, get_redis, init_redis_pool
from app.logconfig import configure_logging
from app.runtime import install_process_default
from app.services.error_responses import install_error_handlers
from app.services.security_headers import SecurityHeadersMiddleware
from research_engine.llm_factory import validate_pricing

# Configure structlog before the first log line: merge_contextvars is what lets a
# run's correlation_id ride along every log across the API → Celery → engine path.
configure_logging(json_output=settings.is_production)

logger = structlog.get_logger()

# Application version exposed by OpenAPI and health endpoints.
APP_VERSION = "0.1.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Validate configuration and initialize shared runtime resources.

    Alembic owns the schema. The host installs RunConfig before engine calls.
    """
    logger.info("startup", message="Starting Multi-Agent Research Assistant API")
    install_process_default()
    validate_pricing()
    await init_redis_pool()
    logger.info("startup", message="Redis initialized ✅ (run `alembic upgrade head` for schema)")
    yield
    await close_redis_pool()
    logger.info("shutdown", message="Server shutting down")


app = FastAPI(
    title="Multi-Agent Research Assistant API",
    version=APP_VERSION,
    description="Self-hostable research assistant with an auditable human-in-the-loop gate.",
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
    lifespan=lifespan,
)

app.add_middleware(SecurityHeadersMiddleware)

# Map domain errors consistently to HTTP responses.
install_error_handlers(app)

# Streamlit calls the API server-side; permit the configured development origin only.
if not settings.is_production:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.frontend_url],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

# Mount versioned API router
app.include_router(api_router, prefix="/api/v1")


@app.get("/health", tags=["Health"])
async def health_check():
    """Liveness — the process is up. Used by the container HEALTHCHECK."""
    return {"status": "ok", "version": APP_VERSION}


@app.get("/health/ready", tags=["Health"])
async def readiness_check():
    """Return 503 until PostgreSQL and Redis are reachable."""
    checks = {"database": False, "redis": False}
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["database"] = True
    except Exception as e:  # noqa: BLE001
        logger.warning("readiness_db_down", error=str(e))
    try:
        await get_redis().ping()
        checks["redis"] = True
    except Exception as e:  # noqa: BLE001
        logger.warning("readiness_redis_down", error=str(e))

    if not all(checks.values()):
        return JSONResponse(status_code=503, content={"status": "not_ready", "checks": checks})
    return {"status": "ready", "checks": checks}


@app.get("/metrics", tags=["Health"])
async def metrics_endpoint() -> Response:
    """Expose aggregate counters without user identifiers or research content."""
    payload, content_type = metrics.render()
    return Response(content=payload, media_type=content_type)
