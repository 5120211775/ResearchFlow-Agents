"""research_engine — the research pipeline, independent of any host."""

import sys

# CI runs this package only on the image's Python (AGENTS.md). Run from a checkout — which is
# how a third party runs `python -m research_engine.verify_bundle` — pip's requires-python
# never applies, and an older interpreter would stop at a SyntaxError in 3.14-only syntax
# instead of saying which Python it needs. A RuntimeError, so no caller reads it as optional.
if sys.version_info < (3, 14):  # noqa: UP036 — the floor is enforced here, not assumed
    raise RuntimeError(f"research_engine needs Python 3.14+, this is {sys.version.split()[0]}")
