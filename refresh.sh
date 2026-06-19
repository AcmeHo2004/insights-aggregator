#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# refresh.sh — local (residential-IP) refresh of the web-scraped sources that
# GitHub's datacenter runners can't reach (Akamai/bot-gating), then SAFELY seed
# the `data` branch so the live site gets the latest. Run manually (./refresh.sh)
# or on a schedule via launchd. Safe to re-run; never overwrites the cloud with
# a stale or partial snapshot (see the guard in step 4).
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

REPO="/Users/acmeho/insights-aggregator"
cd "$REPO" || { echo "repo not found: $REPO"; exit 1; }
log(){ printf '%s  %s\n' "$(date -u +%FT%TZ)" "$*"; }

# secret + interpreter
[ -f .env ] && { set -a; . ./.env; set +a; }
PY="$REPO/.venv/bin/python"; [ -x "$PY" ] || PY="python3"
log "refresh start (py=$PY)"

# ── 1) Re-sync local FROM the data branch first ──────────────────────────────
#     The data branch is the source of truth; the local tree drifts stale. Sync
#     before scanning so we only ever ADD to the cloud's latest, never regress it.
log "sync: fetching + restoring DBs from data branch"
git fetch -q origin data --depth=1 || { log "FATAL: cannot fetch data branch"; exit 1; }
git ls-tree -r --name-only origin/data | grep 'insights.db$' | while IFS= read -r p; do
  git checkout -q origin/data -- "$p" 2>/dev/null
done
git reset -q
log "sync: local now matches the cloud"

# ── 2) Scan every firm from this residential IP ──────────────────────────────
#     Web sources (sitemap scrape) only succeed from a residential IP. Scans are
#     additive + de-duplicated, so re-running is harmless. One bad firm never
#     stops the rest.
log "scan: per-firm scans (residential IP)"
for d in firms/*/; do
  f=$(basename "$d")
  if ( cd "$d" && "$PY" -m "${f}_insights" scan ) >/dev/null 2>&1; then
    log "  scan ok:   $f"
  else
    log "  scan WARN: $f (continuing)"
  fi
done

# ── 3) Rebuild the static site locally ───────────────────────────────────────
log "build: build_static.py"
"$PY" build_static.py || { log "FATAL: build failed — not touching data branch"; exit 1; }

# ── 4) SAFETY GUARD: never push a partial/empty snapshot over the data branch ─
read -r NDB TOTAL < <("$PY" - <<'PY'
import sqlite3, glob
n=tot=0
for db in glob.glob("firms/*/data/insights.db"):
    n+=1
    try: tot+=sqlite3.connect(f"file:{db}?mode=ro",uri=True).execute("SELECT count(*) FROM items").fetchone()[0]
    except Exception: pass
print(n, tot)
PY
)
log "guard: $NDB DBs / $TOTAL items"
if [ "${NDB:-0}" -lt 30 ] || [ "${TOTAL:-0}" -lt 14000 ]; then
  log "GUARD TRIPPED ($NDB DBs / $TOTAL items below threshold) — REFUSING to push data branch"
  exit 1
fi

# ── 5) SAFE push to the data branch ──────────────────────────────────────────
#     Build a single-commit orphan tree of just the DBs using a THROWAWAY index
#     (GIT_INDEX_FILE) — no checkout, no working-tree mutation. This is the
#     "push directly" method; never the orphan-checkout dance that once wiped DBs.
log "push: seeding data branch (safe orphan commit)"
URL=$(git remote get-url origin)
export GIT_INDEX_FILE; GIT_INDEX_FILE="$(mktemp -u /tmp/refresh-idx.XXXXXX)"
git read-tree --empty
git add -f firms/*/data/insights.db
TREE=$(git write-tree)
COMMIT=$(git -c user.name=insights-bot -c user.email=insights-bot@users.noreply.github.com \
          commit-tree "$TREE" -m "local web refresh $(date -u +%FT%TZ)")
if git push -f "$URL" "$COMMIT:data"; then log "push: data branch updated"; else log "push FAILED"; fi
rm -f "$GIT_INDEX_FILE"; unset GIT_INDEX_FILE

# keep the working tree tidy (blk is the one tracked seed DB)
git checkout -q HEAD -- firms/blk/data/insights.db 2>/dev/null || true

# ── 6) Nudge the cloud to rebuild + deploy from the fresh data branch ─────────
if command -v gh >/dev/null 2>&1; then
  gh workflow run update.yml >/dev/null 2>&1 \
    && log "deploy: triggered update.yml" \
    || log "deploy: gh trigger skipped (will deploy on next hourly run)"
else
  log "deploy: gh not found (will deploy on next hourly run)"
fi
log "refresh DONE."
