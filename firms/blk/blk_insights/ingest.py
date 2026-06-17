"""Ingestion dispatcher + adapters.

`collect(source, settings, known_ids)` is the single entry point: it routes a
source to the right adapter and always returns a `CollectResult(items, ok, error)`,
isolated so one broken source never takes down the rest (spec §10).

Priority order (spec §1): RSS/Atom first; backend JSON API; HTML scrape last.
BlackRock: "The Bid" podcast via `rss` (Art19-hosted); blackrock.com written
insights via the generic `sitemap_articles` adapter (public sitemaps + og: meta).
"""

from __future__ import annotations

import feedparser
import httpx

from .adapters import CollectResult, fetch_sitemap_articles
from .config import Source
from .normalize import normalize_entries

# Browser-like UA: blackrock.com returns 403 to non-browser agents (Akamai). The
# Bid's feed (rss.art19.com) may 30x-redirect, which httpx then follows.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
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
    "sitemap_articles": fetch_sitemap_articles,
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
