"""Cross-channel clustering (spec §3).

The same outlook can appear on several feeds (e.g. a Global Research recap on
both `At Any Rate` and `Making Sense`). Cluster items by fuzzy title match within
a +/-N-day window and collapse them to one card, keeping links to each form.

Exact-GUID/URL duplicates are already prevented at insert time by the primary
key; this handles *near*-duplicates across channels.
"""

from __future__ import annotations

import re
from datetime import datetime
from difflib import SequenceMatcher

from .models import Item

_TITLE_CLEAN_RE = re.compile(r"[^a-z0-9 ]+")
_PODCAST_PREFIXES = re.compile(
    r"^(research recap|what'?s the deal|the verdict|special episode|ep\.?\s*\d+[:\-]?)\s*",
    re.IGNORECASE,
)
SIMILARITY_THRESHOLD = 0.84


def _norm_title(title: str) -> str:
    t = title.lower()
    t = _PODCAST_PREFIXES.sub("", t)
    t = _TITLE_CLEAN_RE.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip()


def _parse(dt_iso: str | None) -> datetime | None:
    if not dt_iso:
        return None
    try:
        return datetime.fromisoformat(dt_iso)
    except ValueError:
        return None


def _similar(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def assign_clusters(items: list[Item], window_days: int = 3) -> dict[str, str]:
    """Return {item_id: cluster_id}. cluster_id = id of the earliest item in the
    cluster. Singletons map to themselves."""
    # Union-find over items.
    parent: dict[str, str] = {it.id: it.id for it in items}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    enriched = [(it, _norm_title(it.title), _parse(it.published_at)) for it in items]

    for i in range(len(enriched)):
        it_i, title_i, date_i = enriched[i]
        for j in range(i + 1, len(enriched)):
            it_j, title_j, date_j = enriched[j]
            if date_i and date_j and abs((date_i - date_j).days) > window_days:
                continue
            if _similar(title_i, title_j) >= SIMILARITY_THRESHOLD:
                union(it_i.id, it_j.id)

    # Canonical cluster id = earliest-published member (stable, human-meaningful).
    groups: dict[str, list[Item]] = {}
    for it in items:
        groups.setdefault(find(it.id), []).append(it)

    mapping: dict[str, str] = {}
    for members in groups.values():
        members.sort(key=lambda it: (it.published_at or "", it.id))
        canonical = members[0].id
        for it in members:
            mapping[it.id] = canonical
    return mapping
