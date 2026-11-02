"""Regression: SSRF guard blocks internal/metadata targets."""

import socket

import pytest

from research_engine.net_guard import SSRFBlocked, validate_url


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",  # AWS IMDS
        "http://metadata.google.internal/",  # GCP metadata (resolves internal)
        "http://198.18.2.125/",  # Reserved fake-IP DNS range must remain blocked
        "http://127.0.0.1:6379/",  # local Redis
        "http://localhost:8000/",  # local API
        "http://10.0.0.5/",  # RFC1918
        "http://192.168.1.1/",  # RFC1918
        "http://[::1]/",  # IPv6 loopback
        "http://100.64.0.1/",  # CGNAT
        "ftp://example.com/",  # bad scheme
        "http://user:pass@example.com/",  # userinfo
        "http://example.com:22/",  # bad port
    ],
)
def test_forbidden_urls_are_blocked(url):
    with pytest.raises(SSRFBlocked):
        validate_url(url)


def test_public_https_is_allowed(monkeypatch):
    # Deterministic public DNS fixture; this is a policy unit test, not a network probe.
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 443))])
    ips = validate_url("https://example.com/")
    assert ips  # resolved to at least one public IP
