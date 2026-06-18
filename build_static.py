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
    "PIMCO":           {"short": "PIM", "color": "#273E6B", "order": 5},
    "Apollo":          {"short": "APO", "color": "#007D55", "order": 6},
    "Bank of America": {"short": "BofA", "color": "#E31837", "order": 8},
    "Oaktree":         {"short": "OAK", "color": "#1A6B54", "order": 9},
    "KKR":             {"short": "KKR", "color": "#48104A", "order": 10},
    "Blackstone":      {"short": "BX", "color": "#B86533", "order": 12},
    "Franklin Templeton": {"short": "FT", "color": "#001E62", "order": 13},
    "RBC Capital Markets": {"short": "RBC", "color": "#005DAA", "order": 14},
    "Citi":            {"short": "Citi", "color": "#003A72", "order": 15},
    "AQR":             {"short": "AQR", "color": "#1E2A52", "order": 16},
    "Nomura":          {"short": "NOM", "color": "#CA142A", "order": 17},
    "Jefferies":       {"short": "JEF", "color": "#0067C6", "order": 18},
    "Schroders":       {"short": "SCHR", "color": "#002A5E", "order": 20},
    "abrdn":           {"short": "abrdn", "color": "#FDDA24", "order": 21},
    "Janus Henderson": {"short": "JHG", "color": "#F06C00", "order": 22},
    "Neuberger Berman": {"short": "NB", "color": "#6C6F70", "order": 24},
    "Wellington":      {"short": "WELL", "color": "#0C2340", "order": 25},
    "Brookfield":      {"short": "BN", "color": "#002E5F", "order": 26},
    "GMO":             {"short": "GMO", "color": "#1F3A5F", "order": 29},
    "BNP Paribas":     {"short": "BNP", "color": "#00965E", "order": 30},
    "Société Générale": {"short": "SG", "color": "#E9041E", "order": 31},
    "Capital Group":   {"short": "CapG", "color": "#023775", "order": 33},
    "State Street":    {"short": "SSGA", "color": "#0067B1", "order": 35},
    "DoubleLine":      {"short": "DL", "color": "#1F4E96", "order": 37},
    "Barclays":        {"short": "BARC", "color": "#00AEEF", "order": 38},
    "HSBC":            {"short": "HSBC", "color": "#DB0011", "order": 39},
    "Deutsche Bank":   {"short": "DB", "color": "#0018A8", "order": 40},
}
DEFAULT = {"short": "", "color": "#8A93A6", "order": 99}
TOPICS = ["macro", "rates", "equities", "fixed-income", "credit",
          "alternatives", "fx", "commodities", "multi-asset", "outlook"]

# Firm category (the top-level tabs). key -> display label, in display order.
CATEGORIES = [("bank", "Banks"), ("am", "Asset Managers"),
              ("pe", "Private Equity"), ("hf", "Hedge Funds")]
FIRM_CATEGORY = {
    # Banks (bulge-bracket / sell-side; some also have AM/Wealth divisions)
    "JPMorgan": "bank", "Goldman Sachs": "bank", "Morgan Stanley": "bank",
    "Bank of America": "bank", "Barclays": "bank", "HSBC": "bank",
    "Deutsche Bank": "bank", "RBC Capital Markets": "bank", "Citi": "bank",
    "Nomura": "bank", "Jefferies": "bank", "Société Générale": "bank",
    "BNP Paribas": "bank",
    # Asset managers
    "BlackRock": "am", "PIMCO": "am", "Capital Group": "am",
    "Franklin Templeton": "am", "State Street": "am", "DoubleLine": "am",
    "GMO": "am", "Schroders": "am", "abrdn": "am", "Janus Henderson": "am",
    "Neuberger Berman": "am", "Wellington": "am",
    # Private equity / alternatives
    "Apollo": "pe", "KKR": "pe", "Blackstone": "pe", "Oaktree": "pe",
    "Brookfield": "pe",
    # Hedge funds (more to come)
    "AQR": "hf",
}

# Clean the messy per-source business_unit into 5 standard "business lines".
_BU_WEALTH = {"Private Bank", "Citi Wealth", "Wealth CIO"}
_BU_IB = {"CIB", "Wholesale Banking"}
_BU_INSTITUTE = {"Investment Institute", "FT Institute"}
_BU_AM = {"Asset Management", "Global Advisors"}
_BU_RESEARCH = {"Global Research", "Citi Research", "Cross Asset Research",
                "Investment Bank Research", "Markets & Research", "Research",
                "Markets & Economy"}
BUSINESS_LINES = ["Research", "Asset Management", "Wealth Management",
                  "Investment Bank", "Investment Institute"]


def clean_business_unit(category: str, raw: str) -> str:
    if raw in _BU_INSTITUTE:
        return "Investment Institute"
    if raw in _BU_WEALTH:
        return "Wealth Management"
    if raw in _BU_IB:
        return "Investment Bank"
    if raw in _BU_AM:
        return "Asset Management"
    if raw in _BU_RESEARCH:
        return "Research"
    # generic "Insights" / "News & Insights" / "Markets & Insights": split by firm type
    return "Research" if category == "bank" else "Asset Management"


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
        category = FIRM_CATEGORY.get(firm, "am")
        firms[firm] = {"firm": firm, "short": meta["short"] or firm[:3].upper(),
                       "color": meta["color"], "order": meta["order"], "category": category}
        for r in conn.execute("SELECT * FROM items"):
            d = dict(r)
            items.append({
                "id": d["id"], "firm": firm, "firm_short": firms[firm]["short"], "color": meta["color"],
                "category": category,
                "business_unit": clean_business_unit(category, d["business_unit"] or ""),
                "source_name": d["source_name"],
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
    present_units = {it["business_unit"] for it in items if it["business_unit"]}
    units = [b for b in BUSINESS_LINES if b in present_units]
    types = sorted({it["content_type"] for it in items if it["content_type"]})
    present_cats = {it["category"] for it in items}
    categories = [{"key": k, "label": lbl} for k, lbl in CATEGORIES if k in present_cats]

    if SITE.exists():
        shutil.rmtree(SITE)
    SITE.mkdir(parents=True)
    for f in WEB.iterdir():
        if f.is_file():
            shutil.copy(f, SITE / f.name)

    (SITE / "data.json").write_text(
        json.dumps(items, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (SITE / "facets.json").write_text(
        json.dumps({"firms": firms, "categories": categories, "business_units": units,
                    "content_types": types, "topics": TOPICS},
                   ensure_ascii=False), encoding="utf-8")
    (SITE / "meta.json").write_text(
        json.dumps({"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(), "count": len(items)}),
        encoding="utf-8")
    # Pages: don't run the content through Jekyll
    (SITE / ".nojekyll").write_text("", encoding="utf-8")
    print(f"built site/ — {len(items)} items across {len(firms)} firms")


if __name__ == "__main__":
    main()
