#!/usr/bin/env python3
"""Longitudinal stance-drift detection → site/drift.json.

For each firm × topic over a recent window, derive a directional stance and append
a dated snapshot to a `stance_snapshots` table inside that firm's SQLite DB (which
the workflow already persists to the `data` branch — so the time series survives
across runs with no extra plumbing). Drift = the stance label for a (firm, topic)
changing versus its previous snapshot, e.g. JPM on rates: cautious → constructive.

LLM-optional like enrich/synthesis: with ANTHROPIC_API_KEY, Claude reads the items
and labels the stance; without a key, a small bull/bear lexicon gives a deterministic
lean. Run AFTER build_static.py (writes into the existing site/), BEFORE the persist
step (it appends to the DBs).
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
WINDOW_DAYS = 21
MIN_ITEMS = 3
SCORE_THRESHOLD = 0.2          # |score| above this → directional; else neutral
STANCE_MODEL = "claude-opus-4-8"

_SNAP_SCHEMA = """
CREATE TABLE IF NOT EXISTS stance_snapshots (
    date   TEXT, topic TEXT, label TEXT, score REAL, n INTEGER,
    PRIMARY KEY (date, topic)
);
"""

_BULL = ["bullish", "constructive", "overweight", "upside", "rally", "optimistic",
         "outperform", "favor", "tailwind", "recovery", "resilient", "strength", "add risk"]
_BEAR = ["bearish", "cautious", "underweight", "downside", "sell-off", "selloff", "recession",
         "headwind", "weak", "downgrade", "correction", "slowdown", "defensive", "reduce risk"]

SYSTEM = (
    "You classify a firm's directional stance on one market topic from its recent items. "
    "Use only the provided items. Return a label and a score in [-1,1] (negative = bearish/"
    "cautious, positive = bullish/constructive, ~0 = neutral/mixed)."
)
SCHEMA = {
    "type": "object",
    "properties": {
        "label": {"type": "string", "enum": ["bullish", "constructive", "neutral", "cautious", "bearish"]},
        "score": {"type": "number"},
    },
    "required": ["label", "score"],
    "additionalProperties": False,
}


def _since_iso(days: int) -> str:
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).isoformat()


def _lexicon_stance(texts: list[str]) -> tuple[str, float]:
    blob = " ".join(texts).lower()
    bull = sum(blob.count(w) for w in _BULL)
    bear = sum(blob.count(w) for w in _BEAR)
    if not (bull or bear):
        return "neutral", 0.0
    score = (bull - bear) / (bull + bear)
    label = "bullish" if score > SCORE_THRESHOLD else "bearish" if score < -SCORE_THRESHOLD else "neutral"
    return label, round(score, 2)


def _llm_stance(client, topic: str, items: list[dict]) -> tuple[str, float]:
    ev = "\n".join(f"- {it['title']} — {(it['summary'] or '')[:200]}" for it in items[:14])
    try:
        resp = client.messages.create(
            model=STANCE_MODEL, max_tokens=120, system=SYSTEM,
            messages=[{"role": "user", "content": f"Topic: {topic}\nItems:\n{ev}\n\nReturn JSON: label, score."}],
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        )
        text = next((b.text for b in resp.content if b.type == "text"), "")
        d = json.loads(text)
        return str(d.get("label", "neutral")), round(float(d.get("score", 0)), 2)
    except Exception:  # noqa: BLE001
        return _lexicon_stance([f"{it['title']} {it['summary']}" for it in items])


def main() -> None:
    today = dt.date.today().isoformat()
    since = _since_iso(WINDOW_DAYS)
    client = None
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            import anthropic
            client = anthropic.Anthropic()
        except Exception:  # noqa: BLE001
            client = None

    drifts: list[dict] = []
    points = 0
    for db in sorted(ROOT.glob("firms/*/data/insights.db")):
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        conn.executescript(_SNAP_SCHEMA)
        fr = conn.execute("SELECT firm FROM items WHERE firm != '' LIMIT 1").fetchone()
        firm = fr[0] if fr else db.parts[-3]
        rows = conn.execute(
            "SELECT title, llm_summary, raw_summary, topics FROM items "
            "WHERE COALESCE(published_at, ingested_at) >= ?", (since,)).fetchall()
        by_topic: dict[str, list[dict]] = {}
        for r in rows:
            tps = json.loads(r["topics"]) if r["topics"] else []
            for tp in tps:
                by_topic.setdefault(tp, []).append(
                    {"title": r["title"], "summary": r["llm_summary"] or r["raw_summary"] or ""})

        topics = [t for t in TOPICS if len(by_topic.get(t, [])) >= MIN_ITEMS]
        if client and topics:
            with ThreadPoolExecutor(max_workers=6) as pool:
                stances = list(pool.map(lambda t: _llm_stance(client, t, by_topic[t]), topics))
        else:
            stances = [_lexicon_stance([f"{i['title']} {i['summary']}" for i in by_topic[t]]) for t in topics]

        for topic, (label, score) in zip(topics, stances):
            n = len(by_topic[topic])
            prev = conn.execute(
                "SELECT date, label, score FROM stance_snapshots WHERE topic=? AND date<? "
                "ORDER BY date DESC LIMIT 1", (topic, today)).fetchone()
            conn.execute(
                "INSERT OR REPLACE INTO stance_snapshots (date, topic, label, score, n) VALUES (?,?,?,?,?)",
                (today, topic, label, score, n))
            points += 1
            if prev and prev["label"] != label:
                drifts.append({"firm": firm, "topic": topic,
                               "from": prev["label"], "to": label,
                               "from_date": prev["date"], "to_date": today,
                               "score": score, "n": n})
        conn.commit()
        conn.close()

    drifts.sort(key=lambda d: abs(d["score"]), reverse=True)
    payload = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
               "window_days": WINDOW_DAYS, "llm": bool(client),
               "points": points, "drifts": drifts}
    SITE.mkdir(parents=True, exist_ok=True)
    (SITE / "drift.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    mode = "Claude (Opus)" if client else "lexicon fallback (no API key)"
    print(f"drift.json — {points} stance point(s), {len(drifts)} shift(s) via {mode}")


if __name__ == "__main__":
    main()
