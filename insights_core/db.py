"""SQLite store for normalized Items (spec §7: SQLite, single user, zero-ops).

`connect(db_path)` takes the firm's DB path explicitly — the caller (pipeline)
derives it from the firm_root, so this module is firm-agnostic.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional

from .models import Item

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id            TEXT PRIMARY KEY,
    firm          TEXT,
    business_unit TEXT,
    source_name   TEXT,
    source_type   TEXT,
    content_type  TEXT,
    title         TEXT,
    url           TEXT,
    canonical_url TEXT,
    published_at  TEXT,
    dedup_key     TEXT,
    guid          TEXT,
    audio_url     TEXT,
    authors       TEXT,   -- json
    topics        TEXT,   -- json
    asset_class   TEXT,   -- json
    raw_summary   TEXT,
    llm_summary   TEXT,
    why_it_matters TEXT,
    tier          INTEGER,
    cluster_id    TEXT,
    enriched      INTEGER DEFAULT 0,
    is_read       INTEGER DEFAULT 0,
    is_starred    INTEGER DEFAULT 0,
    ingested_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_items_published ON items(published_at);
CREATE INDEX IF NOT EXISTS idx_items_dedup ON items(dedup_key);
CREATE INDEX IF NOT EXISTS idx_items_cluster ON items(cluster_id);
"""

_LIST_FIELDS = ("authors", "topics", "asset_class")
_BOOL_FIELDS = ("enriched", "is_read", "is_starred")


def connect(db_path) -> sqlite3.Connection:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def _row_to_item(row: sqlite3.Row) -> Item:
    data = dict(row)
    for f in _LIST_FIELDS:
        data[f] = json.loads(data[f]) if data[f] else []
    for f in _BOOL_FIELDS:
        data[f] = bool(data[f])
    return Item(**data)


def _item_to_params(item: Item) -> dict:
    d = item.to_dict()
    for f in _LIST_FIELDS:
        d[f] = json.dumps(d[f])
    for f in _BOOL_FIELDS:
        d[f] = int(bool(d[f]))
    return d


def existing_ids(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT id FROM items")}


def existing_dedup_keys(conn: sqlite3.Connection) -> set[str]:
    return {r[0] for r in conn.execute("SELECT dedup_key FROM items WHERE dedup_key != ''")}


def insert_item(conn: sqlite3.Connection, item: Item) -> bool:
    """Insert a new item. Returns True if inserted, False if it already existed."""
    params = _item_to_params(item)
    cols = ", ".join(params.keys())
    placeholders = ", ".join(f":{k}" for k in params.keys())
    cur = conn.execute(
        f"INSERT OR IGNORE INTO items ({cols}) VALUES ({placeholders})", params
    )
    return cur.rowcount > 0


def update_enrichment(
    conn: sqlite3.Connection,
    item_id: str,
    *,
    llm_summary: str,
    why_it_matters: str,
    topics: list[str],
    asset_class: list[str],
    enriched: bool,
) -> None:
    conn.execute(
        """UPDATE items SET llm_summary=?, why_it_matters=?, topics=?, asset_class=?, enriched=?
           WHERE id=?""",
        (
            llm_summary,
            why_it_matters,
            json.dumps(topics),
            json.dumps(asset_class),
            int(enriched),
            item_id,
        ),
    )


def set_cluster(conn: sqlite3.Connection, item_id: str, cluster_id: str) -> None:
    conn.execute("UPDATE items SET cluster_id=? WHERE id=?", (cluster_id, item_id))


def set_flag(conn: sqlite3.Connection, item_id: str, flag: str, value: bool) -> None:
    if flag not in ("is_read", "is_starred"):
        raise ValueError(f"unsupported flag: {flag}")
    conn.execute(f"UPDATE items SET {flag}=? WHERE id=?", (int(value), item_id))
    conn.commit()


def items_needing_enrichment(
    conn: sqlite3.Connection, since_iso: Optional[str], limit_per_source: int
) -> list[Item]:
    """New/unenriched items published since `since_iso`, capped per source."""
    rows = conn.execute(
        """SELECT * FROM items
           WHERE enriched = 0 AND (? IS NULL OR published_at IS NULL OR published_at >= ?)
           ORDER BY source_name, COALESCE(published_at, ingested_at) DESC""",
        (since_iso, since_iso),
    ).fetchall()

    out: list[Item] = []
    per_source: dict[str, int] = {}
    for row in rows:
        item = _row_to_item(row)
        n = per_source.get(item.source_name, 0)
        if n >= limit_per_source:
            continue
        per_source[item.source_name] = n + 1
        out.append(item)
    return out


def unenriched_items(
    conn: sqlite3.Connection,
    *,
    since_iso: Optional[str] = None,
    max_tier: Optional[int] = None,
    limit: Optional[int] = None,
) -> list[Item]:
    """All items still needing enrichment (no per-source cap) — for backfill.
    Optional filters: recency window, max tier (tier<=N), and a hard limit."""
    where = ["enriched = 0"]
    params: list = []
    if since_iso:
        where.append("(published_at IS NULL OR published_at >= ?)")
        params.append(since_iso)
    if max_tier is not None:
        where.append("tier <= ?")
        params.append(max_tier)
    sql = (
        f"SELECT * FROM items WHERE {' AND '.join(where)} "
        f"ORDER BY tier ASC, COALESCE(published_at, ingested_at) DESC"
    )
    if limit:
        sql += " LIMIT ?"
        params.append(limit)
    return [_row_to_item(r) for r in conn.execute(sql, params).fetchall()]


def recent_items(conn: sqlite3.Connection, since_iso: Optional[str]) -> list[Item]:
    rows = conn.execute(
        """SELECT * FROM items
           WHERE (? IS NULL OR published_at >= ?)
           ORDER BY published_at DESC""",
        (since_iso, since_iso),
    ).fetchall()
    return [_row_to_item(r) for r in rows]


def distinct_values(conn: sqlite3.Connection, column: str) -> list[str]:
    if column not in ("business_unit", "content_type", "source_name"):
        raise ValueError(column)
    rows = conn.execute(
        f"SELECT DISTINCT {column} FROM items WHERE {column} != '' ORDER BY {column}"
    ).fetchall()
    return [r[0] for r in rows]


def counts(conn: sqlite3.Connection) -> dict:
    total = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    enriched = conn.execute("SELECT COUNT(*) FROM items WHERE enriched=1").fetchone()[0]
    return {"total": total, "enriched": enriched}
