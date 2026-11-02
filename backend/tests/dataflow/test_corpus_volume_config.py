"""Corpus persistence across containers."""
from __future__ import annotations
from pathlib import Path
import pytest
yaml = pytest.importorskip('yaml')
COMPOSE_PATH = Path(__file__).resolve().parents[3] / 'docker-compose.full.yml'

@pytest.fixture(scope='module')
def compose() -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding='utf-8'))

def _corpus_volume_mounts(compose: dict, service: str) -> list[str]:
    """This service's `volumes:` entries that mount into /app/data."""
    volumes = compose['services'][service].get('volumes') or []
    return [v for v in volumes if isinstance(v, str) and v.endswith(':/app/data')]

def test_api_and_worker_mount_the_same_named_corpus_volume(compose: dict):
    api_mounts = _corpus_volume_mounts(compose, 'api')
    worker_mounts = _corpus_volume_mounts(compose, 'worker')
    assert api_mounts, 'api must mount a volume at /app/data — corpus_path resolves there'
    assert worker_mounts, 'worker must mount a volume at /app/data — same reason'
    assert api_mounts == worker_mounts, 'api and worker must mount the SAME volume at /app/data, or a document uploaded through one is invisible to the other (the split-brain bug AGENTS.md records)'
    volume_name = api_mounts[0].split(':', 1)[0]
    assert volume_name in compose['volumes'], f'{volume_name!r} must be declared under the top-level `volumes:` key, or it is an anonymous volume tied to the container and not the durable store this exists to provide'

def test_corpus_volume_is_not_bind_mounted_from_a_relative_path(compose: dict):
    """A relative bind mount (`./data:/app/data`) would resolve against wherever compose
    is invoked from — the exact class of bug `corpus_path` exists to avoid one layer down
    (app/config.py). The volume must be a named Docker volume, not a host path."""
    api_mounts = _corpus_volume_mounts(compose, 'api')
    volume_name = api_mounts[0].split(':', 1)[0]
    assert not volume_name.startswith(('.', '/')), f'corpus volume source {volume_name!r} looks like a bind-mounted path, not a named volume'
