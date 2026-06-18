"""Orchestrate the scan: ingest -> normalize -> dedup -> enrich -> store.

Generic across firms: the caller passes `firm_root` (firms/<slug>/, for
sources.yaml + data/) and the `adapters` registry (core built-ins + firm-specific).
Each run writes a structured report to `<firm_root>/data/last_run.json` so
failures are observable (build_static.py aggregates these into site health).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import db, ingest
from .config import db_path, data_dir, load_config
from .dedup import assign_clusters
from .enrich import Enricher
from .types import Config

# Rough per-item token averages for cost estimation (title + show-notes in,
# short structured JSON out).
_EST_IN_TOKENS = 450
_EST_OUT_TOKENS = 170
# Pricing per 1M tokens ($ in / $ out). Used only for the estimate.
_PRICE = {"claude-sonnet-4-6": (3.0, 15.0), "claude-haiku-4-5": (1.0, 5.0),
          "claude-opus-4-8": (5.0, 25.0)}


def _est_cost(model: str, n: int) -> float:
    pin, pout = _PRICE.get(model, (3.0, 15.0))
    return n * (_EST_IN_TOKENS * pin + _EST_OUT_TOKENS * pout) / 1_000_000


def _since_iso(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _firm_name(config: Config, firm_root: Path) -> str:
    for s in config.sources:
        if s.firm:
            return s.firm
    return Path(firm_root).name


def _write_report(firm_root: Path, report: dict) -> None:
    d = data_dir(firm_root)
    d.mkdir(parents=True, exist_ok=True)
    (d / "last_run.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def run_backfill(
    firm_root: Path,
    *,
    workers: int = 6,
    max_tier: int | None = None,
    days: int | None = None,
    limit: int | None = None,
    estimate_only: bool = False,
    config: Config | None = None,
) -> dict:
    """Enrich the back-catalogue: every still-unenriched item (no per-source cap),
    via the Batches API (50% cheaper). Optional filters narrow scope/cost."""
    config = config or load_config(firm_root)
    conn = db.connect(db_path(firm_root))
    db.init_db(conn)
    model = str(config.settings["llm_model"])

    since = _since_iso(days) if days else None
    items = db.unenriched_items(conn, since_iso=since, max_tier=max_tier, limit=limit)
    n = len(items)
    est = _est_cost(model, n)

    print(f"  backfill scope: {n} unenriched item(s)"
          + (f", tier<={max_tier}" if max_tier else "")
          + (f", last {days}d" if days else ""))
    print(f"  model: {model}  ·  est. cost ≈ ${est:.2f} (rough; Batches ≈ 50% of this)")
    if estimate_only:
        conn.close()
        return {"count": n, "est_cost": est, "enriched": 0}

    enricher = Enricher(model=model)
    if not enricher.available:
        print("  ERROR: ANTHROPIC_API_KEY not available — cannot backfill.")
        conn.close()
        return {"count": n, "est_cost": est, "enriched": 0, "error": "no_api_key"}

    ok = 0
    results = enricher.enrich_many(items, workers=workers)
    for item, e in zip(items, results):
        db.update_enrichment(
            conn, item.id,
            llm_summary=e.summary, why_it_matters=e.why_it_matters,
            topics=e.topics, asset_class=e.asset_class, enriched=e.enriched,
        )
        ok += 1 if e.enriched else 0
    conn.commit()
    stats = db.counts(conn)
    conn.close()
    print(f"  done: {ok}/{n} LLM-summarized  |  store now {stats['enriched']}/{stats['total']} enriched")
    return {"count": n, "est_cost": est, "enriched": ok}


def run_scan(firm_root: Path, adapters: dict, *, config: Config | None = None, verbose: bool = True) -> dict:
    config = config or load_config(firm_root)
    conn = db.connect(db_path(firm_root))
    db.init_db(conn)
    firm = _firm_name(config, firm_root)
    started = datetime.now(timezone.utc)

    def log(msg: str) -> None:
        if verbose:
            print(msg)

    log("=" * 64)
    log(f"{firm} Insights scan — {started.strftime('%Y-%m-%d %H:%M:%S')}")
    log("=" * 64)

    # 1. Ingest + normalize + store new items (new-content detection: spec §5).
    known = db.existing_ids(conn)
    new_total = 0
    source_reports: list[dict] = []
    for source in config.sources:
        result = ingest.collect(source, config.settings, known, adapters)
        if not result.ok:
            log(f"  [skip] {source.name}: {result.error}")
            source_reports.append({"name": source.name, "ok": False, "items": 0, "new": 0, "error": result.error})
            continue

        new_here = 0
        for item in result.items:
            if item.id in known:
                continue
            if db.insert_item(conn, item):
                known.add(item.id)
                new_here += 1
        new_total += new_here
        log(f"  [ok]   {source.name:<28} {len(result.items):>4} items, {new_here:>3} new")
        source_reports.append({"name": source.name, "ok": True, "items": len(result.items), "new": new_here, "error": ""})
    conn.commit()

    # 2. Cross-channel clustering over the recent window.
    cluster_window = int(config.settings["cluster_window_days"])
    recent = db.recent_items(conn, _since_iso(max(cluster_window * 4, 30)))
    mapping = assign_clusters(recent, window_days=cluster_window)
    clustered = 0
    for item in recent:
        cid = mapping.get(item.id, item.id)
        if cid != item.cluster_id:
            db.set_cluster(conn, item.id, cid)
            if cid != item.id:
                clustered += 1
    conn.commit()
    log(f"  clustered {clustered} cross-channel duplicate(s)")

    # 3. Enrich new/unenriched items within the recency window (cost-bounded).
    window_days = int(config.settings["enrich_window_days"])
    cap = int(config.settings["max_enrich_per_feed"])
    todo = db.items_needing_enrichment(conn, _since_iso(window_days), cap)

    enricher = Enricher(model=str(config.settings["llm_model"]))
    if todo:
        mode = "LLM (Anthropic)" if enricher.available else "keyword fallback (no API key)"
        log(f"  enriching {len(todo)} item(s) via {mode}…")
    llm_count = 0
    for item in todo:
        e = enricher.enrich(item)
        db.update_enrichment(
            conn,
            item.id,
            llm_summary=e.summary,
            why_it_matters=e.why_it_matters,
            topics=e.topics,
            asset_class=e.asset_class,
            enriched=e.enriched,
        )
        if e.enriched:
            llm_count += 1
    conn.commit()

    stats = db.counts(conn)
    log("-" * 64)
    log(
        f"  new items: {new_total}  |  LLM-summarized this run: {llm_count}  |  "
        f"store: {stats['total']} items ({stats['enriched']} enriched)"
    )
    log("=" * 64)
    conn.close()

    ended = datetime.now(timezone.utc)
    report = {
        "firm": firm,
        "started_at": started.isoformat(),
        "ended_at": ended.isoformat(),
        "duration_s": round((ended - started).total_seconds(), 1),
        "sources": source_reports,
        "sources_total": len(source_reports),
        "sources_ok": sum(1 for s in source_reports if s["ok"]),
        "sources_failed": sum(1 for s in source_reports if not s["ok"]),
        "new_items": new_total,
        "clustered": clustered,
        "enrich_attempted": len(todo),
        "enrich_llm_ok": llm_count,
        "llm_available": enricher.available,
        "total": stats["total"],
        "enriched_total": stats["enriched"],
    }
    _write_report(firm_root, report)
    return {
        "new": new_total,
        "llm_enriched": llm_count,
        "total": stats["total"],
        "enriched_total": stats["enriched"],
        "llm_available": enricher.available,
    }
