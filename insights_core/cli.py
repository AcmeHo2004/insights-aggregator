"""Generic CLI shared by every firm package.

A firm's `__main__.py` calls `main(firm_root, adapters)`; this provides the
`scan` and `backfill` subcommands. `firm_root` is firms/<slug>/; `adapters` is
the merged registry (core built-ins + that firm's bespoke adapters).
"""

from __future__ import annotations

import argparse
from pathlib import Path


def main(firm_root: Path, adapters: dict, argv: list[str] | None = None) -> int:
    firm_root = Path(firm_root)
    parser = argparse.ArgumentParser(prog=f"{firm_root.name}_insights", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="Run the ingestion pipeline once")
    p_scan.add_argument("--quiet", action="store_true")

    p_back = sub.add_parser("backfill", help="LLM-enrich the back-catalogue (Batches API)")
    p_back.add_argument("--estimate", action="store_true", help="Show count + cost only, no API calls")
    p_back.add_argument("--workers", type=int, default=6, help="Fallback per-item concurrency if Batches is unavailable")
    p_back.add_argument("--max-tier", type=int, default=None, help="Only enrich items with tier <= N")
    p_back.add_argument("--days", type=int, default=None, help="Only items published within N days")
    p_back.add_argument("--limit", type=int, default=None)

    args = parser.parse_args(argv)

    if args.command == "scan":
        from .pipeline import run_scan

        result = run_scan(firm_root, adapters, verbose=not args.quiet)
        if not result["llm_available"]:
            print(
                "  note: ANTHROPIC_API_KEY not set — used keyword fallback for tags.\n"
                "        Set it and re-run `scan` (or run `backfill`) to add LLM summaries."
            )
        return 0

    if args.command == "backfill":
        from .pipeline import run_backfill

        run_backfill(
            firm_root,
            workers=args.workers,
            max_tier=args.max_tier,
            days=args.days,
            limit=args.limit,
            estimate_only=args.estimate,
        )
        return 0

    return 1
