"""The normalized Item schema and controlled vocabularies (spec §3, §5)."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional


# Controlled vocabularies — kept short so filtering stays useful (spec §5).
TOPICS = [
    "macro",
    "rates",
    "equities",
    "fixed-income",
    "credit",
    "alternatives",
    "fx",
    "commodities",
    "multi-asset",
    "outlook",
]

ASSET_CLASSES = [
    "equities",
    "fixed-income",
    "credit",
    "rates",
    "fx",
    "commodities",
    "alternatives",
    "cash",
    "multi-asset",
]


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Item:
    """A single normalized insight record (spec §3)."""

    id: str                       # stable hash of canonical_url / guid
    firm: str
    business_unit: str
    source_name: str
    source_type: str              # rss | newsletter | api | scrape
    content_type: str             # article | podcast | video | newsletter | report
    title: str
    url: str                      # link to the original (open in new tab)
    canonical_url: str
    published_at: Optional[str]   # ISO 8601 UTC
    dedup_key: str

    guid: str = ""
    audio_url: str = ""
    authors: list[str] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    asset_class: list[str] = field(default_factory=list)
    raw_summary: str = ""
    llm_summary: str = ""
    why_it_matters: str = ""
    tier: int = 3
    cluster_id: str = ""          # id of the canonical card for cross-channel duplicates

    enriched: bool = False
    is_read: bool = False
    is_starred: bool = False
    ingested_at: str = field(default_factory=now_utc_iso)

    def to_dict(self) -> dict:
        return asdict(self)
