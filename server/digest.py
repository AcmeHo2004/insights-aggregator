#!/usr/bin/env python3
"""Build (and optionally email) a digest of the latest insights.

Reads the published site/data.json (the recent shard). Prints Markdown by default;
emails it if --email is passed and SMTP_* env vars are set. Schedule it however you
self-host (cron, launchd, a CI cron) — this is the "real" digest the static RSS feed
stands in for when you don't run a backend.

  python -m server.digest                 # print top items as Markdown
  python -m server.digest --tier 1 --n 25 # only tier-1, 25 items
  SMTP_HOST=… SMTP_TO=… python -m server.digest --email
"""

from __future__ import annotations

import argparse
import json
import os
import smtplib
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "site" / "data.json"


def build(items: list[dict], n: int, max_tier: int | None) -> str:
    if max_tier is not None:
        items = [it for it in items if (it.get("tier") or 9) <= max_tier]
    items = sorted(items, key=lambda it: (it.get("tier", 9), it.get("published_at") or it.get("ingested_at") or ""),
                   reverse=False)
    items = sorted(items, key=lambda it: it.get("published_at") or it.get("ingested_at") or "", reverse=True)[:n]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = [f"# Insights digest — {today}", ""]
    for it in items:
        link = it.get("url") or it.get("audio_url") or ""
        lines.append(f"- **[{it['title']}]({link})** — {it['firm']} · {it.get('source_name','')}")
        if it.get("summary"):
            lines.append(f"  {it['summary']}")
    return "\n".join(lines)


def email(md: str) -> None:
    host, to = os.environ.get("SMTP_HOST"), os.environ.get("SMTP_TO")
    if not (host and to):
        raise SystemExit("--email needs SMTP_HOST and SMTP_TO (and optionally SMTP_PORT/USER/PASS/FROM).")
    msg = EmailMessage()
    msg["Subject"] = "Insights digest — " + datetime.now(timezone.utc).strftime("%Y-%m-%d")
    msg["From"] = os.environ.get("SMTP_FROM", os.environ.get("SMTP_USER", "insights@localhost"))
    msg["To"] = to
    msg.set_content(md)
    with smtplib.SMTP(host, int(os.environ.get("SMTP_PORT", "587"))) as s:
        s.starttls()
        if os.environ.get("SMTP_USER"):
            s.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASS", ""))
        s.send_message(msg)
    print(f"emailed digest to {to}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--tier", type=int, default=None)
    ap.add_argument("--email", action="store_true")
    args = ap.parse_args()
    if not DATA.exists():
        raise SystemExit(f"{DATA} not found — run build_static.py first.")
    items = json.loads(DATA.read_text(encoding="utf-8"))
    md = build(items, args.n, args.tier)
    if args.email:
        email(md)
    else:
        print(md)


if __name__ == "__main__":
    main()
