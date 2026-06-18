#!/usr/bin/env python3
"""Cross-firm thematic synthesis → site/synthesis.json.

Reads every firm's SQLite DB, groups recent items by topic, and for each theme
produces a short consensus / divergence / stance-shift read — **grounded only in
the cited items** (each theme carries the source items it was drawn from, so the
UI can link every claim back to the originals; no uncited cross-firm assertions).

LLM-optional, like enrich: with ANTHROPIC_API_KEY it uses Claude (Opus) for the
prose; without a key it falls back to a deterministic rollup (counts + firms +
top items). Run AFTER build_static.py (it writes into the existing site/).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from insights_core.models import TOPICS

ROOT = Path(__file__).resolve().parent
SITE = ROOT / "site"

WINDOW_DAYS = 14          # recency window for "what firms are saying now"
MIN_ITEMS = 2             # a topic needs at least this many items to be a theme
MAX_THEMES = 12
MAX_CITATIONS = 6         # source items surfaced per theme
MAX_EVIDENCE = 14         # items shown to the model per theme
SYNTH_MODEL = "claude-opus-4-8"

SYSTEM_PROMPT = (
    "You are a cross-firm markets analyst. You are given recent insight items "
    "(firm + title + short summary) that all touch one topic. Write a tight read "
    "of what the firms are collectively saying. Ground every statement ONLY in the "
    "provided items — refer to firms by name, never invent positions or numbers. "
    "If the items don't support a divergence or a stance shift, return null for it."
)

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "consensus": {"type": "string", "description": "1-2 sentences: the shared view across firms (cite firms)."},
        "divergence": {"type": ["string", "null"], "description": "1 sentence: where firms disagree, or null."},
        "shift": {"type": ["string", "null"], "description": "1 sentence: a notable stance change visible within these items, or null."},
    },
    "required": ["consensus", "divergence", "shift"],
    "additionalProperties": False,
}


def _since_iso(days: int) -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).isoformat()


def load_recent() -> list[dict]:
    since = _since_iso(WINDOW_DAYS)
    items: list[dict] = []
    for db in sorted(ROOT.glob("firms/*/data/insights.db")):
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT firm, title, url, audio_url, llm_summary, raw_summary, topics, "
                "published_at, ingested_at, tier FROM items "
                "WHERE COALESCE(published_at, ingested_at) >= ?", (since,)).fetchall()
        except sqlite3.Error:
            conn.close()
            continue
        for r in rows:
            d = dict(r)
            items.append({
                "firm": d["firm"],
                "title": d["title"],
                "url": d["url"] or d["audio_url"] or "",
                "summary": d["llm_summary"] or d["raw_summary"] or "",
                "topics": json.loads(d["topics"]) if d["topics"] else [],
                "published_at": d["published_at"] or d["ingested_at"],
                "tier": d["tier"] or 3,
            })
        conn.close()
    return items


def group_themes(items: list[dict]) -> list[dict]:
    themes = []
    for topic in TOPICS:
        members = [it for it in items if topic in (it["topics"] or [])]
        if len(members) < MIN_ITEMS:
            continue
        members.sort(key=lambda it: it["published_at"] or "", reverse=True)  # newest first
        members.sort(key=lambda it: it["tier"])                              # then tier (stable)
        firms = sorted({it["firm"] for it in members})
        themes.append({
            "topic": topic,
            "firm_count": len(firms),
            "item_count": len(members),
            "firms": firms,
            "citations": [{"firm": it["firm"], "title": it["title"], "url": it["url"],
                           "published_at": it["published_at"]} for it in members[:MAX_CITATIONS]],
            "_evidence": members[:MAX_EVIDENCE],
        })
    themes.sort(key=lambda t: (t["firm_count"], t["item_count"]), reverse=True)
    return themes[:MAX_THEMES]


def _fallback(theme: dict) -> dict:
    n, m = theme["item_count"], theme["firm_count"]
    firms = ", ".join(theme["firms"][:6]) + ("…" if m > 6 else "")
    return {
        "consensus": f"{n} item(s) from {m} firm(s) in the last {WINDOW_DAYS} days — {firms}.",
        "divergence": None,
        "shift": None,
    }


def _llm_synthesis(client, theme: dict) -> dict:
    evidence = "\n".join(
        f"{i+1}. [{it['firm']}] {it['title']} — {(it['summary'] or '')[:280]}"
        for i, it in enumerate(theme["_evidence"]))
    user = (f"Topic: {theme['topic']}\nItems ({theme['item_count']} across "
            f"{theme['firm_count']} firms):\n{evidence}\n\n"
            "Return JSON: consensus, divergence, shift (grounded only in the items above).")
    try:
        resp = client.messages.create(
            model=SYNTH_MODEL, max_tokens=600, system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
        )
        text = next((b.text for b in resp.content if b.type == "text"), "")
        data = json.loads(text)
        return {"consensus": str(data.get("consensus", "")).strip() or _fallback(theme)["consensus"],
                "divergence": (data.get("divergence") or None),
                "shift": (data.get("shift") or None)}
    except Exception:  # noqa: BLE001 — never fail the build; degrade to the rollup
        return _fallback(theme)


def main() -> None:
    items = load_recent()
    themes = group_themes(items)

    client = None
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            import anthropic
            client = anthropic.Anthropic()
        except Exception:  # noqa: BLE001
            client = None

    if client and themes:
        with ThreadPoolExecutor(max_workers=6) as pool:
            reads = list(pool.map(lambda t: _llm_synthesis(client, t), themes))
    else:
        reads = [_fallback(t) for t in themes]

    out_themes = []
    for theme, read in zip(themes, reads):
        out_themes.append({
            "topic": theme["topic"],
            "firm_count": theme["firm_count"],
            "item_count": theme["item_count"],
            "firms": theme["firms"],
            "consensus": read["consensus"],
            "divergence": read["divergence"],
            "shift": read["shift"],
            "citations": theme["citations"],
        })

    payload = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "window_days": WINDOW_DAYS,
        "llm": bool(client),
        "themes": out_themes,
    }
    SITE.mkdir(parents=True, exist_ok=True)
    (SITE / "synthesis.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    mode = "Claude (Opus)" if client else "deterministic fallback (no API key)"
    print(f"synthesis.json — {len(out_themes)} theme(s) via {mode}")


if __name__ == "__main__":
    main()
