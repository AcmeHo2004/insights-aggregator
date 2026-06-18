"""Minimal smoke tests for insights_core + build_static (no network, deterministic).

Run from the repo root:  python -m pytest tests/ -q
"""

import importlib.util
from pathlib import Path

from insights_core import normalize, dedup, enrich, db, config
from insights_core.models import Item
from insights_core.types import Source

ROOT = Path(__file__).resolve().parent.parent


def _src(**kw):
    base = dict(name="Feed", firm="Acme", business_unit="Research",
                method="rss", content_type="podcast", url="https://x/feed", tier=1)
    base.update(kw)
    return Source(**base)


def _item(id, title, published_at, **kw):
    base = dict(id=id, firm="Acme", business_unit="Research", source_name="Feed",
                source_type="rss", content_type="article", title=title,
                url=f"https://x/{id}", canonical_url=f"https://x/{id}",
                published_at=published_at, dedup_key=id)
    base.update(kw)
    return Item(**base)


# ── normalize ────────────────────────────────────────────────────────────────
def test_canonicalize_url_strips_tracking_and_normalizes():
    u = normalize.canonicalize_url("HTTPS://Example.com/Path/?utm_source=x&id=7&ref=y")
    assert u == "https://example.com/Path?id=7"   # host lowercased, utm_/ref dropped, trailing slash gone


def test_hash_id_is_stable_and_16_chars():
    a = normalize.hash_id("seed"); b = normalize.hash_id("seed")
    assert a == b and len(a) == 16 and a != normalize.hash_id("other")


def test_clean_text_strips_tags_ws_and_truncates():
    assert normalize.clean_text("<p>hi   there</p>") == "hi there"
    assert normalize.clean_text("word " * 100, max_chars=20).endswith("…")


def test_normalize_entry_maps_to_item_with_audio():
    entry = {
        "link": "https://x/ep1?utm_medium=rss",
        "id": "guid-1",
        "title": "Rates <b>outlook</b>",
        "enclosures": [{"href": "https://cdn/a.mp3", "type": "audio/mpeg"}],
        "summary": "Show notes here",
    }
    it = normalize.normalize_entry(entry, _src())
    assert it.firm == "Acme" and it.title == "Rates outlook"
    assert it.audio_url == "https://cdn/a.mp3"
    assert it.guid == "guid-1" and it.id == normalize.hash_id("guid-1")
    assert "utm_" not in it.canonical_url


# ── dedup ────────────────────────────────────────────────────────────────────
def test_assign_clusters_collapses_near_dupes_in_window():
    items = [
        _item("a", "Fed rate outlook for 2026", "2026-06-10T00:00:00+00:00"),
        _item("b", "Fed rate outlook for 2026!", "2026-06-11T00:00:00+00:00"),  # near-dup, 1d apart
        _item("c", "Totally different equities note", "2026-06-10T00:00:00+00:00"),
    ]
    m = dedup.assign_clusters(items, window_days=3)
    assert m["a"] == m["b"]          # clustered together
    assert m["a"] == "a"             # canonical = earliest
    assert m["c"] == "c"             # singleton


def test_assign_clusters_respects_window():
    items = [
        _item("a", "Same exact title here", "2026-01-01T00:00:00+00:00"),
        _item("b", "Same exact title here", "2026-06-01T00:00:00+00:00"),  # months apart
    ]
    m = dedup.assign_clusters(items, window_days=3)
    assert m["a"] != m["b"]


# ── enrich (no API key → keyword fallback) ───────────────────────────────────
def test_keyword_fallback_tags_topics():
    e = enrich.keyword_fallback(_item("x", "Inflation and the Fed rate path", None,
                                      raw_summary="treasury yields"))
    assert e.enriched is False
    assert "macro" in e.topics and "rates" in e.topics


def test_enricher_without_key_falls_back(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    er = enrich.Enricher(model="claude-haiku-4-5")
    assert er.available is False
    out = er.enrich(_item("x", "Oil and commodities", None))
    assert out.enriched is False and "commodities" in out.topics
    # batch path also degrades cleanly to per-item fallback
    many = er.enrich_many([_item("y", "equities valuation", None)])
    assert len(many) == 1 and many[0].enriched is False


# ── config ───────────────────────────────────────────────────────────────────
def test_load_config_reads_params_and_merges_settings(tmp_path):
    (tmp_path / "sources.yaml").write_text(
        "settings:\n  max_enrich_per_feed: 5\n"
        "sources:\n"
        "  - name: Web\n    firm: Acme\n    business_unit: Research\n"
        "    method: scrape\n    content_type: article\n    url: https://x\n"
        "    adapter: sitemap_articles\n    params:\n      sitemap_url: https://x/sm.xml\n"
        "      include: ['/insights/']\n",
        encoding="utf-8",
    )
    cfg = config.load_config(tmp_path, default_firm="Acme")
    assert cfg.settings["max_enrich_per_feed"] == 5          # override
    assert cfg.settings["llm_model"] == "claude-haiku-4-5"   # default preserved
    s = cfg.sources[0]
    assert s.adapter == "sitemap_articles" and s.params["sitemap_url"] == "https://x/sm.xml"


# ── db round-trip ────────────────────────────────────────────────────────────
def test_db_insert_enrich_roundtrip(tmp_path):
    conn = db.connect(tmp_path / "data" / "insights.db")
    db.init_db(conn)
    assert db.insert_item(conn, _item("a", "t", "2026-06-10T00:00:00+00:00")) is True
    assert db.insert_item(conn, _item("a", "t", "2026-06-10T00:00:00+00:00")) is False  # dup ignored
    conn.commit()
    assert db.existing_ids(conn) == {"a"}
    db.update_enrichment(conn, "a", llm_summary="s", why_it_matters="w",
                         topics=["rates"], asset_class=["rates"], enriched=True)
    conn.commit()
    assert db.counts(conn) == {"total": 1, "enriched": 1}


# ── build_static.collapse_clusters ───────────────────────────────────────────
def _build_static():
    spec = importlib.util.spec_from_file_location("build_static", ROOT / "build_static.py")
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    return mod


def test_collapse_clusters_keeps_canonical_and_borrows_link():
    bs = _build_static()
    rows = [
        {"id": "C", "cluster_id": "C", "url": "", "audio_url": "audioC"},
        {"id": "M", "cluster_id": "C", "url": "pageM", "audio_url": "audioM"},
        {"id": "S", "cluster_id": "S", "url": "pageS", "audio_url": ""},
    ]
    kept = bs.collapse_clusters([dict(r) for r in rows])
    by = {d["id"]: d for d in kept}
    assert set(by) == {"C", "S"}            # M collapsed into C
    assert by["C"]["url"] == "pageM"        # borrowed missing link
    assert by["C"]["audio_url"] == "audioC" # kept its own
