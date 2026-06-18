"""Shared dataclasses used across config / ingest / adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .models import Item


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


@dataclass
class CollectResult:
    source: Source
    items: list[Item]
    ok: bool
    error: str = ""
