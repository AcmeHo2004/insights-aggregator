"use strict";
/* Static Insights Aggregator — all data client-side from data.json.
   Read/star state is per-viewer (localStorage). No backend. */

const $  = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, c => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;" }[c]));
const ls = {
  get: (k, d) => { try { return JSON.parse(localStorage.getItem(k)) ?? d; } catch { return d; } },
  set: (k, v) => localStorage.setItem(k, JSON.stringify(v)),
};

const S = { group_by:"firm", sort:"newest", category:"", firms:[], units:[], types:[], topics:[], q:"", days:"30", signal:true, unread:false, starred:false };
let ALL = [], FACETS = null, seenBefore = null, LIMIT = 30;
const FIRMCOLOR = {}; const FIRMSHORT = {}; const CATLABEL = {}; const FIRMCAT = {}; const DEFAULT_COLOR = "#8A93A6";
const READ = new Set(ls.get("agg.read", []));
const STAR = new Set(ls.get("agg.star", []));
const pins = new Set(ls.get("agg.pins", []));
const favs = new Set(ls.get("agg.favs", []));
let INT = ls.get("agg.interests", null);   // {firms:[], topics:[], onboarded:true}
let SYNTH = null;                          // cross-firm synthesis (synthesis.json)
let STANCE = null;                         // firm × asset-class stance grid (stance.json)
let DRIFT = null;                          // longitudinal stance shifts (drift.json)
let HEALTH = null;                         // scan health (health.json, lazy-loaded)
let META = null;                           // meta.json (counts, window, freshness)
let ARCHIVE = { count: 0, loaded: false }; // older items, lazy-loaded on demand
let API = "", TOKEN = "";                  // optional self-hosted companion API
let WEIGHTS = { firms: {}, topics: {} };   // data-driven For You affinity (from API)

/* ── "For You" relevance (client-side; signals from interests + stars) ─────── */
let STARFIRM = new Set(), STARTOPIC = {};
function buildStarSignals() {
  STARFIRM = new Set(); STARTOPIC = {};
  for (const it of ALL) {
    if (!STAR.has(it.id)) continue;
    STARFIRM.add(it.firm);
    for (const t of (it.topics || [])) STARTOPIC[t] = (STARTOPIC[t] || 0) + 1;
  }
}
const hasInterests = () => !!(INT && ((INT.firms && INT.firms.length) || (INT.topics && INT.topics.length)));
function relevanceScore(it) {
  const wantF = (INT && INT.firms) || [], wantT = (INT && INT.topics) || [];
  const tags = it.topics || [];
  let s = 0;
  if (wantF.includes(it.firm)) s += 3;                                  // followed firm
  s += 2 * tags.filter(t => wantT.includes(t)).length;                  // followed topics
  if (STARFIRM.has(it.firm)) s += 1.5;                                  // firms you star
  for (const t of tags) s += 0.6 * (STARTOPIC[t] || 0);                 // topics you star
  s += 2.5 * (WEIGHTS.firms[it.firm] || 0);                             // data-driven affinity (API)
  for (const t of tags) s += 1.2 * (WEIGHTS.topics[t] || 0);
  s += it.tier === 1 ? 1.2 : it.tier === 2 ? 0.6 : 0;                   // editorial priority
  const iso = it.published_at || it.ingested_at;
  if (iso) { const ageD = (Date.now() - Date.parse(iso)) / 864e5; if (ageD >= 0) s += Math.max(0, 1.5 - ageD / 30); }
  if (READ.has(it.id)) s -= 2;                                          // de-prioritize read
  return s;
}

/* ── client-side data layer (replaces the server API) ───────────────────── */
const sk = (it) => it.published_at || it.ingested_at || "";
const isNew = (it) => seenBefore && (it.ingested_at || "") > seenBefore;

function matchItem(it, F) {
  if (F.signal && it.tier !== 1) return false;          // high-signal: flagship/strategist (Tier 1) only
  if (F.category && it.category !== F.category) return false;
  if (F.firms.length && !F.firms.includes(it.firm)) return false;
  if (F.units.length && !F.units.includes(it.business_unit)) return false;
  if (F.types.length && !F.types.includes(it.content_type)) return false;
  if (F.topics.length && !F.topics.some(t => it.topics.includes(t))) return false;
  if (F.q) { const q = F.q.toLowerCase(); if (!((it.title || "").toLowerCase().includes(q) || (it.summary || "").toLowerCase().includes(q))) return false; }
  if (F.sinceTs) { const t = it.published_at ? Date.parse(it.published_at) : NaN; if (!(t >= F.sinceTs)) return false; }  // undated → only in "All"
  if (F.unread && READ.has(it.id)) return false;
  if (F.starred && !STAR.has(it.id)) return false;
  return true;
}
function withSince(F) {
  let sinceTs = 0;
  if (F.days === "ytd") sinceTs = new Date(new Date().getFullYear(), 0, 1).getTime();
  else if (F.days && F.days !== "all") sinceTs = Date.now() - Number(F.days) * 864e5;
  return { ...F, sinceTs };
}
function colKey(it, g) { return g === "firm" ? it.firm : g === "category" ? (CATLABEL[it.category] || it.category || "—") : g === "business_unit" ? (it.business_unit || "—") : g === "content_type" ? it.content_type : g === "foryou" ? "foryou" : "all"; }

function computeColumns(group_by, F) {
  const filtered = ALL.filter(it => matchItem(it, F));
  if (group_by === "foryou")
    return [{ key:"foryou", label:"For You", color:DEFAULT_COLOR, count:filtered.length, new_count:filtered.filter(isNew).length }];
  if (group_by === "theme") {
    const driftsFor = (topic) => ((DRIFT && DRIFT.drifts) || []).filter(d => d.topic === topic);
    const tally = (topic) => {
      const arr = filtered.filter(it => (it.topics || []).includes(topic));
      return { key:topic, label:cap(topic), color:DEFAULT_COLOR, count:arr.length,
               new_count:arr.filter(isNew).length, drifts:driftsFor(topic) };
    };
    const themes = (SYNTH && SYNTH.themes) || [];
    if (themes.length) {
      const cols = [];
      for (const t of themes) { const e = tally(t.topic); if (e.count) { e.synth = t; cols.push(e); } }
      return cols;
    }
    // no synthesis available — fall back to grouping by topic frequency
    const counts = {};
    for (const it of filtered) for (const tp of (it.topics || [])) counts[tp] = (counts[tp] || 0) + 1;
    return Object.keys(counts).map(tally).sort((a, b) => b.count - a.count);
  }
  if (group_by === "topic") {
    const order = ["macro","rates","equities","credit","fixed-income","fx","commodities","multi-asset","alternatives","outlook"];
    const counts = {};
    for (const it of filtered) for (const tp of (it.topics || [])) counts[tp] = (counts[tp] || 0) + 1;
    return Object.keys(counts).sort((a, b) => {
      const ia = order.indexOf(a), ib = order.indexOf(b);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || counts[b] - counts[a];
    }).map(tp => {
      const arr = filtered.filter(it => (it.topics || []).includes(tp));
      return { key:tp, label:cap(tp), color:DEFAULT_COLOR, count:arr.length, new_count:arr.filter(isNew).length };
    });
  }
  if (group_by === "none")
    return [{ key:"all", label:"All insights", color:DEFAULT_COLOR, count:filtered.length, new_count:filtered.filter(isNew).length }];
  const map = new Map();
  for (const it of filtered) {
    const k = colKey(it, group_by);
    let e = map.get(k);
    if (!e) { e = { key:k, label:k, color:(group_by === "firm" ? (FIRMCOLOR[k] || DEFAULT_COLOR) : DEFAULT_COLOR), count:0, new_count:0 }; map.set(k, e); }
    e.count++; if (isNew(it)) e.new_count++;
  }
  return [...map.values()].sort((a, b) => b.count - a.count);
}
function computeItems(group_by, col, F, offset, limit, sort) {
  let arr = ALL.filter(it => matchItem(it, F));
  if (group_by === "foryou") {
    arr = arr.map(it => [relevanceScore(it), it])
             .sort((a, b) => b[0] - a[0] || (sk(b[1]) < sk(a[1]) ? -1 : sk(b[1]) > sk(a[1]) ? 1 : 0))
             .map(x => x[1]);
    return arr.slice(offset, offset + limit);
  }
  if (group_by === "theme" || group_by === "topic") {
    arr = arr.filter(it => (it.topics || []).includes(col));
    arr.sort((a, b) => sort === "tier"
      ? ((a.tier - b.tier) || (sk(b) < sk(a) ? -1 : sk(b) > sk(a) ? 1 : 0))
      : (sk(b) < sk(a) ? -1 : sk(b) > sk(a) ? 1 : 0));
    return arr.slice(offset, offset + limit);
  }
  if (group_by !== "none" && col != null && col !== "all") arr = arr.filter(it => colKey(it, group_by) === col);
  arr.sort((a, b) => sort === "tier"
    ? ((a.tier - b.tier) || (sk(b) < sk(a) ? -1 : sk(b) > sk(a) ? 1 : 0))
    : (sk(b) < sk(a) ? -1 : sk(b) > sk(a) ? 1 : 0));
  return arr.slice(offset, offset + limit);
}

/* ── URL <-> state ──────────────────────────────────────────────────────── */
function syncURL() {
  const p = new URLSearchParams();
  if (S.category) p.set("category", S.category);
  S.firms.forEach(v => p.append("firm", v)); S.units.forEach(v => p.append("unit", v));
  S.types.forEach(v => p.append("type", v)); S.topics.forEach(v => p.append("topic", v));
  if (S.q) p.set("q", S.q); p.set("since_days", S.days);
  if (!S.signal) p.set("signal", "0");
  if (S.unread) p.set("unread", "1"); if (S.starred) p.set("starred", "1");
  p.set("group_by", S.group_by); if (S.sort !== "newest") p.set("sort", S.sort);
  history.replaceState(null, "", "?" + p.toString());
}
function readURL() {
  const p = new URLSearchParams(location.search);
  S.group_by = p.get("group_by") || "firm"; S.sort = p.get("sort") || "newest";
  S.category = p.get("category") || "";
  S.firms = p.getAll("firm"); S.units = p.getAll("unit"); S.types = p.getAll("type"); S.topics = p.getAll("topic");
  S.q = p.get("q") || ""; S.days = p.get("since_days") || "30";
  S.signal = p.get("signal") !== "0";
  S.unread = p.get("unread") === "1"; S.starred = p.get("starred") === "1";
}

/* ── time ───────────────────────────────────────────────────────────────── */
function relTime(it) {
  const iso = it.published_at; if (!iso) return "";   // publish time only — never the fetch/ingest time
  const d = new Date(iso), diff = (Date.now() - d) / 1000;
  if (diff < 3600) return Math.max(1, Math.floor(diff / 60)) + "m";
  if (diff < 86400) return Math.floor(diff / 3600) + "h";
  if (diff < 6 * 86400) return Math.floor(diff / 86400) + "d";
  return d.toLocaleDateString("en-US", { month:"short", day:"numeric" });
}
const absTime = (it) => { const iso = it.published_at; return iso ? new Date(iso).toLocaleDateString("en-US",{year:"numeric",month:"short",day:"numeric"}) : "undated"; };

/* ── render ─────────────────────────────────────────────────────────────── */
function itemHTML(it) {
  const icon = it.content_type === "podcast" ? "podcast" : "article";
  const label = it.content_type === "podcast" ? "PODCAST" : "ARTICLE";
  const tags = (it.topics || []).slice(0, 3).map(t => `<span class="tag">${esc(t)}</span>`).join("");
  const playUrl = `play.html?firm=${encodeURIComponent(it.firm)}&id=${encodeURIComponent(it.id)}`;
  const link = it.url || (it.audio_url ? playUrl : "#");
  const isRead = READ.has(it.id), isStar = STAR.has(it.id);
  const listen = it.audio_url ? `<a class="listen" href="${playUrl}" target="_blank" rel="noopener" title="Listen"><svg class="ic sm"><use href="#i-podcast"/></svg></a>` : "";
  return `<article class="item ${isRead ? "read" : ""}" data-id="${esc(it.id)}" data-url="${esc(link)}" style="--idot:${esc(it.color)}">
    <div class="item-meta">
      <span class="dot"></span>
      <span class="ctype"><svg class="ic"><use href="#i-${icon}"/></svg>${label}</span>
      <span class="src">${esc(it.firm_short || it.firm)} · ${esc(it.source_name)}</span>
      <span class="read-flag">· read</span>
      ${it.tier === 1 ? '<span class="t1">T1</span>' : ""}
      <span class="time" title="${esc(absTime(it))}">${esc(relTime(it))}</span>
    </div>
    <h3 class="item-title"><a href="${esc(link)}" target="_blank" rel="noopener">${esc(it.title)}</a></h3>
    ${it.summary ? `<div class="item-sum">${esc(it.summary)}</div>` : ""}
    ${it.why_it_matters ? `<div class="item-why"><b>Why it matters</b> ${esc(it.why_it_matters)}</div>` : ""}
    <div class="item-foot">${tags}
      <span class="item-act">${listen}
        <button class="star ${isStar ? "on" : ""}" data-act="star" title="Star (s)"><svg class="ic sm"><use href="#i-star"/></svg></button>
        <button class="read" data-act="read" title="Mark read (r)"><svg class="ic sm"><use href="#i-check"/></svg></button>
        <button class="open-link" data-act="open" title="Open (o)"><svg class="ic sm"><use href="#i-ext"/></svg></button>
      </span>
    </div>
  </article>`;
}
function synthHTML(t) {
  const tag = `${t.firm_count} firm${t.firm_count > 1 ? "s" : ""} · ${t.item_count} items`
    + (SYNTH && SYNTH.llm ? "" : " · rollup");
  return `<div class="col-synth">
    <div class="cs-meta">${tag}</div>
    <p class="cs-consensus">${esc(t.consensus)}</p>
    ${t.divergence ? `<p class="cs-line"><b>Divergence</b> ${esc(t.divergence)}</p>` : ""}
    ${t.shift ? `<p class="cs-line cs-shift"><b>Shift</b> ${esc(t.shift)}</p>` : ""}
  </div>`;
}
function driftHTML(ds) {
  return `<div class="col-drift">⤳ ${ds.slice(0, 3).map(d =>
    `<b>${esc(firmShort(d.firm))}</b> ${esc(d.from)}→${esc(d.to)}`).join(" · ")}</div>`;
}
function colHTML(c) {
  const pinned = pins.has(c.key), fav = favs.has(c.key);
  return `<section class="col ${pinned ? "pinned" : ""}" data-key="${esc(c.key)}" style="--accent:${esc(c.color || "var(--accent)")}">
    <header class="col-head">
      <span class="col-name">${esc(c.label)}</span>
      <span class="col-count">${c.count}</span>
      ${c.new_count ? `<span class="col-badge">${c.new_count} new</span>` : ""}
      <span class="col-tools">
        <button class="col-tool fav ${fav ? "on" : ""}" data-t="fav" title="Favorite"><svg class="ic sm"><use href="#i-star"/></svg></button>
        <button class="col-tool pin ${pinned ? "on" : ""}" data-t="pin" title="Pin to front"><svg class="ic sm"><use href="#i-pin"/></svg></button>
      </span>
    </header>
    ${c.synth ? synthHTML(c.synth) : ""}
    ${c.drifts && c.drifts.length ? driftHTML(c.drifts) : ""}
    <div class="col-list"></div>
  </section>`;
}
/* ── Consensus map: firm × asset-class stance (lexicon; prefers stance.json) ── */
const MAP_TOPICS = ["macro", "rates", "equities", "credit", "fixed-income", "fx", "commodities", "multi-asset"];
const BULL = /\b(overweight|over-weight|add(?:ing|s)?|bullish|constructive|favou?rs?|favou?red|attractive|opportunit\w*|upside|prefer\w*|tailwind\w*|resilient|outperform\w*|cheap|undervalued)\b/gi;
const BEAR = /\b(underweight|under-weight|reduc\w*|trim\w*|bearish|caution\w*|defensive|downside|avoid\w*|headwind\w*|expensive|rich|overvalued|vulnerable|underperform\w*|fragile|stretched)\b/gi;
function computeStance(F) {
  const since = Date.now() - 120 * 864e5;            // map looks back ~120d (its own window)
  const M = {};
  for (const it of ALL) {
    if (F.category && it.category !== F.category) continue;
    if (F.firms.length && !F.firms.includes(it.firm)) continue;
    if (!it.published_at || Date.parse(it.published_at) < since) continue;
    const txt = `${it.title} ${it.summary} ${it.why_it_matters}`;
    const b = (txt.match(BULL) || []).length, r = (txt.match(BEAR) || []).length;
    for (const tp of (it.topics || [])) {
      if (!MAP_TOPICS.includes(tp)) continue;
      const m = (M[it.firm] = M[it.firm] || {}), c = (m[tp] = m[tp] || { bull:0, bear:0, n:0 });
      c.bull += b; c.bear += r; c.n += 1;
    }
  }
  return M;
}
function stanceCell(c) {
  if (!c || c.n < 2) return { sym:"", cls:"sx", n: c ? c.n : 0 };
  const net = c.bull - c.bear;
  if (net >= 2) return { sym:"▲", cls:"su", n:c.n };
  if (net <= -2) return { sym:"▼", cls:"sd", n:c.n };
  return { sym:"●", cls:"sn", n:c.n };
}
const stanceSym = (s) => s === "overweight" ? { sym:"▲", cls:"su" }
  : s === "underweight" ? { sym:"▼", cls:"sd" } : { sym:"●", cls:"sn" };
function mapMatrix(F) {
  // Prefer the graded stance.json (Claude or server lexicon); else compute the
  // client-side lexicon over loaded items. Returns {matrix, llm}.
  if (STANCE && STANCE.stances && Object.keys(STANCE.stances).length) {
    const matrix = {};
    for (const f of Object.keys(STANCE.stances)) {
      if (F.firms.length && !F.firms.includes(f)) continue;
      if (F.category && FIRMCAT[f] !== F.category) continue;
      const row = {};
      for (const [tp, s] of Object.entries(STANCE.stances[f])) {
        if (!MAP_TOPICS.includes(tp)) continue;
        row[tp] = { ...stanceSym(s.stance), n: s.n || 0, note: s.rationale || "" };
      }
      if (Object.keys(row).length) matrix[f] = row;
    }
    return { matrix, llm: !!STANCE.llm };
  }
  const M = computeStance(F), matrix = {};
  for (const f of Object.keys(M)) {
    const row = {};
    for (const tp of MAP_TOPICS) { const c = stanceCell(M[f][tp]); if (c.sym) row[tp] = { sym:c.sym, cls:c.cls, n:c.n, note:"" }; }
    if (Object.keys(row).length) matrix[f] = row;
  }
  return { matrix, llm: false };
}
function renderMap(F) {
  const grid = $("#grid"), { matrix, llm } = mapMatrix(F);
  const firms = Object.keys(matrix)
    .map(f => ({ f, n: Object.values(matrix[f]).reduce((s, c) => s + (c.n || 0), 0) }))
    .sort((a, b) => b.n - a.n).map(x => x.f);
  if (!firms.length) { grid.innerHTML = `<div class="empty-col">Not enough dated notes to map — try clearing filters.</div>`; return; }
  const head = `<th class="mh-firm"></th>` + MAP_TOPICS.map(t => `<th class="mh-top">${esc(cap(t))}</th>`).join("");
  const body = firms.map(f => {
    const cells = MAP_TOPICS.map(tp => {
      const c = matrix[f][tp];
      if (!c) return `<td class="mcell sx"></td>`;
      const tip = c.note ? `${firmShort(f)} · ${cap(tp)} — ${c.note}` : `${firmShort(f)} · ${cap(tp)} — ${c.n} note${c.n !== 1 ? "s" : ""}`;
      return `<td class="mcell ${c.cls}" data-firm="${esc(f)}" data-topic="${esc(tp)}" title="${esc(tip)}">${c.sym}</td>`;
    }).join("");
    return `<tr><th class="mr-firm" style="--dot:${esc(FIRMCOLOR[f] || DEFAULT_COLOR)}">${esc(firmShort(f))}</th>${cells}</tr>`;
  }).join("");
  const src = llm ? "graded by Claude" : "tone lexicon";
  grid.innerHTML = `<div class="map-wrap">
    <table class="cmap"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>
    <div class="map-legend"><span class="su">▲</span> overweight &nbsp; <span class="sn">●</span> neutral &nbsp; <span class="sd">▼</span> underweight &nbsp;·&nbsp; last ${(STANCE && STANCE.window_days) || 90} days · ${src} · click a cell to read the notes behind it</div>
  </div>`;
  $$(".mcell", grid).forEach(td => td.onclick = () => {
    if (!td.dataset.topic || td.classList.contains("sx")) return;
    S.firms = [td.dataset.firm]; S.topics = [td.dataset.topic];
    S.group_by = "firm"; S.signal = false; S.days = "all";
    if (ARCHIVE.count && !ARCHIVE.loaded) loadArchive();
    refreshFilterUI(); reload();
  });
}
function loadColumns() {
  syncURL();
  buildStarSignals();
  const F = withSince(S), grid = $("#grid");
  if (S.group_by === "map") {
    // stance.json is precomputed server-side; only the client lexicon fallback needs the archive
    if (!STANCE && ARCHIVE.count && !ARCHIVE.loaded) { loadArchive(); return; }
    renderMap(F); return;
  }
  const cols = computeColumns(S.group_by, F);
  if (!cols.length) { grid.innerHTML = `<div class="empty-col">No items match these filters.</div>`; return; }
  grid.innerHTML = cols.map(colHTML).join("");
  const colEls = $$(".col", grid);
  cols.forEach((c, i) => initColumn(colEls[i], c, F));
}
function initColumn(colEl, c, F) {
  if (!colEl) return;
  const list = $(".col-list", colEl);
  const st = { offset: 0, done: false };
  const sentinel = document.createElement("div"); sentinel.className = "col-more";
  function loadMore() {
    if (st.done) return;
    const rows = computeItems(S.group_by, c.key, F, st.offset, LIMIT, S.sort);
    sentinel.remove();
    list.insertAdjacentHTML("beforeend", rows.map(itemHTML).join(""));
    st.offset += rows.length;
    st.done = rows.length < LIMIT;
    if (!st.done) { list.appendChild(sentinel); io.observe(sentinel); }
    else if (st.offset === 0) list.innerHTML = `<div class="empty-col">No items.</div>`;
  }
  if (c.key === "foryou" && !hasInterests()) {
    list.insertAdjacentHTML("beforeend",
      `<div class="empty-col" style="text-align:left">Ranked by what you star + how fresh it is.<br>
        <span class="cta" data-personalize>✨ Tell us your firms &amp; topics</span></div>`);
  }
  const io = new IntersectionObserver(es => es.forEach(e => { if (e.isIntersecting) loadMore(); }), { root: list, rootMargin: "300px" });
  list.appendChild(sentinel); io.observe(sentinel); loadMore();
}

/* ── filters UI ─────────────────────────────────────────────────────────── */
const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
const firmShort = (f) => FIRMSHORT[f] || f;

function buildFilters() {
  const cats = FACETS.categories || [];
  $("#fp-category").innerHTML = `<button class="seg" data-cat="">All</button>`
    + cats.map(c => `<button class="seg" data-cat="${esc(c.key)}">${esc(c.label)}</button>`).join("");
  $("#fp-firm").innerHTML = FACETS.firms.map(f =>
    `<button class="filt-chip" data-dot data-v="${esc(f.firm)}" data-cat="${esc(f.category || "")}"
       data-name="${esc(((f.short || "") + " " + f.firm).toLowerCase())}" style="--dot:${esc(f.color)}">${esc(f.short || f.firm)}</button>`).join("");
  $("#fp-unit").innerHTML = (FACETS.business_units || []).map(u =>
    `<button class="filt-chip" data-k="units" data-v="${esc(u)}">${esc(cap(u))}</button>`).join("");
  $("#fp-topic").innerHTML = (FACETS.topics || []).map(t =>
    `<button class="filt-chip" data-k="topics" data-v="${esc(t)}">${esc(cap(t))}</button>`).join("");
  $("#fp-type").innerHTML = (FACETS.content_types || []).map(t =>
    `<button class="filt-chip" data-k="types" data-v="${esc(t)}">${esc(cap(t))}</button>`).join("");
  buildPresets();
  refreshFilterUI();
}

function setCategory(cat) {
  S.category = cat || "";
  if (S.category) S.firms = S.firms.filter(fm => { const ff = FACETS.firms.find(x => x.firm === fm); return ff && ff.category === S.category; });
}

function applyFirmSearch() {
  const q = ($("#fp-firm-search").value || "").trim().toLowerCase();
  $$("#fp-firm .filt-chip").forEach(c => {
    const catOk = (!S.category || c.dataset.cat === S.category);
    const qOk = !q || c.dataset.name.includes(q);
    c.style.display = (catOk && qOk) ? "" : "none";
  });
}

function refreshFilterUI() {
  const GL = { foryou:"For You", theme:"Themes", map:"Consensus map", topic:"Topic", category:"Category", firm:"Firm", business_unit:"Business line", content_type:"Type" };
  $("#group-label").textContent = GL[S.group_by] || "Firm";
  $$("#group-menu .menu-item").forEach(b => b.classList.toggle("active", b.dataset.group === S.group_by));
  $("#sort-label").textContent = S.sort === "tier" ? "Priority" : "Newest";
  $$("#sort-menu .menu-item").forEach(b => b.classList.toggle("active", b.dataset.sort === S.sort));

  $$("#fp-category .seg").forEach(b => b.classList.toggle("active", (b.dataset.cat || "") === S.category));
  $$("#fp-firm .filt-chip").forEach(c => c.classList.toggle("active", S.firms.includes(c.dataset.v)));
  $$("#fp-unit .filt-chip").forEach(c => c.classList.toggle("active", S.units.includes(c.dataset.v)));
  $$("#fp-topic .filt-chip").forEach(c => c.classList.toggle("active", S.topics.includes(c.dataset.v)));
  $$("#fp-type .filt-chip").forEach(c => c.classList.toggle("active", S.types.includes(c.dataset.v)));
  $$("#archive-bar .seg").forEach(c => c.classList.toggle("active", String(c.dataset.days) === String(S.days)));
  $("#fp-unread").classList.toggle("active", S.unread);
  applyFirmSearch();

  $("#q").value = S.q;
  $("#t-star").classList.toggle("active", S.starred);
  $("#t-signal").classList.toggle("active", S.signal);

  const n = (S.category ? 1 : 0) + S.firms.length + S.units.length + S.topics.length + S.types.length + (S.unread ? 1 : 0);
  const badge = $("#filters-count");
  badge.textContent = n; badge.classList.toggle("hidden", n === 0);

  buildActivebar();
}

function buildActivebar() {
  const bar = $("#activebar"), chips = [];
  const add = (rm, v, label) =>
    chips.push(`<span class="afc" data-rm="${rm}"${v !== undefined ? ` data-v="${esc(v)}"` : ""}>${esc(label)}<span class="x">×</span></span>`);
  if (S.category) add("category", undefined, CATLABEL[S.category] || S.category);
  S.firms.forEach(f => add("firm", f, firmShort(f)));
  S.units.forEach(u => add("unit", u, cap(u)));
  S.topics.forEach(t => add("topic", t, cap(t)));
  S.types.forEach(t => add("type", t, cap(t)));
  if (S.signal) add("signal", undefined, "✦ Signal · T1");
  if (S.unread) add("unread", undefined, "Unread");
  if (S.starred) add("starred", undefined, "★ Starred");
  if (S.q) add("q", undefined, `“${S.q}”`);
  if (!chips.length) { bar.classList.add("hidden"); bar.innerHTML = ""; return; }
  bar.classList.remove("hidden");
  bar.innerHTML = chips.join("") + `<button class="af-clear">Clear all</button>`;
}

function closeDropdowns(except) { $$(".dropdown.open").forEach(d => { if (d !== except) d.classList.remove("open"); }); }

function resetFilters() { S.category = ""; S.firms = []; S.units = []; S.types = []; S.topics = []; S.q = ""; S.days = ""; S.unread = false; S.starred = false; refreshFilterUI(); reload(); }

/* ── modal helpers (focus management + trap) + telemetry ─────────────────── */
let _lastFocus = null;
function _focusables(el) {
  return $$('button, [href], input, [tabindex]:not([tabindex="-1"])', el)
    .filter(n => !n.disabled && n.offsetParent !== null);
}
function openModal(sel) {
  _lastFocus = document.activeElement;
  const el = $(sel); el.classList.remove("hidden");
  const f = _focusables(el); if (f.length) f[0].focus();
  el._trap = (e) => {
    if (e.key !== "Tab") return;
    const g = _focusables(el); if (!g.length) return;
    const first = g[0], last = g[g.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  };
  el.addEventListener("keydown", el._trap);
}
function closeModal(sel) {
  const el = $(sel); el.classList.add("hidden");
  if (el._trap) { el.removeEventListener("keydown", el._trap); el._trap = null; }
  if (_lastFocus && _lastFocus.focus) _lastFocus.focus();
}

/* ── optional companion API: sync + weights (no-op unless ?api= configured) ── */
function initApi() {
  const qp = new URLSearchParams(location.search);
  if (qp.get("api")) ls.set("agg.api", qp.get("api"));
  if (qp.get("token")) ls.set("agg.token", qp.get("token"));
  API = ls.get("agg.api", "") || "";
  if (API) {
    TOKEN = ls.get("agg.token", "") || (crypto.randomUUID ? crypto.randomUUID() : Math.random().toString(36).slice(2));
    ls.set("agg.token", TOKEN);
  }
}
async function syncPull() {
  if (!API) return;
  try {
    const [st, w] = await Promise.all([
      fetch(`${API}/v1/state?token=${encodeURIComponent(TOKEN)}`).then(r => r.json()),
      fetch(`${API}/v1/weights?token=${encodeURIComponent(TOKEN)}`).then(r => r.json()).catch(() => null),
    ]);
    const s = (st && st.state) || {};
    (s.read || []).forEach(id => READ.add(id)); ls.set("agg.read", [...READ]);
    (s.star || []).forEach(id => STAR.add(id)); ls.set("agg.star", [...STAR]);
    (s.pins || []).forEach(k => pins.add(k)); ls.set("agg.pins", [...pins]);
    (s.favs || []).forEach(k => favs.add(k)); ls.set("agg.favs", [...favs]);
    if (s.interests) { INT = s.interests; ls.set("agg.interests", INT); }
    if (w && (w.firms || w.topics)) WEIGHTS = { firms: w.firms || {}, topics: w.topics || {} };
  } catch { /* server down → stay on localStorage */ }
}
let _pushT;
function markDirty() {
  if (!API) return;
  clearTimeout(_pushT);
  _pushT = setTimeout(() => {
    fetch(`${API}/v1/state`, {
      method: "PUT", headers: { "content-type": "application/json" },
      body: JSON.stringify({ token: TOKEN, state: {
        read: [...READ], star: [...STAR], pins: [...pins], favs: [...favs], interests: INT } }),
    }).catch(() => {});
  }, 800);
}

/* ── archive lazy-load (data.json is recent-only; older loads on demand) ───── */
function renderDateBar() {
  const bar = $("#archive-bar"); if (!bar) return;
  const opts = [["7","7d"], ["30","30d"], ["90","90d"], ["ytd","YTD"], ["all","All"]];
  bar.classList.remove("hidden");
  bar.innerHTML = `<span class="db-k">Window</span>`
    + opts.map(([d, l]) => `<button class="seg" data-days="${d}">${l}</button>`).join("")
    + `<span class="db-note" id="db-note"></span>`;
  $$("#archive-bar .seg").forEach(b => {
    b.classList.toggle("active", String(b.dataset.days) === String(S.days));
    b.onclick = () => setDays(b.dataset.days);
  });
}
function setDays(d) {
  S.days = d || "all";
  const w = (META && META.window_days) || 60;
  const needsArchive = S.days === "all" || S.days === "ytd" || Number(S.days) > w;
  refreshFilterUI();
  if (needsArchive && ARCHIVE.count && !ARCHIVE.loaded) loadArchive();   // transparently pull older items
  else reload();
}
let _archiveCache = null, _archivePrefetching = false;
async function prefetchArchive() {   // warm the back-catalogue on idle so wide windows are instant
  if (_archiveCache || _archivePrefetching || ARCHIVE.loaded || !ARCHIVE.count) return;
  _archivePrefetching = true;
  try { _archiveCache = await fetch("data-archive.json").then(r => r.json()); } catch { /* retry on demand */ }
  _archivePrefetching = false;
}
async function loadArchive() {
  if (ARCHIVE.loaded) return;
  let a = _archiveCache;
  if (!a) { try { a = await fetch("data-archive.json").then(r => r.json()); } catch { toast("Couldn't load archive"); return; } }
  ALL = ALL.concat(a);
  ARCHIVE.loaded = true;
  loadColumns();
  track("load_archive", { n: a.length });
}
/* Privacy-first, backend-free telemetry: always keeps a capped local event log
   (a future on-device signal for For You); only phones home if an endpoint is
   configured via <meta name="agg:analytics" content="…"> or window.AGG_ANALYTICS_URL. */
function track(event, props) {
  try {
    const log = ls.get("agg.events", []);
    log.push({ t: Date.now(), event, ...(props || {}) });
    if (log.length > 500) log.splice(0, log.length - 500);
    ls.set("agg.events", log);
    const m = document.querySelector('meta[name="agg:analytics"]');
    const url = (m && m.content) || window.AGG_ANALYTICS_URL;
    if (url && navigator.sendBeacon) navigator.sendBeacon(url, JSON.stringify({ event, ...(props || {}) }));
    if (API) fetch(`${API}/v1/events`, { method: "POST", keepalive: true,
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ token: TOKEN, events: [{ t: Date.now(), event, ...(props || {}) }] }) }).catch(() => {});
  } catch (e) { /* analytics must never break the app */ }
}
async function openHealth() {
  if (!HEALTH) { try { HEALTH = await fetch("health.json").then(r => r.json()); } catch { HEALTH = null; } }
  const sub = $("#health-sub"), list = $("#health-list"), h = HEALTH;
  if (!h || !h.firms || !h.firms.length) { sub.textContent = "No scan reports yet (run a scan)."; list.innerHTML = ""; }
  else {
    const s = h.summary;
    sub.innerHTML = `<span class="hl-sum">
      <span class="hl-pill">${s.firms_reporting} reporting</span>
      <span class="hl-pill ${s.firms_with_failures ? "bad" : ""}">${s.firms_with_failures} w/ failed sources</span>
      <span class="hl-pill ${s.firms_zero_items ? "bad" : ""}">${s.firms_zero_items} zero-item</span>
      <span class="hl-pill ${s.firms_stale ? "bad" : ""}">${s.firms_stale} stale</span></span>`;
    list.innerHTML = h.firms.map(f => `<div class="hl-firm">
      <div class="hl-row"><span class="hl-name">${esc(f.firm)}</span>
        <span class="hl-stat">${f.total} items · ${f.sources_ok}/${f.sources_total} src${f.age_hours != null ? ` · ${f.age_hours}h ago` : ""}</span></div>
      ${(f.failed_sources || []).map(x => `<div class="hl-err">✕ ${esc(x.name)}: ${esc(x.error || "")}</div>`).join("")}
    </div>`).join("");
  }
  openModal("#health");
}

/* ── onboarding / personalize (For You interests) ───────────────────────── */
const ONB = { firms: new Set(), topics: new Set() };
function buildOnboarding() {
  $("#onb-firms").innerHTML = FACETS.firms.map(f =>
    `<button class="filt-chip" data-dot data-v="${esc(f.firm)}" style="--dot:${esc(f.color)}">${esc(f.short || f.firm)}</button>`).join("");
  $("#onb-topics").innerHTML = (FACETS.topics || []).map(t =>
    `<button class="filt-chip" data-v="${esc(t)}">${esc(cap(t))}</button>`).join("");
}
function syncOnb() {
  $$("#onb-firms .filt-chip").forEach(c => c.classList.toggle("active", ONB.firms.has(c.dataset.v)));
  $$("#onb-topics .filt-chip").forEach(c => c.classList.toggle("active", ONB.topics.has(c.dataset.v)));
}
function openOnboarding() {
  ONB.firms = new Set((INT && INT.firms) || []);
  ONB.topics = new Set((INT && INT.topics) || []);
  syncOnb();
  openModal("#onb");
}
const closeOnboarding = () => closeModal("#onb");
function saveInterests(firms, topics) { INT = { firms, topics, onboarded: true }; ls.set("agg.interests", INT); markDirty(); }

/* ── presets (saved views) ──────────────────────────────────────────────── */
function currentState() { const { group_by, sort, firms, units, types, topics, q, days, signal, unread, starred } = S; return { group_by, sort, firms:[...firms], units:[...units], types:[...types], topics:[...topics], q, days, signal, unread, starred }; }
const VIEW_BASE = { group_by:"firm", sort:"newest", category:"", firms:[], units:[], types:[], topics:[], q:"", days:"30", signal:true, unread:false, starred:false };
// Built-in starting points a PM actually uses (clean state, not additive).
const DEFAULT_VIEWS = [
  { name:"Consensus map",       state:{ group_by:"map" } },
  { name:"This week’s outlooks", state:{ group_by:"firm", topics:["outlook"], days:"7", signal:false } },
  { name:"Rates · Tier 1",       state:{ group_by:"firm", topics:["rates"], days:"30", signal:true } },
  { name:"Credit · Tier 1",      state:{ group_by:"firm", topics:["credit"], days:"30", signal:true } },
  { name:"Cross-firm themes",    state:{ group_by:"theme", days:"30", signal:false } },
  { name:"My follows",           state:{ group_by:"foryou" } },
];
function applyView(state) {
  Object.assign(S, VIEW_BASE, state, { firms:[...(state.firms||[])], topics:[...(state.topics||[])], units:[...(state.units||[])], types:[...(state.types||[])] });
  refreshFilterUI();
  const w = (META && META.window_days) || 60;
  if ((S.days === "all" || S.days === "ytd" || Number(S.days) > w) && ARCHIVE.count && !ARCHIVE.loaded) loadArchive();
  else reload();
}
function buildPresets() {
  const el = $("#presets"), items = ls.get("agg.presets", []);
  const builtin = DEFAULT_VIEWS.map((v, i) => `<div class="dd-item preset" data-b="${i}"><span style="flex:1">${esc(v.name)}</span></div>`).join("");
  const saved = items.length
    ? items.map((p, i) => `<div class="dd-item preset" data-i="${i}"><span style="flex:1">${esc(p.name)}</span><button class="del" data-del="${i}" title="Delete">✕</button></div>`).join("")
    : "";
  el.innerHTML = `<button class="bar-btn"><svg class="ic sm"><use href="#i-bookmark"/></svg>Views</button>
    <div class="dd-panel">
      <div class="dd-head">Quick views</div>
      ${builtin}
      ${saved ? `<div class="dd-sep"></div><div class="dd-head">Saved</div>${saved}` : ""}
      <div class="dd-actions"><button data-save>＋ Save current view…</button></div></div>`;
  $(".bar-btn", el).onclick = (e) => { e.stopPropagation(); const open = el.classList.contains("open"); closeDropdowns(null); if (!open) el.classList.add("open"); };
  $$(".preset", el).forEach(p => p.onclick = (e) => {
    if (e.target.dataset.del !== undefined) return;
    if (p.dataset.b !== undefined) applyView(DEFAULT_VIEWS[+p.dataset.b].state);
    else { const s = ls.get("agg.presets", [])[+p.dataset.i]; if (s) applyView(s.state); }
    el.classList.remove("open");
  });
  $$("[data-del]", el).forEach(b => b.onclick = (e) => { e.stopPropagation(); const a = ls.get("agg.presets", []); a.splice(+b.dataset.del, 1); ls.set("agg.presets", a); buildPresets(); el.classList.add("open"); });
  $("[data-save]", el).onclick = () => { const name = prompt("Name this view:"); if (!name) return; const a = ls.get("agg.presets", []); a.push({ name, state: currentState() }); ls.set("agg.presets", a); buildPresets(); el.classList.remove("open"); toast("View saved"); };
}

/* ── digest ─────────────────────────────────────────────────────────────── */
function digestLine(it) {
  return `- **[${esc0(it.title)}](${it.url || it.audio_url})** — ${it.firm} · ${it.source_name} · ${absTime(it)}`
    + (it.why_it_matters ? `\n  *Why it matters:* ${it.why_it_matters}` : it.summary ? `\n  ${it.summary}` : "");
}
const esc0 = (s) => String(s ?? "").replace(/\]/g, "");   // keep markdown link text clean
function buildDigest() {
  const today = new Date().toLocaleDateString("en-US", { year:"numeric", month:"long", day:"numeric" });
  const out = [`# Insights digest — ${today}`, ""];
  const starred = ALL.filter(it => STAR.has(it.id)).sort((a, b) => sk(b) < sk(a) ? -1 : 1);
  if (starred.length) { out.push(`## ★ Starred (${starred.length})`, "", ...starred.map(digestLine), ""); }
  // This week's high-signal: Tier-1, real date in last 7d, ranked by what you follow.
  const wk = Date.now() - 7 * 864e5;
  const wantF = (INT && INT.firms) || [], wantT = (INT && INT.topics) || [];
  const score = (it) => (wantF.includes(it.firm) ? 2 : 0)
    + (it.topics || []).filter(t => wantT.includes(t)).length * 2 + (it.why_it_matters ? 1 : 0);
  let hi = ALL.filter(it => it.tier === 1 && it.published_at && Date.parse(it.published_at) >= wk && !STAR.has(it.id));
  hi.sort((a, b) => score(b) - score(a) || (sk(b) < sk(a) ? -1 : 1));
  hi = hi.slice(0, 30);
  if (hi.length) {
    out.push(`## This week’s high-signal (Tier 1)`, "");
    const byTopic = {};
    for (const it of hi) { const tp = (it.topics && it.topics[0]) || "other"; (byTopic[tp] = byTopic[tp] || []).push(it); }
    for (const tp of Object.keys(byTopic)) out.push(`### ${cap(tp)}`, ...byTopic[tp].map(digestLine), "");
  }
  return (starred.length || hi.length) ? out.join("\n") : null;
}
async function exportDigest() {
  const md = buildDigest();
  if (!md) return toast("Nothing to digest yet — star a few, or check back when Tier-1 items land");
  try { await navigator.clipboard.writeText(md); toast("Digest copied — paste into email / Slack"); }
  catch { toast("Clipboard blocked — see console"); console.log(md); }
}

/* ── events ─────────────────────────────────────────────────────────────── */
let reloadT; function reload() { clearTimeout(reloadT); reloadT = setTimeout(loadColumns, 110); }
function toast(m) { const t = $("#toast"); t.textContent = m; t.classList.add("show"); setTimeout(() => t.classList.remove("show"), 1700); }
function setFlag(id, which, val) { const set = which === "read" ? READ : STAR; val ? set.add(id) : set.delete(id); ls.set(which === "read" ? "agg.read" : "agg.star", [...set]); markDirty(); }
function markRead(item, val) { item.classList.toggle("read", val); setFlag(item.dataset.id, "read", val); }
function onGridClick(e) {
  if (e.target.closest("[data-personalize]")) { openOnboarding(); return; }
  const tool = e.target.closest(".col-tool");
  if (tool) { const key = tool.closest(".col").dataset.key, set = tool.dataset.t === "pin" ? pins : favs; set.has(key) ? set.delete(key) : set.add(key); ls.set("agg.pins", [...pins]); ls.set("agg.favs", [...favs]); markDirty(); loadColumns(); return; }
  const item = e.target.closest(".item"); if (!item) return;
  const act = e.target.closest("[data-act]");
  if (act) {
    e.preventDefault(); const id = item.dataset.id, d0 = ALL.find(x => x.id === id);
    const sig = { id, firm: d0 && d0.firm, topics: d0 && d0.topics };
    if (act.dataset.act === "open") { window.open(item.dataset.url, "_blank", "noopener"); markRead(item, true); track("open", sig); }
    else if (act.dataset.act === "read") markRead(item, !item.classList.contains("read"));
    else if (act.dataset.act === "star") { setFlag(id, "star", act.classList.toggle("on")); track("star", sig); }
    return;
  }
  if (e.target.closest("a")) { markRead(item, true); return; }
  item.classList.toggle("open");
}
let selEl = null;
function select(dir) { const l = $$(".item"); if (!l.length) return; let i = l.indexOf(selEl); i = dir === 0 ? 0 : Math.min(l.length - 1, Math.max(0, i + dir)); if (selEl) selEl.classList.remove("sel"); selEl = l[i]; selEl.classList.add("sel"); selEl.scrollIntoView({ block:"nearest", behavior:"smooth" }); }
function onKey(e) {
  const openSel = !$("#onb").classList.contains("hidden") ? "#onb"
                : !$("#health").classList.contains("hidden") ? "#health" : null;
  if (openSel) { if (e.key === "Escape") closeModal(openSel); return; }
  if (/input|textarea|select/i.test(e.target.tagName)) { if (e.key === "Escape") e.target.blur(); return; }
  if (e.key === "/") { e.preventDefault(); $("#q").focus(); return; }
  if (e.key === "j") { e.preventDefault(); select(selEl ? 1 : 0); }
  else if (e.key === "k") { e.preventDefault(); select(selEl ? -1 : 0); }
  else if (!selEl) return;
  else if (e.key === "o") { window.open(selEl.dataset.url, "_blank", "noopener"); markRead(selEl, true); }
  else if (e.key === "r") markRead(selEl, !selEl.classList.contains("read"));
  else if (e.key === "s") { const b = $(".star", selEl); setFlag(selEl.dataset.id, "star", b.classList.toggle("on")); }
  else if (e.key === "Enter") selEl.classList.toggle("open");
}
function applyTheme(t) { document.documentElement.dataset.theme = t; $("#theme-btn use").setAttribute("href", t === "dark" ? "#i-moon" : "#i-sun"); ls.set("agg.theme", t); }
function toggleArr(arr, v) { const i = arr.indexOf(v); i >= 0 ? arr.splice(i, 1) : arr.push(v); }
function toggleSet(s, v) { s.has(v) ? s.delete(v) : s.add(v); }

/* ── boot ───────────────────────────────────────────────────────────────── */
function wireToggle(id) {
  const el = $(id);
  $(".bar-btn", el).onclick = (e) => { e.stopPropagation(); const open = el.classList.contains("open"); closeDropdowns(null); if (!open) el.classList.add("open"); };
}

async function boot() {
  applyTheme(ls.get("agg.theme", "light"));
  initApi();
  seenBefore = ls.get("agg.lastVisit", null);
  ls.set("agg.lastVisit", new Date().toISOString());
  readURL();
  // Personalized returning visitors land on For You unless the URL pins a group.
  if (!new URLSearchParams(location.search).has("group_by") && hasInterests()) S.group_by = "foryou";

  let facets, data, meta, synth, drift, stance;
  try {
    [facets, data, meta, synth, drift, stance] = await Promise.all([
      fetch("facets.json").then(r => r.json()),
      fetch("data.json").then(r => r.json()),
      fetch("meta.json").then(r => r.json()).catch(() => null),
      fetch("synthesis.json").then(r => r.json()).catch(() => null),
      fetch("drift.json").then(r => r.json()).catch(() => null),
      fetch("stance.json").then(r => r.json()).catch(() => null),
    ]);
  } catch {
    $("#grid").innerHTML = `<div class="empty-col">Couldn't load data. Check your connection and refresh.</div>`;
    return;
  }
  FACETS = facets; ALL = data; SYNTH = synth; DRIFT = drift; META = meta; STANCE = stance;
  ARCHIVE = { count: (meta && meta.archive_count) || 0, loaded: false };
  facets.firms.forEach(f => { FIRMCOLOR[f.firm] = f.color; FIRMSHORT[f.firm] = f.short || f.firm; FIRMCAT[f.firm] = f.category || ""; });
  (facets.categories || []).forEach(c => CATLABEL[c.key] = c.label);
  if (meta && meta.generated_at) {
    const ago = relTime({ published_at: meta.generated_at });
    const total = meta.count != null ? meta.count : data.length;
    $("#freshness").textContent = `${total.toLocaleString()} items · updated ${ago} ago` + (API ? " · sync ●" : "");
  }
  buildFilters();
  renderDateBar();
  { const w = (META && META.window_days) || 60;   // saved/shared window may need older items up front
    if ((S.days === "all" || S.days === "ytd" || Number(S.days) > w) && ARCHIVE.count) await loadArchive(); }

  // group / sort menus (single-select, close on pick)
  $("#group-menu").onclick = (e) => { const b = e.target.closest(".menu-item"); if (!b) return; S.group_by = b.dataset.group; closeDropdowns(null); refreshFilterUI(); reload(); track("group", { group: S.group_by }); };
  $("#sort-menu").onclick = (e) => { const b = e.target.closest(".menu-item"); if (!b) return; S.sort = b.dataset.sort; closeDropdowns(null); refreshFilterUI(); reload(); };
  wireToggle("#dd-group"); wireToggle("#dd-filters"); wireToggle("#dd-sort");

  // filters popover — stays open for multi-select
  $("#filters-panel").addEventListener("click", (e) => {
    if (e.target.closest("#fp-clearall")) { resetFilters(); return; }
    if (e.target.closest("#fp-done")) { closeDropdowns(null); return; }
    if (e.target.closest("#fp-unread")) { S.unread = !S.unread; refreshFilterUI(); reload(); return; }
    const clr = e.target.closest(".fp-clear");
    if (clr) { S[clr.dataset.clear] = []; refreshFilterUI(); reload(); return; }
    const seg = e.target.closest(".seg");
    if (seg && seg.closest("#fp-category")) { setCategory(seg.dataset.cat); refreshFilterUI(); reload(); return; }
    if (seg && seg.closest("#fp-date")) { S.days = seg.dataset.days || ""; refreshFilterUI(); reload(); return; }
    const chip = e.target.closest(".filt-chip");
    if (chip && chip.closest("#fp-firm")) { toggleArr(S.firms, chip.dataset.v); refreshFilterUI(); reload(); return; }
    if (chip && chip.dataset.k) { toggleArr(S[chip.dataset.k], chip.dataset.v); refreshFilterUI(); reload(); return; }
  });
  $("#fp-firm-search").oninput = applyFirmSearch;

  // active-filter strip
  $("#activebar").onclick = (e) => {
    if (e.target.closest(".af-clear")) { resetFilters(); return; }
    const chip = e.target.closest(".afc"); if (!chip) return;
    const rm = chip.dataset.rm, v = chip.dataset.v;
    if (rm === "category") S.category = "";
    else if (rm === "firm") toggleArr(S.firms, v);
    else if (rm === "unit") toggleArr(S.units, v);
    else if (rm === "topic") toggleArr(S.topics, v);
    else if (rm === "type") toggleArr(S.types, v);
    else if (rm === "signal") S.signal = false;
    else if (rm === "unread") S.unread = false;
    else if (rm === "starred") S.starred = false;
    else if (rm === "q") S.q = "";
    refreshFilterUI(); reload();
  };

  // bar actions
  $("#t-star").onclick = () => { S.starred = !S.starred; refreshFilterUI(); reload(); };
  $("#t-signal").onclick = () => { S.signal = !S.signal; refreshFilterUI(); reload(); track("signal", { on: S.signal }); };
  let qT; $("#q").oninput = (e) => { S.q = e.target.value.trim(); clearTimeout(qT); qT = setTimeout(() => { refreshFilterUI(); reload(); }, 220); };
  $("#theme-btn").onclick = () => applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
  $("#export-btn").onclick = exportDigest;
  $("#grid").addEventListener("click", onGridClick);
  document.addEventListener("click", (e) => { if (!e.target.closest(".dropdown")) closeDropdowns(null); });
  document.addEventListener("keydown", onKey);

  // onboarding / personalize
  buildOnboarding();
  $("#onb-firms").onclick = (e) => { const c = e.target.closest(".filt-chip"); if (c) { toggleSet(ONB.firms, c.dataset.v); syncOnb(); } };
  $("#onb-topics").onclick = (e) => { const c = e.target.closest(".filt-chip"); if (c) { toggleSet(ONB.topics, c.dataset.v); syncOnb(); } };
  $("#onb-save").onclick = () => { saveInterests([...ONB.firms], [...ONB.topics]); track("onboard", { firms: ONB.firms.size, topics: ONB.topics.size }); closeOnboarding(); S.group_by = "foryou"; refreshFilterUI(); reload(); };
  $("#onb-skip").onclick = () => { if (!INT) saveInterests([], []); closeOnboarding(); loadColumns(); };
  $("#onb-x").onclick = closeOnboarding;
  $("#onb").onclick = (e) => { if (e.target.id === "onb") closeOnboarding(); };
  $("#personalize-btn").onclick = openOnboarding;

  // status / health
  $("#status-link").onclick = (e) => { e.preventDefault(); openHealth(); };
  $("#health-x").onclick = () => closeModal("#health");
  $("#health").onclick = (e) => { if (e.target.id === "health") closeModal("#health"); };

  if (API) await syncPull();   // merge cross-device state + weights (no-op if server down)
  loadColumns();
  setTimeout(prefetchArchive, 3500);   // warm the archive in the background → instant "All"/wide windows
  if (!INT) openOnboarding();   // first visit
}
boot();
