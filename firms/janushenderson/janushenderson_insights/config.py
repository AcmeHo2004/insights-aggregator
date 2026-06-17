"""Load sources.yaml and resolve paths / env."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
SOURCES_PATH = ROOT / "sources.yaml"
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "insights.db"


DEFAULT_SETTINGS = {
    "enrich_window_days": 60,
    "max_enrich_per_feed": 8,
    "llm_model": "claude-haiku-4-5",
    "cluster_window_days": 3,
}


@dataclass
class Source:
    name: str
    firm: str
    business_unit: str
    method: str          # rss | newsletter | api | scrape
    content_type: str
    url: str
    tier: int = 3
    notes: str = ""
    adapter: str = ""    # specific adapter for api/scrape (e.g. sitemap_articles)
    params: dict = field(default_factory=dict)  # adapter-specific config


@dataclass
class Config:
    settings: dict[str, Any]
    sources: list[Source]


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


def load_config(sources_path: Path = SOURCES_PATH) -> Config:
    _load_dotenv(ROOT / ".env")
    raw = yaml.safe_load(sources_path.read_text(encoding="utf-8")) or {}

    settings = {**DEFAULT_SETTINGS, **(raw.get("settings") or {})}

    sources: list[Source] = []
    for entry in raw.get("sources") or []:
        sources.append(
            Source(
                name=entry["name"],
                firm=entry.get("firm", "Janus Henderson"),
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
