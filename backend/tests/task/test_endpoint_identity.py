"""Model endpoints must not change identity or trigger implicit DNS probes."""
import socket

import pytest

from research_engine.llm_factory import map_local_host


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:20128/v1", "http://localhost:11434/v1",
    "http://host.docker.internal:11434/v1", "https://api.deepseek.com/v1",
])
def test_explicit_endpoint_is_preserved_without_dns(monkeypatch, url):
    def forbidden(*args, **kwargs):
        raise AssertionError("Constructing a model endpoint must not probe DNS")
    monkeypatch.setattr(socket, "gethostbyname", forbidden)
    assert map_local_host(url) == url
