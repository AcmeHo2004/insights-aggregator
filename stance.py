#!/usr/bin/env python3
"""Firm × asset-class stance grading → site/stance.json.

For each firm, reads its recent notes grouped by asset class and grades a stance
(overweight / neutral / underweight) per asset class, grounded ONLY in that
firm's own items. The Consensus Map in the UI prefers this file over its built-in
client-side lexicon.

LLM-optional, like synthesize/drift: with ANTHROPIC_API_KEY it uses Claude (Opus)
for nuanced grading + a one-line rationale; without a key it falls back to a
deterministic sentiment-lexicon score (the same one the UI ships). One Claude
call per firm (stances for all its covered asset classes at once). Run AFTER
build_static.py — it writes into the existing site/.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SITE = ROOT / "site"

WINDOW_DAYS = 90          # "current positioning" lookback
MIN_ITEMS = 2             # a firm×asset-class needs >= this many items to grade
MAX_EVIDENCE = 10         # items shown to the model per asset class
STANCE_MODEL = "claude-opus-4-8"
ASSET_CLASSES = ["macro", "rates", "equities", "credit", "fixed-income",
                 "fx", "commodities", "multi-asset"]

BULL = re.compile(r"\b(overweight|add(?:ing|s)?|bullish|constructive|favou?rs?|attractive|"
                  r"opportunit\w*|upside|prefer\w*|tailwind\w*|resilient|outperform\w*|cheap|undervalued)\b", re.I)
BEAR = re.compile(r"\b(underweight|reduc\w*|trim\w*|bearish|caution\w*|defensive|downside|avoid\w*|"
                  r"headwind\w*|expensive|rich|overvalued|vulnerable|underperform\w*|fragile|stretched)\b", re.I)

SYSTEM_PROMPT = (
    "You are a buy-side strategist reading ONE investment firm's own recent research "
    "notes. For each asset class you are given, grade that firm's current stance as "
    "exactly one of: overweight, neutral, underweight. Base each grade ONLY on that "
    "firm's provided notes — never use outside knowledge. If a firm's notes on an "
    "asset class are mixed, descriptive, or have no directional view, return neutral. "
    "Give a one-line rationale that refers only to what the notes say."
)

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "stances": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "topic": {"type": "string", "enum": ASSET_CLASSES},
                    "stance": {"type": "string", "enum": ["overweight", "neutral", "underweight"]},
                    "rationale": {"type": "string", "description": "one line, grounded in the firm's notes"},
                },
                "required": ["topic", "stance", "rationale"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["stances"],
    "additionalProperties": False,
}


def _since_iso(days: int) -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).isoformat()


def load_by_firm() -> dict[str, dict[str, list[dict]]]:
    """{firm: {asset_class: [items]}} for items with a real date in the window."""
    since = _since_iso(WINDOW_DAYS)
    by_firm: dict[str, dict[str, list[dict]]] = {}
    for db in sorted(ROOT.glob("firms/*/data/insights.db")):
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT firm, title, llm_summary, raw_summary, why_it_matters, topics, published_at "
                "FROM items WHERE published_at IS NOT NULL AND published_at >= ?", (since,)).fetchall()
        except sqlite3.Error:
            conn.close()
            continue
        for r in rows:
            d = dict(r)
            topics = json.loads(d["topics"]) if d["topics"] else []
            item = {
                "firm": d["firm"],
                "title": d["title"] or "",
                "summary": d["llm_summary"] or d["raw_summary"] or "",
                "why": d["why_it_matters"] or "",
            }
            fm = by_firm.setdefault(d["firm"], {})
            for tp in topics:
                if tp in ASSET_CLASSES:
                    fm.setdefault(tp, []).append(item)
        conn.close()
    return by_firm


def _lexicon_stance(items: list[dict]) -> tuple[str, int]:
    bull = bear = 0
    for it in items:
        txt = f"{it['title']} {it['summary']} {it['why']}"
        bull += len(BULL.findall(txt))
        bear += len(BEAR.findall(txt))
    net = bull - bear
    return ("overweight" if net >= 2 else "underweight" if net <= -2 else "neutral"), len(items)


def _fallback_firm(topics: dict[str, list[dict]]) -> dict[str, dict]:
    out = {}
    for tp, items in topics.items():
        if len(items) < MIN_ITEMS:
            continue
        stance, n = _lexicon_stance(items)
        out[tp] = {"stance": stance, "rationale": "", "n": n}
    return out


def _llm_firm(client, firm: str, topics: dict[str, list[dict]]) -> dict[str, dict]:
    graded = {tp: items for tp, items in topics.items() if len(items) >= MIN_ITEMS}
    if not graded:
        return {}
    counts = {tp: len(items) for tp, items in graded.items()}
    blocks = []
    for tp, items in graded.items():
        ev = "\n".join(f"  - {it['title']} — {(it['summary'] or '')[:200]}" for it in items[:MAX_EVIDENCE])
        blocks.append(f"## {tp} ({len(items)} notes)\n{ev}")
    user = (f"Firm: {firm}\nGrade {firm}'s current stance for each asset class below, "
            f"using only {firm}'s own notes.\n\n" + "\n\n".join(blocks))
    try:
        resp = client.messages.create(
            model=STANCE_MODEL, max_tokens=900, system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
        )
        text = next((b.text for b in resp.content if b.type == "text"), "")
        data = json.loads(text)
        out = {}
        for s in data.get("stances", []):
            tp = s.get("topic")
            if tp in graded:
                out[tp] = {"stance": s.get("stance", "neutral"),
                           "rationale": str(s.get("rationale", "")).strip(),
                           "n": counts[tp]}
        # any topic the model skipped → lexicon, so the cell still renders
        for tp in graded:
            out.setdefault(tp, {"stance": _lexicon_stance(graded[tp])[0], "rationale": "", "n": counts[tp]})
        return out
    except Exception:  # noqa: BLE001 — never fail the build; degrade to lexicon
        return _fallback_firm(topics)


def _fresh(iso: str, hours: float) -> bool:
    try:
        return (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(iso)).total_seconds() < hours * 3600
    except Exception:  # noqa: BLE001
        return False


def main() -> None:
    client = None
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            import anthropic
            client = anthropic.Anthropic()
        except Exception:  # noqa: BLE001
            client = None

    # Hourly (keyless) runs must NOT downgrade a recent Claude grade to lexicon.
    # 8-day window so a *weekly* backfill keeps the map Claude-graded (stance is
    # slow-moving — an 8-day-old grade still beats the lexicon).
    if not client and (SITE / "stance.json").exists():
        try:
            prev = json.loads((SITE / "stance.json").read_text())
            if prev.get("llm") and _fresh(prev.get("generated_at", ""), 24 * 8):
                print("stance.json — kept existing Claude grade (skipped hourly lexicon)")
                return
        except Exception:  # noqa: BLE001
            pass

    by_firm = load_by_firm()

    if client and by_firm:
        firms = list(by_firm)
        with ThreadPoolExecutor(max_workers=6) as pool:
            graded = list(pool.map(lambda f: _llm_firm(client, f, by_firm[f]), firms))
        stances = {f: g for f, g in zip(firms, graded) if g}
    else:
        stances = {f: _fallback_firm(t) for f, t in by_firm.items()}
        stances = {f: g for f, g in stances.items() if g}

    payload = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "window_days": WINDOW_DAYS,
        "llm": bool(client),
        "stances": stances,
    }
    SITE.mkdir(parents=True, exist_ok=True)
    (SITE / "stance.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    mode = "Claude (Opus)" if client else "deterministic lexicon (no API key)"
    print(f"stance.json — {len(stances)} firm(s) graded via {mode}")


if __name__ == "__main__":
    main()
