"""Explicit product capabilities exposed by the API."""
from typing import Literal

from pydantic import BaseModel


class Capabilities(BaseModel):
    accounts: bool
    project_memory: bool
    project_chat: bool
    server_pdf: bool
    rate_limits: bool
    local_llm_control: bool
    update_check: bool
    byok_storage: Literal['encrypted_column']
    host: Literal['server']


SERVER = Capabilities(
    accounts=True,
    project_memory=True,
    project_chat=True,
    server_pdf=True,
    rate_limits=True,
    local_llm_control=False,
    update_check=False,
    byok_storage='encrypted_column',
    host='server',
)
