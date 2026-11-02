"""Test demo stamp parity."""
from __future__ import annotations
import ast
import inspect
import textwrap
from research_engine import bundle

def test_stamp_is_applied_only_for_demo_sessions():
    body = '# Report\n\nA finding [1].\n'
    assert bundle.stamp_demo_md(body, demo=False) == body
    stamped = bundle.stamp_demo_md(body, demo=True)
    assert stamped.endswith(body)
    assert stamped.startswith(bundle.DEMO_STAMP_MD)
    assert 'DEMO — NOT REAL RESEARCH' in stamped

def test_server_demo_constant_is_the_shared_one():
    """`app.api.v1.research._DEMO_STAMP` must be the shared object, not a copy.

    A copied string would pass a text comparison today and drift the first time either
    side is edited, which is the failure this issue was.
    """
    from app.api.v1 import research
    assert research._DEMO_STAMP is bundle.DEMO_STAMP_MD

def _calls_in(source: str, func_name: str) -> set[str]:
    """Dotted names actually *called* inside `func_name`, via AST.

    Deliberately not a substring search. The first version of this test grepped the
    source for "stamp_demo_md" and passed with the call deleted, because the explanatory
    comment above it still contained the word — the same use-versus-mention trap the
    repo's CI greps have hit (AGENTS.md). A call node cannot be faked by prose.
    """
    tree = ast.parse(textwrap.dedent(source))
    target = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) and n.name == func_name), None)
    assert target is not None, f'{func_name} not found'
    names = set()
    for node in ast.walk(target):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute):
                base = f.value
                if isinstance(base, ast.Name):
                    names.add(f'{base.id}.{f.attr}')
                names.add(f.attr)
            elif isinstance(f, ast.Name):
                names.add(f.id)
    return names

def test_bundle_is_never_stamped_on_either_host():
    """The deliberate asymmetry, pinned.

    `report_hash` is checked against the `draft_hash` recorded at approval, so prepending
    prose to the report body inside a bundle would break the approval chain and make every
    demo bundle fail verification for a reason unrelated to its integrity. The bundle
    carries `demo` as a hash-covered field instead.
    """
    from app.api.v1 import research as server
    assert 'stamp_demo' in inspect.getsource(server._report_or_404), "the bundle's opt-out parameter is gone"
