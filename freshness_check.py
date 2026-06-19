#!/usr/bin/env python3
"""Web-freshness watchdog (runs in GitHub Actions, daily).

The web-scraped sources can only be collected from a residential IP (the
GitHub datacenter runners are bot-gated), so they're refreshed by a local
`refresh.sh`. If that local refresh lapses (laptop off for days), the live
site silently goes stale. This watchdog restores the DBs from the data branch
and checks the most recent web (`content_type='article'`) ingest across the
whole pipeline. If nothing new has been ingested in STALE_DAYS, it exits non-zero
so the Action fails and GitHub emails you — your signal to run refresh.sh.

Tune the threshold with the STALE_DAYS env var (default 7).
"""
from __future__ import annotations

import datetime as dt
import glob
import os
import sqlite3
import sys

STALE_DAYS = int(os.environ.get("STALE_DAYS", "7"))
now = dt.datetime.now(dt.timezone.utc)


def _age_days(iso: str) -> float:
    return (now - dt.datetime.fromisoformat(iso)).total_seconds() / 86400


newest: str | None = None
per_firm: dict[str, str] = {}
for db in sorted(glob.glob("firms/*/data/insights.db")):
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        row = conn.execute(
            "SELECT firm, MAX(ingested_at) FROM items WHERE content_type='article'"
        ).fetchone()
        conn.close()
    except Exception:  # noqa: BLE001
        continue
    firm, mx = (row or (None, None))
    if firm and mx:
        per_firm[firm] = mx
        if newest is None or mx > newest:
            newest = mx

if newest is None:
    print("::error::No web (article) items found in any DB — pipeline broken.")
    sys.exit(1)

age = _age_days(newest)
print(f"Newest web ingest across pipeline: {newest}  ({age:.1f} days ago)")
print(f"Threshold: {STALE_DAYS} days\n")
print("Per-firm newest web ingest (oldest first):")
for firm, mx in sorted(per_firm.items(), key=lambda kv: kv[1]):
    a = _age_days(mx)
    flag = "   <-- STALE" if a > STALE_DAYS else ""
    print(f"  {firm:<24} {mx[:10]}  ({a:>3.0f}d){flag}")

if age > STALE_DAYS:
    print(
        f"\n::error::Web pipeline is STALE — no new web content ingested in "
        f"{age:.1f} days (> {STALE_DAYS}). Run ./refresh.sh on your Mac."
    )
    sys.exit(1)

print("\nWeb pipeline is fresh. OK.")
