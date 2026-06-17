# Next steps — live verification & fixes (handoff)

First real Action run (PR #1, run 27693891315) succeeded and ingested **~10k items
across 31/40 firms**. This file lists what still needs fixing. Most fixes need a
**live network session** (set the env Network access to **Full** and start a fresh
session) so feeds/sitemaps can be verified in seconds.

Source of truth for all sources: `tools/recon2.json` (regenerate every
`firms/<slug>/sources.yaml` with `python tools/finalize2.py`). Per-firm unresolved
podcasts are also recorded as `# TODO` comments at the top of each `sources.yaml`.

## Already done
- **Sitemap-index recursion + gzip** added to the shared `sitemap_articles` adapter
  (all 39 sitemap-using packages). Unit-tested offline. This alone should unlock
  firms whose `/sitemap.xml` is a `<sitemapindex>` (Jefferies, Nuveen, T. Rowe …) —
  re-run the Action to confirm before hand-fixing those.

## To fix (live session)

### A. Wrong podcast feed URLs (returned 404 — find correct slug via iTunes lookup `itunes.apple.com/lookup?id=<id>` → feedUrl)
- Schroders — "The Investor Download" (`feed.podbean.com/schroders/feed.xml` 404) and
  "The Value Perspective" (`feed.podbean.com/schroderstvp/feed.xml` 404).
- TD Securities — "Buyside Views" (`feed.podbean.com/xiabg-28b57a/feed.xml` 404).
- Deutsche Bank — "PERSPECTIVES Weekly" (`feed.podbean.com/3g9r4-1af4bb/feed.xml` 404).

### B. Wrong web sitemap URL (404 — find the real sitemap, often /sitemap_index.xml or robots.txt-declared)
- Oaktree (`oaktreecapital.com/sitemap.xml` 404) — podcast OK (77).
- RBC (`rbccm.com/sitemap.xml` 404) — podcasts OK.
- Citi (`citigroup.com/sitemap.xml` 404).
- Nomura (`nomuraconnects.com/sitemap.xml` 404) — podcast OK (100).
- Société Générale wholesale (`…/sitemap.xml/` 404, note trailing slash) — other SG source OK (209).
- Vanguard (`corporate.vanguard.com/sitemap.xml` 404).

### C. Bot-gated 403 — DO NOT bypass; leave best-effort + documented, or find a non-gated surface
- UBS (all 3 sources 403, incl. the self-hosted podcast feed) → 0 items.
- Brookfield, Ares, Carlyle, Invesco (web 403). Brookfield podcast OK (60).

### D. Sitemap fetched but 0 article matches (re-check after the index-recursion change; else fix include paths)
- Jefferies, Nuveen, T. Rowe Price, Schroders (web).

### E. Transient
- Neuberger Berman web `429` (still got 151 from podcast) — harmless; retry.

## Also: resolve the 20 Apple-ID-only podcasts
Search each `# TODO` block in `firms/*/sources.yaml` (Vanguard, Citi ×3, Jefferies,
TD ×4, T. Rowe ×2, Invesco ×2, Barclays ×2, abrdn, Carlyle ×2, Janus Henderson,
Deutsche Bank). Resolve `itunes.apple.com/lookup?id=<id>` → `feedUrl`, then add as
`rss` sources and re-run `tools/finalize2.py`.

## Loop
After edits: `python build_static.py` locally, then re-trigger the Action
(`update.yml` on this branch) and re-read the scan logs for per-source counts.
