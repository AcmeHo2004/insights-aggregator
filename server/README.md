# Companion API (optional, self-hosted)

GitHub Pages can only host the **static frontend**, so the things a static site
genuinely can't do — cross-device sync, telemetry capture, data-driven For You
weights, a real emailed digest — live here, in a small API you run yourself. It's
**entirely optional**: if it isn't running, the site works exactly as before
(localStorage only). Nothing here is part of the Pages deploy.

## Run it

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r server/requirements.txt
uvicorn server.app:app --port 8787        # → http://localhost:8787
```

Data lands in `server/data/server.db` (SQLite, gitignored).

## Point the frontend at it

Open the site with an `?api=` parameter once; it's remembered in localStorage:

```
http://localhost:8000/?api=http://localhost:8787
```

From then on the frontend will, **only when an API is configured**:
- sync `read / star / interests / pins / favs` to `/v1/state` (debounced),
- send telemetry events to `/v1/events`,
- blend per-user firm/topic affinity from `/v1/weights` into the **For You** ranking.

A random `agg.token` is generated and stored in the browser. **To sync a second
device, reuse the same token + api**, e.g.:

```
http://localhost:8000/?api=http://localhost:8787&token=PASTE_YOUR_TOKEN
```

(Your token is in `localStorage.getItem("agg.token")`.) To disconnect, run
`localStorage.removeItem("agg.api")` in the console (or open the site without `?api=`
after clearing it).

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/v1/health` | liveness |
| `GET`  | `/v1/state?token=` | fetch synced state |
| `PUT`  | `/v1/state` | upsert `{token, state}` |
| `POST` | `/v1/events` | ingest `{token?, events:[…]}` telemetry |
| `GET`  | `/v1/weights?token=` | firm/topic affinity (a star = 2× an open), normalized 0–1 |

## Digest (real email vs the static RSS feed)

```bash
python -m server.digest                          # print Markdown
python -m server.digest --tier 1 --n 25          # tier-1 only
SMTP_HOST=smtp.example.com SMTP_TO=you@x.com \
  SMTP_USER=… SMTP_PASS=… python -m server.digest --email
```

Schedule it with cron/launchd. (The static `site/feed.xml` RSS feed remains the
backend-free way to subscribe.)

## Trust model

Identity is an opaque token, namespacing all data — **no passwords**. This is a
personal / self-hosted model. CORS is open so the Pages site or localhost can call
it; **do not expose this server publicly** without putting real auth in front of it.
Keep your token private; treat the telemetry DB as personal data.
