"""Load a firm's sources.yaml + .env, resolved relative to its firm_root.

`firm_root` is `firms/<slug>/` — it holds `sources.yaml`, `.env`, and `data/`.
A firm package passes its own root in; nothing here is firm-specific.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from .types import Config, Source

# Defaults; a firm's sources.yaml `settings:` block overrides per key.
DEFAULT_SETTINGS = {
    # Enrichment coverage: the hourly scan LLM-summarizes new items within this
    # window, capped per source. Backfill (Batches) covers the rest of the
    # back-catalogue. Raised from the original 60d/8 to widen steady-state coverage.
    "enrich_window_days": 120,
    "max_enrich_per_feed": 20,
    "llm_model": "claude-haiku-4-5",
    "cluster_window_days": 3,
    # Web adapters.
    "web_max_new_per_run": 150,   # generic sitemap adapter: max NEW pages/run/source
    "am_max_items": 400,          # JPM AM Solr cap
    "cib_max_new_per_run": 200,   # JPM CIB cap
}


def data_dir(firm_root: Path) -> Path:
    return Path(firm_root) / "data"


def db_path(firm_root: Path) -> Path:
    return data_dir(firm_root) / "insights.db"


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (no dependency). Only sets keys not already in env."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def load_config(firm_root: Path, default_firm: str = "") -> Config:
    firm_root = Path(firm_root)
    _load_dotenv(firm_root / ".env")
    raw = yaml.safe_load((firm_root / "sources.yaml").read_text(encoding="utf-8")) or {}

    settings = {**DEFAULT_SETTINGS, **(raw.get("settings") or {})}

    sources: list[Source] = []
    for entry in raw.get("sources") or []:
        sources.append(
            Source(
                name=entry["name"],
                firm=entry.get("firm", default_firm),
                business_unit=entry.get("business_unit", ""),
                method=entry.get("method", "rss"),
                content_type=entry.get("content_type", "podcast"),
                url=entry["url"],
                tier=int(entry.get("tier", 3)),
                notes=entry.get("notes", ""),
                adapter=entry.get("adapter", ""),
                params=entry.get("params", {}) or {},
            )
        )
    return Config(settings=settings, sources=sources)
