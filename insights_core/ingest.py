"""Ingestion dispatcher.

`collect(source, settings, known_ids, adapters)` is the single entry point: it
routes a source to the right adapter and always returns a `CollectResult`,
isolated so one broken source never takes down the rest (spec §10).

`rss` is built in (browser-like UA — many corporate feeds 403 non-browser agents
and some 30x-redirect to a host CDN). Non-rss methods dispatch by `source.adapter`
through the `adapters` registry the caller passes in (core built-ins + any
firm-specific adapters).
"""

from __future__ import annotations

import feedparser
import httpx

from .types import Source, CollectResult
from .normalize import normalize_entries

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
TIMEOUT = 30.0


def _fetch_rss(source: Source) -> CollectResult:
    ua = source.params.get("user_agent", USER_AGENT)
    try:
        resp = httpx.get(
            source.url,
            timeout=TIMEOUT,
            follow_redirects=True,  # handle 302 redirects (spec §4)
            headers={"User-Agent": ua},
        )
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 — isolate per-source failure
        return CollectResult(source=source, items=[], ok=False, error=str(exc))

    parsed = feedparser.parse(resp.content)
    items = normalize_entries(parsed.entries, source)
    return CollectResult(source=source, items=items, ok=True)


def collect(source: Source, settings: dict, known_ids: set[str], adapters: dict) -> CollectResult:
    if source.method == "rss":
        return _fetch_rss(source)

    fn = adapters.get(source.adapter)
    if fn is None:
        return CollectResult(
            source=source,
            items=[],
            ok=False,
            error=f"no adapter for method='{source.method}' adapter='{source.adapter}'",
        )
    return fn(source, settings, known_ids)
