"""Marking a route as a thin adapter over a named owner."""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import TypeVar

F = TypeVar("F", bound=Callable)


def delegates_to(owner: str) -> Callable[[F], F]:
    """Record that this route is an adapter over `owner`, and nothing more.

    `owner` is a `"module:function"` string, not the function itself, and that is
    load-bearing rather than stylistic. The desktop's run routes import their handlers
    *inside* the request, because importing `app.api.v1.runs` at module scope reaches
    `app.config` — which requires `database_url` and `jwt_secret_key`, neither of which an
    installed app has, and which killed the packaged sidecar at launch (#50).

    A decorator taking the function object would have to resolve it at import time and
    would reintroduce exactly that. A string costs nothing until something asks.
    """

    def mark(route: F) -> F:
        route.__canonical__ = owner
        return route

    return mark


def canonical_owner(route: Callable) -> Callable:
    """The function this route delegates to, or the route itself when it owns the operation.

    Resolves the reference here rather than at decoration time — callers are tests and
    tooling, where importing the owning module is free.
    """
    reference = getattr(route, "__canonical__", None)
    if reference is None:
        return route
    module_path, _, name = reference.partition(":")
    return getattr(importlib.import_module(module_path), name)
