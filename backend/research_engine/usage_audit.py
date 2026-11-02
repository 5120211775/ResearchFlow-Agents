"""Provider-reported usage only; never log prompts, responses, keys or headers."""
from __future__ import annotations

import time

import structlog
from langchain_core.callbacks import AsyncCallbackHandler

logger = structlog.get_logger()


class UsageAudit(AsyncCallbackHandler):
    def __init__(self, role: str, route: str) -> None:
        self.role = role
        self.route = route
        self.started: dict[str, float] = {}

    async def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        self.started[str(run_id)] = time.monotonic()

    async def on_llm_end(self, response, *, run_id, **kwargs):
        message = response.generations[0][0].message if response.generations else None
        metadata = getattr(message, 'response_metadata', None) or {}
        started = self.started.pop(str(run_id), None)
        logger.info(
            'provider_usage', role=self.role, route=self.route,
            served_model=metadata.get('model_name') or metadata.get('model'),
            usage=getattr(message, 'usage_metadata', None),
            elapsed_seconds=round(time.monotonic() - started, 3) if started else None,
            request_id=str(run_id),
        )

    async def on_llm_error(self, error, *, run_id, **kwargs):
        self.started.pop(str(run_id), None)
        logger.warning('provider_request_failed', role=self.role, route=self.route,
                       error_type=type(error).__name__, request_id=str(run_id))
