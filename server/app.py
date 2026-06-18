"""Self-hosted companion API (FastAPI + SQLite).

Run:  uvicorn server.app:app --reload --port 8787
Then point the frontend at it:  open the site with ?api=http://localhost:8787

Identity is a simple opaque token the frontend generates and stores; all data is
namespaced by it. No passwords: same token on two devices = synced. This is a
personal/self-hosted trust model — keep your token private and don't expose this
server publicly without putting real auth in front of it.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections import Counter
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

DB = Path(__file__).resolve().parent / "data" / "server.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS state (
    token TEXT PRIMARY KEY, json TEXT NOT NULL, updated_at REAL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token TEXT, ts REAL, event TEXT, props TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_token ON events(token);
"""


def _conn() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c


app = FastAPI(title="Insights Aggregator — companion API")
# Personal tool: allow any origin so the GitHub Pages site or localhost can call it.
# Tighten allow_origins if you ever expose this beyond your own machine.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def _require(token: str | None) -> str:
    if not token:
        raise HTTPException(400, "token required")
    return token


@app.get("/v1/health")
def health() -> dict:
    return {"ok": True}


# ── state sync (read / star / interests / pins / favs) ───────────────────────
class StatePut(BaseModel):
    token: str
    state: dict[str, Any]


@app.get("/v1/state")
def get_state(token: str | None = None) -> dict:
    _require(token)
    with _conn() as c:
        row = c.execute("SELECT json, updated_at FROM state WHERE token=?", (token,)).fetchone()
    if not row:
        return {"state": {}, "updated_at": None}
    return {"state": json.loads(row["json"]), "updated_at": row["updated_at"]}


@app.put("/v1/state")
def put_state(body: StatePut) -> dict:
    _require(body.token)
    with _conn() as c:
        c.execute("INSERT INTO state (token, json, updated_at) VALUES (?,?,?) "
                  "ON CONFLICT(token) DO UPDATE SET json=excluded.json, updated_at=excluded.updated_at",
                  (body.token, json.dumps(body.state), time.time()))
        c.commit()
    return {"ok": True, "updated_at": time.time()}


# ── telemetry capture ────────────────────────────────────────────────────────
class EventsIn(BaseModel):
    token: str | None = None
    events: list[dict[str, Any]]


@app.post("/v1/events")
def post_events(body: EventsIn) -> dict:
    with _conn() as c:
        c.executemany(
            "INSERT INTO events (token, ts, event, props) VALUES (?,?,?,?)",
            [(body.token, e.get("t", time.time()) / (1000 if e.get("t", 0) > 1e12 else 1),
              e.get("event", ""), json.dumps({k: v for k, v in e.items() if k not in ("t", "event")}))
             for e in body.events])
        c.commit()
    return {"ok": True, "n": len(body.events)}


# ── data-driven For You weights (firm/topic affinity from this token's events) ──
@app.get("/v1/weights")
def get_weights(token: str | None = None) -> dict:
    _require(token)
    firm_c: Counter = Counter()
    topic_c: Counter = Counter()
    with _conn() as c:
        rows = c.execute("SELECT event, props FROM events WHERE token=? AND event IN ('open','star')",
                         (token,)).fetchall()
    for r in rows:
        p = json.loads(r["props"] or "{}")
        w = 2 if r["event"] == "star" else 1            # a star counts double an open
        if p.get("firm"):
            firm_c[p["firm"]] += w
        for t in (p.get("topics") or []):
            topic_c[t] += w

    def norm(c: Counter) -> dict:
        m = max(c.values()) if c else 0
        return {k: round(v / m, 3) for k, v in c.items()} if m else {}

    return {"firms": norm(firm_c), "topics": norm(topic_c), "events": len(rows)}
