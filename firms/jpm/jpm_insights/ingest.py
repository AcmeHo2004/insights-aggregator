"""Ingestion dispatcher + adapters.

`collect(source, settings, known_ids)` is the single entry point: it routes a
source to the right adapter and always returns a `CollectResult(items, ok, error)`,
isolated so one broken source never takes down the rest (spec §10).

Priority order (spec §1): RSS/Atom first; backend JSON API; HTML scrape last.
Phase 1 used RSS only; AM (`api` → Solr) and CIB (`scrape` → corporate hub) are
added here.
"""

from __future__ import annotations

import feedparser
import httpx

from .adapters import CollectResult, fetch_am_solr, fetch_cib_hub
from .config import Source
from .normalize import normalize_entries

USER_AGENT = "JPM-Insights-Aggregator/0.1 (personal research tool)"
TIMEOUT = 30.0


def _fetch_rss(source: Source) -> CollectResult:
    try:
        resp = httpx.get(
            source.url,
            timeout=TIMEOUT,
            follow_redirects=True,  # handle 302 redirects (spec §4)
            headers={"User-Agent": USER_AGENT},
        )
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 — isolate per-source failure
        return CollectResult(source=source, items=[], ok=False, error=str(exc))

    parsed = feedparser.parse(resp.content)
    items = normalize_entries(parsed.entries, source)
    return CollectResult(source=source, items=items, ok=True)


# adapter name → callable. `method: rss` maps to the RSS adapter implicitly.
_ADAPTERS = {
    "am_solr": fetch_am_solr,
    "jpm_corp_hub": fetch_cib_hub,
}


def collect(source: Source, settings: dict, known_ids: set[str]) -> CollectResult:
    if source.method == "rss":
        return _fetch_rss(source)

    fn = _ADAPTERS.get(source.adapter)
    if fn is None:
        return CollectResult(
            source=source,
            items=[],
            ok=False,
            error=f"no adapter for method='{source.method}' adapter='{source.adapter}'",
        )
    return fn(source, settings, known_ids)
