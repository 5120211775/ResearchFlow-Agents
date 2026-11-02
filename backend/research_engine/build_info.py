"""What this build is, and which commit it came from."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from functools import cache
from pathlib import Path

import structlog

logger = structlog.get_logger()

UNKNOWN = "unknown"
#: What a stamp claims about the checkout that produced it, and so all a checkout can refute.
IDENTITY_FIELDS = ("VERSION", "GIT_SHA", "DIRTY")
#: The repository root, when this file sits where a source checkout puts it.
_CHECKOUT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class BuildInfo:
    """The five facts a build can state about itself."""

    version: str
    git_sha: str
    #: True when the working tree had uncommitted changes at build time. The one honest
    #: answer to "which commit is this?" for a local build — a SHA alone would claim the
    #: artifact matches that commit when it does not.
    dirty: bool
    #: A hash of the canonical API surface. Two builds with the same value serve the same
    #: contract, whatever their versions say.
    contract_version: str
    built_at: str

    def as_dict(self) -> dict:
        return asdict(self)


_UNSTAMPED = BuildInfo(
    version=UNKNOWN,
    git_sha=UNKNOWN,
    # Not `False`: "we did not look" is not "the tree was clean". A build with no
    # stamp cannot claim either.
    dirty=True,
    contract_version=UNKNOWN,
    built_at=UNKNOWN,
)


def build_info() -> BuildInfo:
    """This build's identity, or an honest `unknown` when it was never stamped."""
    try:
        from research_engine import _build  # type: ignore[attr-defined]
    except ImportError:
        return _UNSTAMPED
    claimed = tuple((key, getattr(_build, key, None)) for key in IDENTITY_FIELDS)
    if not getattr(sys, "frozen", False) and _refuted_by_checkout(claimed):
        return _UNSTAMPED
    return BuildInfo(
        version=getattr(_build, "VERSION", UNKNOWN),
        git_sha=getattr(_build, "GIT_SHA", UNKNOWN),
        dirty=bool(getattr(_build, "DIRTY", True)),
        contract_version=getattr(_build, "CONTRACT_VERSION", UNKNOWN),
        built_at=getattr(_build, "BUILT_AT", UNKNOWN),
    )


def checkout_identity(root: Path) -> dict | None:
    """`root`'s VERSION, HEAD and whether its tree is dirty — or None when `root` is not the
    top of a checkout git can read: no git, no repository, or `root` sits inside a larger one.

    `DIRTY` counts untracked files, as `git status` does: a stamp is a claim about the
    tree, and an added file is part of what would be built.
    """

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=root, capture_output=True, text=True, check=True
        ).stdout.strip()

    try:
        if Path(git("rev-parse", "--show-toplevel")).resolve() != root.resolve():
            return None
        return {
            "VERSION": (root / "VERSION").read_text(encoding="utf-8").strip(),
            "GIT_SHA": git("rev-parse", "HEAD"),
            "DIRTY": bool(git("status", "--porcelain")),
        }
    except OSError, subprocess.CalledProcessError:
        return None


def stamp_contradiction(
    claimed: Mapping[str, object], checkout: Mapping[str, object]
) -> str | None:
    """Why this stamp does not describe this checkout, or None when it does."""
    for key in IDENTITY_FIELDS:
        if claimed.get(key) != checkout[key]:
            return f"stamp says {key}={claimed.get(key)!r}, checkout says {checkout[key]!r}"
    return None


@cache
def _refuted_by_checkout(claimed: tuple[tuple[str, object], ...]) -> bool:
    """Once per process and stamp: git is a subprocess, and `/version` is served per request."""
    checkout = checkout_identity(_CHECKOUT)
    if checkout is None:
        return False
    reason = stamp_contradiction(dict(claimed), checkout)
    if reason:
        logger.warning("build_stamp_refuted_by_checkout", reason=reason, reporting=UNKNOWN)
    return reason is not None
