FROM python:3.14-slim-bookworm@sha256:516b51cb9db2ef061e465c803e50f64361b82cefd91e525b889b967b83e83527 AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangoft2-1.0-0 curl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app

FROM base AS builder
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libffi-dev \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt constraints.txt ./
RUN python -m venv /venv \
    && /venv/bin/pip install --upgrade pip \
    && /venv/bin/pip install -r requirements.txt

FROM base AS runtime
RUN useradd --create-home --uid 10001 appuser && mkdir -p /app/data/corpus && chown -R appuser:appuser /app
COPY --from=builder /venv /venv
ENV PATH="/venv/bin:$PATH"

FROM runtime AS pdf
RUN apt-get update && apt-get install -y --no-install-recommends fonts-noto-cjk && rm -rf /var/lib/apt/lists/*

FROM runtime AS source
COPY --chown=appuser:appuser app ./app
COPY --chown=appuser:appuser research_engine ./research_engine
COPY --chown=appuser:appuser alembic ./alembic
COPY --chown=appuser:appuser alembic.ini ./
COPY --chown=appuser:appuser docker ./docker

FROM pdf AS api
COPY --from=source --chown=appuser:appuser /app /app
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=10 CMD curl -fsS http://localhost:8000/health/ready || exit 1
ENTRYPOINT ["sh", "/app/docker/entrypoint-api.sh"]

FROM source AS worker
USER appuser
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=5 CMD celery -A app.workers.celery_app.celery_app inspect ping || exit 1
CMD ["celery", "-A", "app.workers.celery_app.celery_app", "worker", "--concurrency=1", "--loglevel=info"]

FROM source AS test
COPY requirements-dev.txt ./
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/* && pip install -r requirements-dev.txt
COPY --chown=appuser:appuser tests ./tests
COPY --chown=appuser:appuser evals ./evals
COPY --chown=appuser:appuser pyproject.toml ./
USER appuser

