"""JPM-specific web adapters (bespoke — not the generic sitemap adapter).

AM written insights  → `am_solr`: JPMorgan AM exposes a *public, unauthenticated*
  Solr index at `am.jpmorgan.com/cs/search/am/select`. We query US/English
  insights pages and **filter to `contentAccess_s:unlocked`** so only publicly
  accessible content is ingested (spec §10 — never bypass gating).

CIB / corporate-hub → `jpm_corp_hub`: featured articles are present in the static
  HTML of the hub pages (no JS needed). We extract article links for the two
  high-signal categories and read each page's og: metadata for title/summary.

Shared helpers (Item, CollectResult, normalize utils) come from `insights_core`.
`ADAPTERS` is merged into the registry by this package's `__main__`.
"""

from __future__ import annotations

import re
import time

import httpx

from insights_core.types import Source, CollectResult
from insights_core.models import Item
from insights_core.normalize import canonicalize_url, clean_text, hash_id, parse_iso

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 " \
     "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
TIMEOUT = 30.0

# CIB full article list comes from the public sitemap (more robust than chasing
# the JS "Load More" endpoint, which the corporate AEM site exposes no clean API
# for). The sitemap also carries <lastmod>, giving each article a usable date.
CIB_SITEMAP_URL = "https://www.jpmorgan.com/US/en/sitemap.xml"
CIB_FETCH_DELAY = 0.2  # politeness delay between per-article fetches (seconds)
_sitemap_cache: dict[str, dict[str, str]] = {}  # sitemap url -> {article url: lastmod}


# ─────────────────────────────────────────────────────────────────────────────
# AM — public Solr index
# ─────────────────────────────────────────────────────────────────────────────

# Map an insights path section to (source_name, tier) per spec §4a tiering.
_AM_SECTIONS = {
    "market-insights": ("AM · Market Insights", 1),
    "portfolio-insights": ("AM · Portfolio Insights", 1),
    "etf-insights": ("AM · ETF Insights", 2),
    "retirement-insights": ("AM · Retirement Insights", 3),
    "market-response-center": ("AM · Market Response Center", 1),
}


def _am_section(url_path: str) -> tuple[str, int]:
    for key, (name, tier) in _AM_SECTIONS.items():
        if f"/insights/{key}" in url_path:
            return name, tier
    return "AM · Insights", 2


def fetch_am_solr(source: Source, settings: dict, known_ids: set[str]) -> CollectResult:
    base = source.url  # .../cs/search/am/select
    rows = 100
    max_items = int(settings.get("am_max_items", 400))
    fields = "id,url,uri,title_en,pageTitle_en,description_en,sortDate,jcr_created_dt,format"

    items: list[Item] = []
    seen: set[str] = set()
    try:
        with httpx.Client(timeout=TIMEOUT, headers={"User-Agent": UA}, follow_redirects=True) as client:
            start = 0
            while len(items) < max_items:
                params = [
                    ("q", "*:*"),
                    ("fq", "site:us_en_adv"),
                    ("fq", "contentAccess_s:unlocked"),
                    ("fq", "format:webpage"),
                    ("fq", "id:*/insights/*"),
                    ("sort", "sortDate desc"),
                    ("fl", fields),
                    ("rows", str(rows)),
                    ("start", str(start)),
                    ("wt", "json"),
                ]
                resp = client.get(base, params=params)
                resp.raise_for_status()
                docs = resp.json().get("response", {}).get("docs", [])
                if not docs:
                    break
                for doc in docs:
                    item = _am_doc_to_item(doc, source)
                    if item and item.id not in seen:
                        seen.add(item.id)
                        items.append(item)
                start += rows
                if start >= 1000:  # safety bound
                    break
    except Exception as exc:  # noqa: BLE001
        return CollectResult(source=source, items=[], ok=False, error=str(exc))

    return CollectResult(source=source, items=items, ok=True)


def _am_doc_to_item(doc: dict, source: Source) -> Item | None:
    url = (doc.get("url") or "").strip()
    if not url:
        uri = (doc.get("uri") or doc.get("id") or "").strip()
        url = f"https://am.jpmorgan.com{uri}" if uri.startswith("/") else uri
    if not url:
        return None

    title = clean_text(doc.get("title_en") or doc.get("pageTitle_en") or "", 300)
    if not title:
        return None
    # Skip section-root landing pages (not content items, spec §4a).
    path = url.split("am.jpmorgan.com", 1)[-1]
    if re.search(r"/insights/[a-z-]+/?$", path):
        return None

    canonical = canonicalize_url(url)
    source_name, tier = _am_section(path)
    return Item(
        id=hash_id(canonical),
        firm=source.firm,
        business_unit="Asset Management",
        source_name=source_name,
        source_type="api",
        content_type="article",
        title=title,
        url=url,
        canonical_url=canonical,
        published_at=parse_iso(doc.get("sortDate") or doc.get("jcr_created_dt")),
        dedup_key=canonical,
        raw_summary=clean_text(doc.get("description_en") or ""),
        tier=tier,
    )


# ─────────────────────────────────────────────────────────────────────────────
# CIB / corporate hub — static-HTML featured set + per-article og: metadata
# ─────────────────────────────────────────────────────────────────────────────

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


def fetch_cib_hub(source: Source, settings: dict, known_ids: set[str]) -> CollectResult:
    category = source.url.rstrip("/").split("/")[-1]  # markets-and-economy | global-research
    max_new = int(settings.get("cib_max_new_per_run", 200))

    try:
        with httpx.Client(timeout=TIMEOUT, headers={"User-Agent": UA}, follow_redirects=True) as client:
            sitemap = _load_sitemap(client, CIB_SITEMAP_URL)
            urls = _category_article_urls(sitemap, category)

            items: list[Item] = []
            for url in urls:
                canonical = canonicalize_url(url)
                item_id = hash_id(canonical)
                if item_id in known_ids:
                    continue  # already stored — don't re-fetch the page
                if len(items) >= max_new:
                    break
                item = _fetch_cib_article(
                    client, url, canonical, item_id, source, lastmod=sitemap.get(url)
                )
                if item:
                    items.append(item)
                time.sleep(CIB_FETCH_DELAY)
    except Exception as exc:  # noqa: BLE001
        return CollectResult(source=source, items=[], ok=False, error=str(exc))

    return CollectResult(source=source, items=items, ok=True)


def _load_sitemap(client, sitemap_url: str) -> dict[str, str]:
    """Parse the corporate sitemap into {article_url: lastmod}. Cached per process
    so both CIB sources in one scan share a single fetch."""
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


def _category_article_urls(sitemap: dict[str, str], category: str) -> list[str]:
    """Article URLs for one /insights/<category>/ section, newest lastmod first."""
    prefix = f"/insights/{category}/"
    rows = []
    for url, lastmod in sitemap.items():
        if prefix not in url:
            continue
        if "-" not in url.rstrip("/").split("/")[-1]:  # skip section/subcategory roots
            continue
        rows.append((lastmod, url))
    rows.sort(reverse=True)  # newest lastmod first
    return [url for _, url in rows]


def _extract_date(html: str, meta: dict[str, str], lastmod: str | None) -> str | None:
    for key in ("article:published_time", "article:modified_time", "og:updated_time"):
        if meta.get(key):
            d = parse_iso(meta[key])
            if d:
                return d
    m = _JSONLD_DATE.search(html)
    if m and parse_iso(m.group(1)):
        return parse_iso(m.group(1))
    return parse_iso(lastmod)  # sitemap <lastmod> as the fallback date


def _fetch_cib_article(client, url, canonical, item_id, source: Source, lastmod=None) -> Item | None:
    try:
        resp = client.get(url)
        resp.raise_for_status()
    except Exception:  # noqa: BLE001 — skip a single bad article, keep the rest
        return None
    html = resp.text
    meta = _meta(html)

    title = clean_text(meta.get("og:title") or "", 300)
    if not title:
        _t = re.search(r"<title[^>]*>([^<]+)</title>", html, re.I)
        title = clean_text(_t.group(1) if _t else "", 300)
    if not title:
        return None
    # Strip the trailing " | J.P. Morgan ..." house suffix.
    title = re.split(r"\s+[|I]\s+J\.?P\.?\s*Morgan", title)[0].strip() or title

    return Item(
        id=item_id,
        firm=source.firm,
        business_unit=source.business_unit,
        source_name=source.name,
        source_type="scrape",
        content_type="article",
        title=title,
        url=url,
        canonical_url=canonical,
        published_at=_extract_date(html, meta, lastmod),
        dedup_key=canonical,
        raw_summary=clean_text(meta.get("og:description") or ""),
        tier=source.tier,
    )


# Registry merged into core's built-ins by jpm_insights/__main__.py.
ADAPTERS = {
    "am_solr": fetch_am_solr,
    "jpm_corp_hub": fetch_cib_hub,
}
