"""Minimal scannable dashboard (spec §6): one page, cards, filters, read/star."""

from __future__ import annotations

from datetime import datetime

from flask import Flask, jsonify, render_template, request

from . import db
from .config import DB_PATH
from .models import TOPICS

app = Flask(__name__)


def _fmt_date(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        return datetime.fromisoformat(iso).strftime("%b %d, %Y")
    except ValueError:
        return iso[:10]


def _collapse_clusters(items) -> list[dict]:
    """Collapse cross-channel duplicates into one card, keeping each form's link."""
    groups: dict[str, list] = {}
    order: list[str] = []
    for it in items:
        cid = it.cluster_id or it.id
        if cid not in groups:
            groups[cid] = []
            order.append(cid)
        groups[cid].append(it)

    cards = []
    for cid in order:
        members = groups[cid]
        # Representative card = newest member that survived the filter.
        members.sort(key=lambda x: (x.published_at or "", x.id), reverse=True)
        rep = members[0]
        alts = [
            {"source_name": m.source_name, "url": m.url}
            for m in members[1:]
        ]
        cards.append(
            {
                "id": rep.id,
                "firm": rep.firm,
                "business_unit": rep.business_unit,
                "source_name": rep.source_name,
                "content_type": rep.content_type,
                "title": rep.title,
                "url": rep.url,
                "date": _fmt_date(rep.published_at),
                "published_at": rep.published_at or "",
                "summary": rep.llm_summary or rep.raw_summary,
                "is_llm": bool(rep.llm_summary),
                "why_it_matters": rep.why_it_matters,
                "topics": rep.topics,
                "asset_class": rep.asset_class,
                "tier": rep.tier,
                "is_read": rep.is_read,
                "is_starred": rep.is_starred,
                "audio_url": rep.audio_url,
                "alts": alts,
            }
        )
    return cards


@app.route("/")
def index():
    conn = db.connect(DB_PATH)
    db.init_db(conn)

    f = request.args
    items = db.query_items(
        conn,
        business_unit=f.get("business_unit") or None,
        topic=f.get("topic") or None,
        content_type=f.get("content_type") or None,
        source_name=f.get("source") or None,
        unread_only=f.get("unread") == "1",
        starred_only=f.get("starred") == "1",
        limit=600,
    )
    cards = _collapse_clusters(items)

    if f.get("sort") == "tier":
        cards.sort(key=lambda c: (c["tier"], _neg_iso(c["published_at"])))
    # default ordering already newest-first from the query

    facets = {
        "business_units": db.distinct_values(conn, "business_unit"),
        "sources": db.distinct_values(conn, "source_name"),
        "content_types": db.distinct_values(conn, "content_type"),
        "topics": TOPICS,
    }
    stats = db.counts(conn)
    conn.close()

    return render_template(
        "index.html",
        cards=cards,
        facets=facets,
        active=dict(f),
        stats=stats,
    )


def _neg_iso(iso: str) -> str:
    # helper to sort newest-first as a secondary key under a numeric primary key
    return "".join(chr(255 - ord(c)) for c in iso) if iso else ""


@app.route("/api/<flag>/<item_id>", methods=["POST"])
def set_flag(flag: str, item_id: str):
    if flag not in ("read", "star"):
        return jsonify({"error": "unknown flag"}), 400
    column = "is_read" if flag == "read" else "is_starred"
    value = request.json.get("value", True) if request.is_json else True
    conn = db.connect(DB_PATH)
    db.set_flag(conn, item_id, column, bool(value))
    conn.close()
    return jsonify({"ok": True})


def main():
    import argparse

    parser = argparse.ArgumentParser(description="abrdn Insights dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    print(f"\n  abrdn Insights dashboard → http://{args.host}:{args.port}\n")
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
