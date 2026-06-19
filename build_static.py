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
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
SITE = ROOT / "site"

# First paint ships only the last RECENT_DAYS (all firms); older items lazy-load
# from data-archive.json on demand — keeps the first download small once all 31
# firms are live (the full corpus is tens of MB).
RECENT_DAYS = 60

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
    "Man Group":       {"short": "Man", "color": "#3E4A89", "order": 90},
    "Verdad":          {"short": "VRD", "color": "#556B7D", "order": 93},
    "Research Affiliates": {"short": "RAFI", "color": "#B5651D", "order": 94},
}
DEFAULT = {"short": "", "color": "#8A93A6", "order": 99}
# Firms whose DB stays (for a future re-attempt) but are hidden from the site —
# no scrapable public source. Schroders: podcast feeds 404 + article pages are
# JS-rendered/Akamai-blocked (no og:title), and we don't run a headless browser.
HIDDEN_FIRMS = {"Schroders"}
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
    # Hedge funds (publish public research; secretive multi-strats excluded)
    "AQR": "hf", "Man Group": "hf", "Verdad": "hf", "Research Affiliates": "hf",
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


def collapse_clusters(rows: list[dict]) -> list[dict]:
    """Collapse cross-channel near-duplicates the pipeline already clustered.
    The dedup step stamps every clustered item with `cluster_id` = the canonical
    (earliest-published) member's id; singletons point at themselves. Keep one card
    per cluster (the canonical), and borrow a readable/playable link from a dropped
    member if the canonical lacks one, so we don't lose the alternate feed."""
    present = {d["id"] for d in rows}
    groups: dict[str, list[dict]] = defaultdict(list)
    for d in rows:
        cid = d.get("cluster_id") or ""
        key = cid if (cid and cid in present) else d["id"]
        groups[key].append(d)

    kept: list[dict] = []
    for key, members in groups.items():
        if len(members) == 1:
            kept.append(members[0])
            continue
        canon = next((d for d in members if d["id"] == key), None)
        if canon is None:          # canonical not in this DB — keep members as-is
            kept.extend(members)
            continue
        for d in members:
            if d is canon:
                continue
            if not canon.get("url") and d.get("url"):
                canon["url"] = d["url"]
            if not canon.get("audio_url") and d.get("audio_url"):
                canon["audio_url"] = d["audio_url"]
        kept.append(canon)
    return kept


def load():
    items, firms = [], {}
    collapsed = 0
    for db in sorted(ROOT.glob("firms/*/data/insights.db")):
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        fr = conn.execute("SELECT firm FROM items WHERE firm != '' LIMIT 1").fetchone()
        if not fr:
            conn.close()
            continue
        firm = fr[0]
        if firm in HIDDEN_FIRMS:           # firm has no scrapable public source → keep the DB, hide from the site
            conn.close()
            continue
        meta = FIRM_META.get(firm, DEFAULT)
        category = FIRM_CATEGORY.get(firm, "am")
        firms[firm] = {"firm": firm, "short": meta["short"] or firm[:3].upper(),
                       "color": meta["color"], "order": meta["order"], "category": category}
        rows = [dict(r) for r in conn.execute("SELECT * FROM items")]
        kept = collapse_clusters(rows)
        collapsed += len(rows) - len(kept)
        for d in kept:
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
    return items, firms_list, collapsed


def collect_health(stale_hours: int = 6) -> dict:
    """Aggregate every firm's `data/last_run.json` (written by the core pipeline)
    into a single health view: which firms reported, which had failed sources,
    which returned zero items, and which are stale. The data spine for the P1
    health view; a summary is also folded into meta.json."""
    now = dt.datetime.now(dt.timezone.utc)
    firms_health = []
    for rep_path in sorted(ROOT.glob("firms/*/data/last_run.json")):
        try:
            r = json.loads(rep_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — a missing/corrupt report shouldn't break the build
            continue
        ended = r.get("ended_at")
        age_h = None
        if ended:
            try:
                age_h = round((now - dt.datetime.fromisoformat(ended)).total_seconds() / 3600, 1)
            except ValueError:
                pass
        firms_health.append({
            "firm": r.get("firm", rep_path.parts[-3]),
            "ended_at": ended,
            "age_hours": age_h,
            "duration_s": r.get("duration_s"),
            "sources_total": r.get("sources_total", 0),
            "sources_ok": r.get("sources_ok", 0),
            "sources_failed": r.get("sources_failed", 0),
            "failed_sources": [{"name": s["name"], "error": s["error"]}
                               for s in r.get("sources", []) if not s.get("ok")],
            "new_items": r.get("new_items", 0),
            "total": r.get("total", 0),
            "enriched_total": r.get("enriched_total", 0),
            "enrich_llm_ok": r.get("enrich_llm_ok", 0),
            "llm_available": r.get("llm_available", False),
            "stale": (age_h is not None and age_h > stale_hours),
        })
    firms_health.sort(key=lambda h: (-h["sources_failed"], -(h["total"] or 0)))
    summary = {
        "firms_reporting": len(firms_health),
        "firms_with_failures": sum(1 for h in firms_health if h["sources_failed"]),
        "firms_zero_items": sum(1 for h in firms_health if not h["total"]),
        "firms_stale": sum(1 for h in firms_health if h["stale"]),
        "failed_sources": sum(h["sources_failed"] for h in firms_health),
    }
    return {"generated_at": now.isoformat(), "summary": summary, "firms": firms_health}


def write_feed(items: list[dict], site: Path, limit: int = 50) -> int:
    """RSS 2.0 of the latest items — a backend-free subscribe/digest/alert channel
    (point any reader at site/feed.xml). Personalization stays client-side; this is
    the firehose. The hourly/daily workflow regenerates it."""
    from email.utils import format_datetime
    from xml.sax.saxutils import escape

    def rfc822(iso):
        try:
            return format_datetime(dt.datetime.fromisoformat(iso))
        except (ValueError, TypeError):
            return ""

    entries = []
    for it in items[:limit]:
        link = it["url"] or it["audio_url"]
        if not link:
            continue
        when = rfc822(it["published_at"] or it["ingested_at"])
        desc = f'{it["firm"]} · {it["source_name"]} — {it.get("summary", "")}'
        entries.append(
            "<item>"
            f"<title>{escape(it['title'])}</title>"
            f"<link>{escape(link)}</link>"
            f"<guid isPermaLink=\"false\">{escape(it['id'])}</guid>"
            f"<dc:creator>{escape(it['firm'])}</dc:creator>"
            f"<description>{escape(desc)}</description>"
            + (f"<pubDate>{when}</pubDate>" if when else "")
            + "</item>"
        )
    now = format_datetime(dt.datetime.now(dt.timezone.utc))
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        "<channel>"
        "<title>Insights Aggregator</title>"
        "<link>./index.html</link>"
        "<description>Investment insights from banks &amp; asset managers, aggregated.</description>"
        f"<lastBuildDate>{now}</lastBuildDate>"
        + "".join(entries)
        + "</channel></rss>\n"
    )
    (site / "feed.xml").write_text(xml, encoding="utf-8")
    return len(entries)


def main():
    items, firms, collapsed = load()
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

    # "Recent" = items with a REAL publish date inside the window. Undated items
    # (no published_at — some sitemaps expose no date, e.g. AQR working papers)
    # can't honestly claim recency, so they go to the archive instead of flooding
    # the default view with everything stamped "today".
    cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=RECENT_DAYS)).isoformat()
    recent, archive = [], []
    for it in items:
        (recent if (it["published_at"] or "") >= cutoff else archive).append(it)
    (SITE / "data.json").write_text(
        json.dumps(recent, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (SITE / "data-archive.json").write_text(
        json.dumps(archive, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (SITE / "facets.json").write_text(
        json.dumps({"firms": firms, "categories": categories, "business_units": units,
                    "content_types": types, "topics": TOPICS},
                   ensure_ascii=False), encoding="utf-8")
    feed_n = write_feed(items, SITE)
    health = collect_health()
    (SITE / "health.json").write_text(
        json.dumps(health, ensure_ascii=False), encoding="utf-8")
    (SITE / "meta.json").write_text(
        json.dumps({"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                    "count": len(items), "recent_count": len(recent),
                    "archive_count": len(archive), "window_days": RECENT_DAYS,
                    "health": health["summary"]}),
        encoding="utf-8")
    # Pages: don't run the content through Jekyll
    (SITE / ".nojekyll").write_text("", encoding="utf-8")
    h = health["summary"]
    print(f"built site/ — {len(items)} items across {len(firms)} firms"
          + (f" ({collapsed} collapsed)" if collapsed else "")
          + f" · {len(recent)} recent + {len(archive)} archive · feed.xml {feed_n}")
    if h["firms_reporting"]:
        print(f"  health: {h['firms_reporting']} firms reported · "
              f"{h['firms_with_failures']} with failed sources · "
              f"{h['firms_zero_items']} zero-item · {h['firms_stale']} stale · "
              f"{h['failed_sources']} failed source(s)")


if __name__ == "__main__":
    main()
