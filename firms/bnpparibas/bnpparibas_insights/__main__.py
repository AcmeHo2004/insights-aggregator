"""CLI entry — delegates to `insights_core`.

Invoked as `python -m <slug>_insights scan` (the GitHub Action does
`cd firms/<slug> && python -m <slug>_insights scan`). We bootstrap the repo root
onto sys.path so `insights_core` imports under any cwd, then hand off the firm's
root + adapter registry (core built-ins + this firm's bespoke adapters, if any).
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
_REPO_ROOT = _HERE.parents[3]            # firms/<slug>/<slug>_insights/__main__.py -> repo root
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from insights_core.cli import main                          # noqa: E402
from insights_core.adapters_sitemap import BUILTIN_ADAPTERS  # noqa: E402

_ADAPTERS = dict(BUILTIN_ADAPTERS)
try:
    from . import adapters as _firm_adapters                # firm-specific, optional
    _ADAPTERS.update(getattr(_firm_adapters, "ADAPTERS", {}))
except ImportError:
    pass

if __name__ == "__main__":
    sys.exit(main(firm_root=_HERE.parents[1], adapters=_ADAPTERS))
