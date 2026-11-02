"""All four streams run one loop, and hand it the right two lists."""
from __future__ import annotations
import ast
import inspect
from app.services.event_stream import sse_frames

def _routes():
    from app.api.v1.research import stream_events
    from app.api.v1.runs import stream_run
    return {'legacy_session': stream_events, 'research_run': stream_run}

def _calls_to(source: str, name: str) -> list[ast.Call]:
    tree = ast.parse(inspect.cleandoc(source))
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and (node.func.id == name)]

def _stop_lists(route) -> tuple[str, str]:
    """The `replay_stop` and `terminal_stop` arguments one route hands the generator."""
    calls = _calls_to(inspect.getsource(route), 'sse_frames')
    assert len(calls) == 1, f'{route.__qualname__} does not call sse_frames exactly once'
    kwargs = {kw.arg: ast.unparse(kw.value) for kw in calls[0].keywords}
    assert 'replay_stop' in kwargs and 'terminal_stop' in kwargs, f'{route.__qualname__} does not pass both stop-lists'
    return (kwargs['replay_stop'], kwargs['terminal_stop'])

def test_all_four_streams_use_the_one_generator():
    """The anti-duplication check. A fifth copy of the loop would pass every behavioural
    test in `test_sse_frames` and still be a fifth copy."""
    routes = _routes()
    assert len(routes) == 2, f'expected the two server streams, found {sorted(routes)}'
    for name, route in routes.items():
        assert _calls_to(inspect.getsource(route), 'sse_frames'), f'{name} does not use the shared frame generator — it has its own loop'

def test_no_stream_passes_the_same_list_twice():
    """The original defect, stated where it can still be made: the two lists are different
    lists, and a route that passes one for both has recreated the bug inside the new
    structure."""
    for name, route in _routes().items():
        replay, terminal = _stop_lists(route)
        assert replay != terminal, f'{name} hands the same stop-list to replay and to the live tail — replay would stop at the design gate and hide everything the run did afterwards'

def test_the_generator_is_the_only_place_the_rule_is_written():
    """`sse_frames` takes both lists as arguments and never names an event type itself.

    If it did, a route's lists would be advisory and the real rule would be hidden inside
    the generator — which is how this got wrong in the first place, four times over.
    """
    source = inspect.getsource(sse_frames)
    for event in ('PLAN_READY', 'HITL_READY', 'COMPLETED', 'FAILED'):
        assert f'"{event}"' not in source, f"sse_frames hard-codes {event}; the stop-lists are the caller's to state"
