"""Web adapters for DoubleLine written insights.

The corporate insights site renders article lists client-side but publishes a
public **sitemap with <lastmod>**.
So — exactly like the JPM corporate-hub pattern — we read the sitemap for the
full URL list (no headless browser), filter by path, and pull each article's
title/summary from og: metadata. One generic, config-driven adapter
(`sitemap_articles`) serves every such source; per-source `params` in sources.yaml
supply the sitemap URL and the include/exclude path filters.

robots.txt is respected: each source's `params.exclude` lists the disallowed
paths (per each source's robots.txt).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

import httpx

from .config import Source
from .models import Item
from .normalize import canonicalize_url, clean_text, hash_id, parse_iso

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " \
     "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
TIMEOUT = 30.0
FETCH_DELAY = 0.2  # politeness delay between per-article fetches
_sitemap_cache: dict[str, dict[str, str]] = {}  # sitemap url -> {article url: lastmod}


@dataclass
class CollectResult:
    source: Source
    items: list[Item]
    ok: bool
    error: str = ""


_META_RE = re.compile(
    r'<meta[^>]+(?:property|name)=["\']([^"\']+)["\'][^>]+content=["\']([^"\']*)["\']',
    re.I,
)
_META_RE2 = re.compile(
    r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:property|name)=["\']([^"\']+)["\']',
    re.I,
)
_JSONLD_DATE = re.compile(r'"datePublished"\s*:\s*"([^"]+)"')


def _meta(html: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for prop, content in _META_RE.findall(html):
        out.setdefault(prop.lower(), content)
    for content, prop in _META_RE2.findall(html):
        out.setdefault(prop.lower(), content)
    return out


def _load_sitemap(client, sitemap_url: str) -> dict[str, str]:
    """Parse a sitemap urlset into {url: lastmod}. Cached per process."""
    if sitemap_url in _sitemap_cache:
        return _sitemap_cache[sitemap_url]
    resp = client.get(sitemap_url)
    resp.raise_for_status()
    xml = resp.text
    mapping: dict[str, str] = {}
    for block in re.findall(r"<url>.*?</url>", xml, re.S):
        loc = re.search(r"<loc>([^<]+)</loc>", block)
        if not loc:
            continue
        lm = re.search(r"<lastmod>([^<]+)</lastmod>", block)
        mapping[loc.group(1).strip()] = lm.group(1).strip() if lm else ""
    _sitemap_cache[sitemap_url] = mapping
    return mapping


def _filtered_urls(sitemap: dict[str, str], include: list[str], exclude: list[str]) -> list[str]:
    rows = []
    for url, lastmod in sitemap.items():
        if include and not any(inc in url for inc in include):
            continue
        if any(exc in url for exc in exclude):
            continue
        if "-" not in url.rstrip("/").split("/")[-1]:  # skip section/landing roots
            continue
        rows.append((lastmod, url))
    rows.sort(reverse=True)  # newest lastmod first
    return [url for _, url in rows]


def fetch_sitemap_articles(source: Source, settings: dict, known_ids: set[str]) -> CollectResult:
    p = source.params
    sitemap_url = p.get("sitemap_url")
    if not sitemap_url:
        return CollectResult(source, [], False, "params.sitemap_url missing")
    include = p.get("include", [])
    exclude = p.get("exclude", [])
    max_new = int(settings.get("web_max_new_per_run", 150))

    try:
        with httpx.Client(timeout=TIMEOUT, headers={"User-Agent": UA}, follow_redirects=True) as client:
            sitemap = _load_sitemap(client, sitemap_url)
            urls = _filtered_urls(sitemap, include, exclude)

            items: list[Item] = []
            for url in urls:
                canonical = canonicalize_url(url)
                item_id = hash_id(canonical)
                if item_id in known_ids:
                    continue
                if len(items) >= max_new:
                    break
                item = _fetch_article(client, url, canonical, item_id, source, sitemap.get(url))
                if item:
                    items.append(item)
                time.sleep(FETCH_DELAY)
    except Exception as exc:  # noqa: BLE001
        return CollectResult(source, [], False, str(exc))

    return CollectResult(source, items, True)


def _extract_date(html: str, meta: dict[str, str], lastmod: str | None) -> str | None:
    for key in ("article:published_time", "article:modified_time", "og:updated_time"):
        if meta.get(key) and parse_iso(meta[key]):
            return parse_iso(meta[key])
    m = _JSONLD_DATE.search(html)
    if m and parse_iso(m.group(1)):
        return parse_iso(m.group(1))
    return parse_iso(lastmod)


def _fetch_article(client, url, canonical, item_id, source: Source, lastmod=None) -> Item | None:
    try:
        resp = client.get(url)
        resp.raise_for_status()
    except Exception:  # noqa: BLE001 — skip one bad article, keep the rest
        return None
    html = resp.text
    meta = _meta(html)

    title = clean_text(meta.get("og:title") or "", 300)
    if not title:
        return None
    title = re.split(r"\s+[|I]\s+DoubleLine", title)[0].strip() or title

    return Item(
        id=item_id,
        firm="DoubleLine",
        business_unit=source.business_unit,
        source_name=source.name,
        source_type=source.method,
        content_type="article",
        title=title,
        url=url,
        canonical_url=canonical,
        published_at=_extract_date(html, meta, lastmod),
        dedup_key=canonical,
        raw_summary=clean_text(meta.get("og:description") or ""),
        tier=source.tier,
    )
