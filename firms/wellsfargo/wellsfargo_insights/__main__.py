"""CLI: `python -m wellsfargo_insights {scan|serve}`."""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="wellsfargo_insights", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="Run the ingestion pipeline once")
    p_scan.add_argument("--quiet", action="store_true")

    p_serve = sub.add_parser("serve", help="Run the dashboard web server")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=5000)
    p_serve.add_argument("--debug", action="store_true")

    p_back = sub.add_parser("backfill", help="LLM-enrich the back-catalogue (all unenriched items)")
    p_back.add_argument("--estimate", action="store_true", help="Show count + cost only, no API calls")
    p_back.add_argument("--workers", type=int, default=6)
    p_back.add_argument("--max-tier", type=int, default=None, help="Only enrich items with tier <= N")
    p_back.add_argument("--days", type=int, default=None, help="Only items published within N days")
    p_back.add_argument("--limit", type=int, default=None)

    args = parser.parse_args(argv)

    if args.command == "scan":
        from .pipeline import run_scan

        result = run_scan(verbose=not args.quiet)
        if not result["llm_available"]:
            print(
                "  note: ANTHROPIC_API_KEY not set — used keyword fallback for tags.\n"
                "        Set it (see .env.example) and re-run `scan` to add LLM summaries."
            )
        return 0

    if args.command == "backfill":
        from .pipeline import run_backfill

        run_backfill(
            workers=args.workers,
            max_tier=args.max_tier,
            days=args.days,
            limit=args.limit,
            estimate_only=args.estimate,
        )
        return 0

    if args.command == "serve":
        from .dashboard import app

        print(f"\n  Wells Fargo Insights dashboard → http://{args.host}:{args.port}\n")
        app.run(host=args.host, port=args.port, debug=args.debug)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
