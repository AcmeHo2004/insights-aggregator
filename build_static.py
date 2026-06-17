#!/usr/bin/env python3
"""Export every firm's SQLite DB into a static site/ folder (data.json + facets
+ the front-end assets) that GitHub Pages can serve with no backend.

Read/star state is per-viewer (localStorage), so the export carries content only.
"""

from __future__ import annotations

import datetime as dt
import json
import shutil
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
SITE = ROOT / "site"

# Firm brand metadata (display name + accent). Add a line when you add a firm.
FIRM_META = {
    "JPMorgan":       {"short": "JPM", "color": "#B0894F", "order": 1},
    "Goldman Sachs":  {"short": "GS",  "color": "#6E9BD8", "order": 2},
    "Morgan Stanley": {"short": "MS",  "color": "#3FB8C4", "order": 3},
    "BlackRock":      {"short": "BLK", "color": "#FF4713", "order": 4},
}
DEFAULT = {"short": "", "color": "#8A93A6", "order": 99}
TOPICS = ["macro", "rates", "equities", "fixed-income", "credit",
          "alternatives", "fx", "commodities", "multi-asset", "outlook"]


def load():
    items, firms = [], {}
    for db in sorted(ROOT.glob("firms/*/data/insights.db")):
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        fr = conn.execute("SELECT firm FROM items WHERE firm != '' LIMIT 1").fetchone()
        if not fr:
            conn.close()
            continue
        firm = fr[0]
        meta = FIRM_META.get(firm, DEFAULT)
        firms[firm] = {"firm": firm, "short": meta["short"] or firm[:3].upper(),
                       "color": meta["color"], "order": meta["order"]}
        for r in conn.execute("SELECT * FROM items"):
            d = dict(r)
            items.append({
                "id": d["id"], "firm": firm, "firm_short": firms[firm]["short"], "color": meta["color"],
                "business_unit": d["business_unit"], "source_name": d["source_name"],
                "content_type": d["content_type"], "title": d["title"],
                "url": d["url"] or "", "audio_url": d["audio_url"] or "",
                "published_at": d["published_at"], "ingested_at": d["ingested_at"],
                "summary": d["llm_summary"] or d["raw_summary"] or "",
                "is_llm": bool(d["llm_summary"]), "why_it_matters": d["why_it_matters"] or "",
                "topics": json.loads(d["topics"]) if d["topics"] else [],
                "asset_class": json.loads(d["asset_class"]) if d["asset_class"] else [],
                "tier": d["tier"],
            })
        conn.close()
    items.sort(key=lambda it: (it["published_at"] or it["ingested_at"] or ""), reverse=True)
    firms_list = sorted(firms.values(), key=lambda f: (f["order"], f["firm"]))
    return items, firms_list


def main():
    items, firms = load()
    units = sorted({it["business_unit"] for it in items if it["business_unit"]})
    types = sorted({it["content_type"] for it in items if it["content_type"]})

    if SITE.exists():
        shutil.rmtree(SITE)
    SITE.mkdir(parents=True)
    for f in WEB.iterdir():
        if f.is_file():
            shutil.copy(f, SITE / f.name)

    (SITE / "data.json").write_text(
        json.dumps(items, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (SITE / "facets.json").write_text(
        json.dumps({"firms": firms, "business_units": units, "content_types": types, "topics": TOPICS},
                   ensure_ascii=False), encoding="utf-8")
    (SITE / "meta.json").write_text(
        json.dumps({"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(), "count": len(items)}),
        encoding="utf-8")
    # Pages: don't run the content through Jekyll
    (SITE / ".nojekyll").write_text("", encoding="utf-8")
    print(f"built site/ — {len(items)} items across {len(firms)} firms")


if __name__ == "__main__":
    main()
