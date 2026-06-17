"""Orchestrate the scan: ingest -> normalize -> dedup -> enrich -> store.

A single call to `run_scan()` is the whole Phase-1 pipeline (spec §8:
"Done = real cards showing up daily from a single command").
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

from . import db, ingest
from .config import Config, load_config
from .dedup import assign_clusters
from .enrich import Enricher

# Rough per-item token averages for cost estimation (title + show-notes in,
# short structured JSON out).
_EST_IN_TOKENS = 450
_EST_OUT_TOKENS = 170
# Sonnet 4.6 pricing per 1M tokens ($ in / $ out). Used only for the estimate.
_PRICE = {"claude-sonnet-4-6": (3.0, 15.0), "claude-haiku-4-5": (1.0, 5.0),
          "claude-opus-4-8": (5.0, 25.0)}


def _est_cost(model: str, n: int) -> float:
    pin, pout = _PRICE.get(model, (3.0, 15.0))
    return n * (_EST_IN_TOKENS * pin + _EST_OUT_TOKENS * pout) / 1_000_000


def _since_iso(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def run_backfill(
    *,
    workers: int = 6,
    max_tier: int | None = None,
    days: int | None = None,
    limit: int | None = None,
    estimate_only: bool = False,
    config: Config | None = None,
) -> dict:
    """Enrich the back-catalogue: every still-unenriched item (no per-source cap),
    concurrently. Optional filters narrow scope/cost. Network calls run in a
    thread pool; SQLite writes happen on the main thread."""
    config = config or load_config()
    conn = db.connect()
    db.init_db(conn)
    model = str(config.settings["llm_model"])

    since = _since_iso(days) if days else None
    items = db.unenriched_items(conn, since_iso=since, max_tier=max_tier, limit=limit)
    n = len(items)
    est = _est_cost(model, n)

    print(f"  backfill scope: {n} unenriched item(s)"
          + (f", tier<={max_tier}" if max_tier else "")
          + (f", last {days}d" if days else ""))
    print(f"  model: {model}  ·  est. cost ≈ ${est:.2f} (rough)")
    if estimate_only:
        conn.close()
        return {"count": n, "est_cost": est, "enriched": 0}

    enricher = Enricher(model=model)
    if not enricher.available:
        print("  ERROR: ANTHROPIC_API_KEY not available — cannot backfill.")
        conn.close()
        return {"count": n, "est_cost": est, "enriched": 0, "error": "no_api_key"}

    done = ok = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(enricher.enrich, it): it for it in items}
        for fut in as_completed(futures):
            item = futures[fut]
            try:
                e = fut.result()
            except Exception:  # noqa: BLE001 — skip a failed item, keep going
                done += 1
                continue
            db.update_enrichment(
                conn, item.id,
                llm_summary=e.summary, why_it_matters=e.why_it_matters,
                topics=e.topics, asset_class=e.asset_class, enriched=e.enriched,
            )
            done += 1
            ok += 1 if e.enriched else 0
            if done % 50 == 0:
                conn.commit()
                print(f"    … {done}/{n}  ({ok} LLM-summarized)")
    conn.commit()
    stats = db.counts(conn)
    conn.close()
    print(f"  done: {ok}/{n} LLM-summarized  |  store now {stats['enriched']}/{stats['total']} enriched")
    return {"count": n, "est_cost": est, "enriched": ok}


def run_scan(config: Config | None = None, verbose: bool = True) -> dict:
    config = config or load_config()
    conn = db.connect()
    db.init_db(conn)

    def log(msg: str) -> None:
        if verbose:
            print(msg)

    log("=" * 64)
    log(f"Capital Group Insights scan — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    log("=" * 64)

    # 1. Ingest + normalize + store new items (new-content detection: spec §5).
    known = db.existing_ids(conn)
    new_total = 0
    for source in config.sources:
        result = ingest.collect(source, config.settings, known)
        if not result.ok:
            log(f"  [skip] {source.name}: {result.error}")
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
    return {
        "new": new_total,
        "llm_enriched": llm_count,
        "total": stats["total"],
        "enriched_total": stats["enriched"],
        "llm_available": enricher.available,
    }
