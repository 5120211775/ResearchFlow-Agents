"""The retained server advertises capabilities without hiding security controls."""
import pytest

from app.schemas.capabilities import SERVER, Capabilities


@pytest.mark.parametrize('capability', sorted(Capabilities.model_fields))
def test_every_server_capability_is_explicit(capability):
    assert capability in SERVER.model_dump()


def test_server_declares_the_supported_product_features():
    assert SERVER.accounts and SERVER.project_memory and SERVER.project_chat
    assert SERVER.server_pdf and SERVER.rate_limits
    assert not SERVER.local_llm_control and not SERVER.update_check
    assert SERVER.byok_storage == 'encrypted_column'
    assert SERVER.host == 'server'


def test_capabilities_do_not_expose_infrastructure():
    for field in Capabilities.model_fields:
        assert not any(word in field for word in ('postgres', 'sqlite', 'redis', 'celery'))
