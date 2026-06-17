"""LLM enrichment: 2-3 sentence summary + why-it-matters + topics (spec §5).

Uses the Anthropic API with a cheap, fast model (Claude Haiku) and structured
JSON output. Copyright-safe: we send only title + feed show-notes and store the
*generated* summary, never large verbatim excerpts (spec §5, §10).

Degrades gracefully: with no ANTHROPIC_API_KEY the pipeline still runs — items
keep the feed's show-notes as their displayed summary and get keyword-based
topic tags, so the dashboard is populated. Re-running `scan` once a key is set
backfills real LLM summaries.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from .models import Item, TOPICS, ASSET_CLASSES

SYSTEM_PROMPT = (
    "You are summarizing a financial-markets insight for a personal triage "
    "dashboard. You are given the title and the source's own short description "
    "(podcast show notes). Produce a neutral, factual summary. Do NOT quote more "
    "than a few words verbatim from the source."
)

# JSON schema for structured output (spec §5 output contract).
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "2-3 sentence neutral summary."},
        "why_it_matters": {"type": "string", "description": "One line on why it matters."},
        "topics": {"type": "array", "items": {"type": "string", "enum": TOPICS}},
        "asset_class": {"type": "array", "items": {"type": "string", "enum": ASSET_CLASSES}},
    },
    "required": ["summary", "why_it_matters", "topics", "asset_class"],
    "additionalProperties": False,
}

# Lightweight keyword fallback so cards are still tagged without an API key.
_KEYWORDS = {
    "rates": ["rate", "yield", "fed ", "fomc", "central bank", "treasur", "duration"],
    "equities": ["equit", "stock", "s&p", "earnings", "valuation"],
    "fixed-income": ["bond", "fixed income", "credit spread", "duration"],
    "credit": ["credit", "high yield", "spread", "default", "loan"],
    "fx": ["currency", "dollar", "fx ", "exchange rate", "yen", "euro"],
    "commodities": ["oil", "gold", "commodit", "energy price", "metal"],
    "alternatives": ["private", "alternative", "hedge", "infrastructure", "real estate"],
    "multi-asset": ["multi-asset", "allocation", "portfolio", "diversif"],
    "macro": ["inflation", "gdp", "recession", "growth", "economy", "labor", "jobs", "tariff"],
    "outlook": ["outlook", "week ahead", "forecast", "2024", "2025", "2026", "year ahead"],
}


@dataclass
class Enrichment:
    summary: str
    why_it_matters: str
    topics: list[str]
    asset_class: list[str]
    enriched: bool


def keyword_fallback(item: Item) -> Enrichment:
    text = f"{item.title} {item.raw_summary}".lower()
    topics = [t for t, kws in _KEYWORDS.items() if any(k in text for k in kws)]
    asset = [a for a in ASSET_CLASSES if a in topics]
    return Enrichment(
        summary="",  # UI falls back to raw_summary when llm_summary is empty
        why_it_matters="",
        topics=topics[:4],
        asset_class=asset[:3],
        enriched=False,
    )


def _coerce(values, allowed: list[str], limit: int) -> list[str]:
    out = []
    for v in values or []:
        v = str(v).strip().lower()
        if v in allowed and v not in out:
            out.append(v)
    return out[:limit]


class Enricher:
    def __init__(self, model: str):
        self.model = model
        self._client = None
        self.available = bool(os.environ.get("ANTHROPIC_API_KEY"))
        if self.available:
            try:
                import anthropic

                self._client = anthropic.Anthropic()
            except Exception:  # noqa: BLE001
                self.available = False

    def enrich(self, item: Item) -> Enrichment:
        if not self.available or self._client is None:
            return keyword_fallback(item)

        user = (
            f"Title: {item.title}\n"
            f"Source: {item.source_name} ({item.firm} — {item.business_unit})\n"
            f"Description: {item.raw_summary or '(no description provided)'}\n\n"
            "Return JSON with: summary (2-3 sentences), why_it_matters (one line), "
            "topics, and asset_class."
        )
        try:
            resp = self._client.messages.create(
                model=self.model,
                max_tokens=400,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user}],
                output_config={"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
            )
            text = next((b.text for b in resp.content if b.type == "text"), "")
            data = json.loads(text)
        except Exception:  # noqa: BLE001 — fall back, never break the run
            return keyword_fallback(item)

        return Enrichment(
            summary=str(data.get("summary", "")).strip(),
            why_it_matters=str(data.get("why_it_matters", "")).strip(),
            topics=_coerce(data.get("topics"), TOPICS, 4),
            asset_class=_coerce(data.get("asset_class"), ASSET_CLASSES, 3),
            enriched=True,
        )
