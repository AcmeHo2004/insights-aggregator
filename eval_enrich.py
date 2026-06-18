#!/usr/bin/env python3
"""Minimal LLM-as-judge quality check for enrichment summaries.

Samples enriched items across all firm DBs and asks Claude to rate whether each
generated summary stays faithful to the source (title + feed show-notes) without
inventing facts. Prints an aggregate and writes eval_report.json. This is the
quality loop that guards against silent hallucination drift in summaries (and,
by extension, the cross-firm synthesis built on them).

Key-gated: with no ANTHROPIC_API_KEY it explains and exits 0 (so CI stays green
on forks/PRs without secrets). Run: python eval_enrich.py [--n 30]
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent
JUDGE_MODEL = "claude-opus-4-8"

SYSTEM = (
    "You are a strict QA judge for AI-generated financial summaries. Given a source "
    "(title + the source's own short description) and a generated summary, decide whether "
    "the summary is FAITHFUL: it must only state things supported by the source and must "
    "not invent figures, claims, or positions. Minor compression/omission is fine."
)
SCHEMA = {
    "type": "object",
    "properties": {
        "faithful": {"type": "boolean"},
        "score": {"type": "integer", "description": "1 (fabricated) to 5 (fully faithful)"},
        "issue": {"type": ["string", "null"], "description": "Short note if not faithful, else null"},
    },
    "required": ["faithful", "score", "issue"],
    "additionalProperties": False,
}


def sample_enriched(n: int) -> list[dict]:
    rows: list[dict] = []
    for db in sorted(ROOT.glob("firms/*/data/insights.db")):
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            for r in conn.execute(
                "SELECT firm, title, raw_summary, llm_summary FROM items "
                "WHERE llm_summary != '' AND enriched = 1"):
                rows.append(dict(r))
        except sqlite3.Error:
            pass
        conn.close()
    random.shuffle(rows)
    return rows[:n]


def judge(client, it: dict) -> dict:
    user = (f"Title: {it['title']}\n"
            f"Source description: {it['raw_summary'] or '(none provided)'}\n"
            f"Generated summary: {it['llm_summary']}\n\n"
            "Return JSON: faithful, score (1-5), issue.")
    try:
        resp = client.messages.create(
            model=JUDGE_MODEL, max_tokens=300, system=SYSTEM,
            messages=[{"role": "user", "content": user}],
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        )
        text = next((b.text for b in resp.content if b.type == "text"), "")
        d = json.loads(text)
        return {"firm": it["firm"], "title": it["title"][:80],
                "faithful": bool(d.get("faithful")), "score": int(d.get("score", 0)),
                "issue": d.get("issue")}
    except Exception as e:  # noqa: BLE001
        return {"firm": it["firm"], "title": it["title"][:80], "error": str(e)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30, help="sample size")
    args = ap.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("eval_enrich: ANTHROPIC_API_KEY not set — skipping LLM judge (exit 0).")
        return 0
    sample = sample_enriched(args.n)
    if not sample:
        print("eval_enrich: no enriched items found to evaluate (exit 0).")
        return 0

    import anthropic
    client = anthropic.Anthropic()
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda it: judge(client, it), sample))

    scored = [r for r in results if "score" in r]
    mean = sum(r["score"] for r in scored) / len(scored) if scored else 0
    faithful = sum(1 for r in scored if r["faithful"])
    low = sorted((r for r in scored if not r["faithful"]), key=lambda r: r["score"])

    report = {"n": len(sample), "judged": len(scored), "mean_score": round(mean, 2),
              "faithful_pct": round(100 * faithful / len(scored), 1) if scored else 0,
              "flagged": low[:20]}
    (ROOT / "eval_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"eval_enrich: {report['judged']}/{report['n']} judged · "
          f"mean {report['mean_score']}/5 · faithful {report['faithful_pct']}%")
    for r in low[:10]:
        print(f"  ⚠ [{r['firm']}] score {r['score']}: {r['title']} — {r.get('issue')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
