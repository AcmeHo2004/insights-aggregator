# Deferred work — firms & sources to fix and re-add

State after the 2026-06-17 cleanup: **31 firms live** (JPM, GS, MS + 28 added).
Everything below was pruned because it returned **0 items / was blocked** on the
first run. Each is easy to fix later in a **live network session** (verify the real
feed/sitemap URL in seconds, edit that firm's `sources.yaml`, re-run its scan).

No `tools/` scripts anymore — sources are edited directly in `firms/<slug>/sources.yaml`.

## A. Deferred firms (folder removed — re-add one-at-a-time with corrected sources)
| Firm | Why removed | Re-add hint |
|---|---|---|
| UBS | podcast 403 (self-hosted feed bot-gated); 2 web 0 | find a non-gated feed surface (Apple/Megaphone); do NOT bypass 403 |
| Wells Fargo | WFII web 0, CIB web 0; no podcast wired | find correct sitemap + the WF Investment Institute podcast feed |
| TD Securities | web 0; podcast feed 404 | resolve correct sitemap + "Buyside Views" feed via iTunes lookup |
| T. Rowe Price | web 0 (sitemap is a `<sitemapindex>` — recheck include paths) | fix include paths |
| Invesco | web 403 | find non-gated surface; podcast "The Curious Investor" already lives under AQR |
| Nuveen | web 0 | fix sitemap include paths |
| Ares | web 403 | non-gated surface only |
| Carlyle | web 403 | non-gated surface only |
| Vanguard | sitemap 404 (`corporate.vanguard.com/sitemap.xml`) | find the real sitemap |

## B. Disabled web sources inside LIVE firms (podcast carries the firm; web returned 0)
Re-enable each `# --- DISABLED --- ` block in the firm's `sources.yaml` after
fixing its `params.sitemap_url` / `include` paths:
- **404 sitemap URL:** Oaktree, RBC, Citi (extra), Nomura, Société Générale (wholesale, trailing-slash bug).
- **Sitemap fetched but 0 matches (fix include paths):** Franklin Templeton, SSGA,
  DoubleLine, HSBC (×2), BNP Paribas, Janus Henderson, Neuberger Berman, Brookfield,
  BofA, abrdn, BlackRock (corporate BII).
- **403 web (do NOT bypass):** Brookfield web (podcast OK).

## C. Removed dead podcast feeds (404)
- Schroders — "The Investor Download" + "The Value Perspective" (both podbean 404).
  Schroders web works; find the correct podcast slugs via
  `itunes.apple.com/lookup?id=<id>` → `feedUrl` and re-add as `rss` if wanted.

## Fix loop
1. (live session) verify the real URL with a quick fetch.
2. edit `firms/<slug>/sources.yaml` (fix URL / include paths, or re-add a feed).
3. `cd firms/<slug> && python -m <slug>_insights scan` — confirm >0 items.
4. `python build_static.py`, eyeball, commit. The hourly Action auto-discovers
   `firms/*/`, so no workflow edit is needed.
