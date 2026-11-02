"""Which preferences a run honours is one implementation, not four (RFC AP-2)."""
from __future__ import annotations
import ast
import inspect
from pathlib import Path
from app import run_execution
from app.services.run_config import PREFERENCE_FIELDS, preference_overrides
BACKEND = Path(__file__).resolve().parents[2]
EXPECTED_FIELDS = ('retrieval_k', 'min_sources_per_task', 'snippet_max_chars', 'tavily_api_key', 'brave_api_key')

def test_the_preference_surface_is_exactly_what_it_was():
    """AP-2 moves the contract; it must not broaden it."""
    assert PREFERENCE_FIELDS == EXPECTED_FIELDS

def test_the_shared_module_stays_free_of_infrastructure_and_the_orm():
    """It is on the desktop's request-time import path, so what it imports ships.

    `app.config` builds `Settings` from environment an installed app does not have, and
    `app.models` drags the ORM in for a rule that only reads a mapping.
    """
    source = (BACKEND / 'app' / 'services' / 'run_config.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update((a.name for a in node.names))
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    forbidden = ('app.config', 'app.db', 'app.dependencies', 'app.workers', 'app.models')
    assert not [m for m in imported if m.startswith(forbidden)], sorted(imported)
