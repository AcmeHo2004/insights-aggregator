"""Normalize raw feed entries into the common Item schema (spec §3)."""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from html import unescape
from time import struct_time
from typing import Optional
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from .config import Source
from .models import Item

# Tracking params to strip when canonicalizing a URL (spec §3 dedup).
_STRIP_PREFIXES = ("utm_",)
_STRIP_EXACT = {"fbclid", "gclid", "mc_cid", "mc_eid", "ref", "source"}

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def canonicalize_url(url: str) -> str:
    if not url:
        return ""
    parts = urlsplit(url.strip())
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if not k.lower().startswith(_STRIP_PREFIXES) and k.lower() not in _STRIP_EXACT
    ]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def clean_text(raw: str, max_chars: int = 1200) -> str:
    if not raw:
        return ""
    text = unescape(_TAG_RE.sub(" ", raw))
    text = _WS_RE.sub(" ", text).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rsplit(" ", 1)[0] + "…"
    return text


def _to_utc_iso(parsed: Optional[struct_time]) -> Optional[str]:
    if not parsed:
        return None
    try:
        dt = datetime(*parsed[:6], tzinfo=timezone.utc)
        return dt.isoformat()
    except (ValueError, TypeError):
        return None


def hash_id(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def parse_iso(value: str | None) -> Optional[str]:
    """Normalize an ISO-8601-ish string (e.g. Solr's 2026-06-15T08:10:13.578Z)
    to a UTC ISO string. Returns None if unparseable."""
    if not value:
        return None
    s = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except ValueError:
        return None


def _extract_audio(entry) -> str:
    for enc in entry.get("enclosures", []) or []:
        href = enc.get("href") or enc.get("url")
        if href and (enc.get("type", "").startswith("audio") or href.endswith((".mp3", ".m4a"))):
            return href
    for link in entry.get("links", []) or []:
        if link.get("rel") == "enclosure" and link.get("type", "").startswith("audio"):
            return link.get("href", "")
    return ""


def _extract_authors(entry) -> list[str]:
    authors = []
    for a in entry.get("authors", []) or []:
        name = (a.get("name") or "").strip()
        if name:
            authors.append(name)
    if not authors and entry.get("author"):
        authors.append(entry["author"].strip())
    # de-dupe, keep order
    seen, out = set(), []
    for a in authors:
        if a.lower() not in seen:
            seen.add(a.lower())
            out.append(a)
    return out


def _raw_summary(entry) -> str:
    # Prefer the richest available description / show notes.
    for key in ("summary", "subtitle", "description"):
        if entry.get(key):
            return clean_text(entry[key])
    content = entry.get("content") or []
    if content and content[0].get("value"):
        return clean_text(content[0]["value"])
    return ""


def normalize_entry(entry, source: Source) -> Item:
    link = (entry.get("link") or "").strip()
    canonical = canonicalize_url(link)

    guid = (entry.get("id") or entry.get("guid") or "").strip()
    # Dedup key: feed GUID when present, else canonical URL (spec §3).
    dedup_key = guid or canonical
    # Stable primary key.
    item_id = hash_id(guid or canonical or (source.name + (entry.get("title") or "")))

    return Item(
        id=item_id,
        firm=source.firm,
        business_unit=source.business_unit,
        source_name=source.name,
        source_type=source.method,
        content_type=source.content_type,
        title=clean_text(entry.get("title", ""), max_chars=300) or "(untitled)",
        url=link or canonical,
        canonical_url=canonical,
        published_at=_to_utc_iso(entry.get("published_parsed") or entry.get("updated_parsed")),
        dedup_key=dedup_key,
        guid=guid,
        audio_url=_extract_audio(entry),
        authors=_extract_authors(entry),
        raw_summary=_raw_summary(entry),
        tier=source.tier,
    )


def normalize_entries(entries, source: Source) -> list[Item]:
    return [normalize_entry(e, source) for e in entries]
