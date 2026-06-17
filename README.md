# Insights Aggregator

A self-updating, NewsNow-style dashboard of investment insights from **JPMorgan,
Goldman Sachs, Morgan Stanley, and BlackRock** (podcasts + written articles), each
with a 2–3 sentence Claude summary and a "why it matters" line.

**Live site:** _(GitHub Pages — see repo Settings → Pages)_

## How it works

- `firms/{jpm,gs,ms,blk}/` — independent scanners (RSS feeds + public sitemaps →
  normalize → dedup → enrich with Claude Sonnet → SQLite). Add a firm by adding a
  folder here.
- `build_static.py` — exports every firm DB into `site/` (`data.json` + the
  front-end). No backend: the page filters/groups entirely client-side.
- `web/` — the front-end (group by firm / business line / type / stream;
  multi-filters; per-column infinite scroll; pin & favorite columns; "N new"
  badges; keyboard triage `j/k/o/r/s//`; copy-starred Markdown digest; light/dark).
  Read/star state is per-viewer (localStorage).
- `.github/workflows/update.yml` — **hourly**: restores the DBs, scans for new
  content, rebuilds `site/`, deploys to GitHub Pages, and persists the DBs back
  to a single-commit `data` branch. No always-on machine needed.

## Setup (already done for this repo)

1. `ANTHROPIC_API_KEY` stored as an Actions secret (used only by the workflow).
2. Pages source = GitHub Actions.
3. The `data` branch holds the current SQLite DBs (so the hourly run only enriches
   *new* items).

## Run locally

```bash
python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
# (put each firm's insights.db under firms/<firm>/data/ — or run the scanners)
python build_static.py
python -m http.server -d site 8000      # → http://127.0.0.1:8000
```

Change cadence in `.github/workflows/update.yml` (the `cron:` line).
