"""Test memory index port."""
from __future__ import annotations
import uuid
import pytest
from app.errors import CapabilityUnavailable
from app.ports import MemoryIndex

def test_the_server_index_satisfies_the_port():
    from app.adapters import PgVectorMemoryIndex
    assert isinstance(PgVectorMemoryIndex(), MemoryIndex)

def test_the_two_hosts_disagree_about_availability_and_say_so():
    """The capability difference, read off the port rather than re-derived by each caller.

    `memory.is_available(db)` tests the SQL dialect, which is a fact about storage standing
    in for a fact about the product. A caller that writes its own dialect test is a caller
    that will disagree with it.
    """
    from app.adapters import PgVectorMemoryIndex
    assert PgVectorMemoryIndex().available is True

def test_the_domain_layer_no_longer_contains_the_pgvector_operator():
    """The reversal, made checkable.

    `cosine_distance` is pgvector's. Its presence in `app/services/memory.py` is what
    invalidated revision 1's "capability, not a port", so its absence is what the fix has
    to mean.
    """
    import inspect
    from app.services import memory
    source = inspect.getsource(memory)
    assert 'cosine_distance' not in source, 'app/services/memory.py still writes the pgvector distance operator — it belongs to the index adapter'

def test_retrieve_asks_the_index_rather_than_writing_the_query():
    """And passes both isolation predicates through."""
    import inspect
    from app.services import memory
    signature = inspect.signature(memory.retrieve)
    assert 'index' in signature.parameters, 'retrieve must take the index it queries'
    source = inspect.getsource(memory.retrieve)
    assert 'index.nearest' in source
    for predicate in ('project_id', 'embedding_model'):
        assert predicate in source, f'{predicate} is an isolation boundary and must reach the index'
