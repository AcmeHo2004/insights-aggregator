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

const S = { group_by:"firm", sort:"newest", category:"", firms:[], units:[], types:[], topics:[], q:"", days:"", unread:false, starred:false };
let ALL = [], FACETS = null, seenBefore = null, LIMIT = 30;
const FIRMCOLOR = {}; const FIRMSHORT = {}; const CATLABEL = {}; const DEFAULT_COLOR = "#8A93A6";
const READ = new Set(ls.get("agg.read", []));
const STAR = new Set(ls.get("agg.star", []));
const pins = new Set(ls.get("agg.pins", []));
const favs = new Set(ls.get("agg.favs", []));
let INT = ls.get("agg.interests", null);   // {firms:[], topics:[], onboarded:true}
let SYNTH = null;                          // cross-firm synthesis (synthesis.json)
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
  if (F.category && it.category !== F.category) return false;
  if (F.firms.length && !F.firms.includes(it.firm)) return false;
  if (F.units.length && !F.units.includes(it.business_unit)) return false;
  if (F.types.length && !F.types.includes(it.content_type)) return false;
  if (F.topics.length && !F.topics.some(t => it.topics.includes(t))) return false;
  if (F.q) { const q = F.q.toLowerCase(); if (!((it.title || "").toLowerCase().includes(q) || (it.summary || "").toLowerCase().includes(q))) return false; }
  if (F.sinceTs && !(Date.parse(it.published_at || it.ingested_at || 0) >= F.sinceTs)) return false;
  if (F.unread && READ.has(it.id)) return false;
  if (F.starred && !STAR.has(it.id)) return false;
  return true;
}
function withSince(F) { return { ...F, sinceTs: F.days ? Date.now() - F.days * 864e5 : 0 }; }
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
  if (group_by === "theme") {
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
  if (S.q) p.set("q", S.q); if (S.days) p.set("since_days", S.days);
  if (S.unread) p.set("unread", "1"); if (S.starred) p.set("starred", "1");
  p.set("group_by", S.group_by); if (S.sort !== "newest") p.set("sort", S.sort);
  history.replaceState(null, "", "?" + p.toString());
}
function readURL() {
  const p = new URLSearchParams(location.search);
  S.group_by = p.get("group_by") || "firm"; S.sort = p.get("sort") || "newest";
  S.category = p.get("category") || "";
  S.firms = p.getAll("firm"); S.units = p.getAll("unit"); S.types = p.getAll("type"); S.topics = p.getAll("topic");
  S.q = p.get("q") || ""; S.days = p.get("since_days") || "";
  S.unread = p.get("unread") === "1"; S.starred = p.get("starred") === "1";
}

/* ── time ───────────────────────────────────────────────────────────────── */
function relTime(it) {
  const iso = it.published_at || it.ingested_at; if (!iso) return "";
  const d = new Date(iso), diff = (Date.now() - d) / 1000;
  if (diff < 3600) return Math.max(1, Math.floor(diff / 60)) + "m";
  if (diff < 86400) return Math.floor(diff / 3600) + "h";
  if (diff < 6 * 86400) return Math.floor(diff / 86400) + "d";
  return d.toLocaleDateString("en-US", { month:"short", day:"numeric" });
}
const absTime = (it) => { const iso = it.published_at || it.ingested_at; return iso ? new Date(iso).toLocaleDateString("en-US",{year:"numeric",month:"short",day:"numeric"}) : ""; };

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
function loadColumns() {
  syncURL();
  buildStarSignals();
  const F = withSince(S), grid = $("#grid");
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
  const GL = { foryou:"For You", theme:"Themes", category:"Category", firm:"Firm", business_unit:"Business line", content_type:"Type" };
  $("#group-label").textContent = GL[S.group_by] || "Firm";
  $$("#group-menu .menu-item").forEach(b => b.classList.toggle("active", b.dataset.group === S.group_by));
  $("#sort-label").textContent = S.sort === "tier" ? "Priority" : "Newest";
  $$("#sort-menu .menu-item").forEach(b => b.classList.toggle("active", b.dataset.sort === S.sort));

  $$("#fp-category .seg").forEach(b => b.classList.toggle("active", (b.dataset.cat || "") === S.category));
  $$("#fp-firm .filt-chip").forEach(c => c.classList.toggle("active", S.firms.includes(c.dataset.v)));
  $$("#fp-unit .filt-chip").forEach(c => c.classList.toggle("active", S.units.includes(c.dataset.v)));
  $$("#fp-topic .filt-chip").forEach(c => c.classList.toggle("active", S.topics.includes(c.dataset.v)));
  $$("#fp-type .filt-chip").forEach(c => c.classList.toggle("active", S.types.includes(c.dataset.v)));
  $$("#fp-date .seg").forEach(c => c.classList.toggle("active", String(c.dataset.days) === String(S.days)));
  $("#fp-unread").classList.toggle("active", S.unread);
  applyFirmSearch();

  $("#q").value = S.q;
  $("#t-star").classList.toggle("active", S.starred);

  const n = (S.category ? 1 : 0) + S.firms.length + S.units.length + S.topics.length + S.types.length + (S.days ? 1 : 0) + (S.unread ? 1 : 0);
  const badge = $("#filters-count");
  badge.textContent = n; badge.classList.toggle("hidden", n === 0);

  buildActivebar();
}

function buildActivebar() {
  const bar = $("#activebar"), chips = [];
  const DAYS = { "1":"Today", "7":"7 days", "30":"30 days" };
  const add = (rm, v, label) =>
    chips.push(`<span class="afc" data-rm="${rm}"${v !== undefined ? ` data-v="${esc(v)}"` : ""}>${esc(label)}<span class="x">×</span></span>`);
  if (S.category) add("category", undefined, CATLABEL[S.category] || S.category);
  S.firms.forEach(f => add("firm", f, firmShort(f)));
  S.units.forEach(u => add("unit", u, cap(u)));
  S.topics.forEach(t => add("topic", t, cap(t)));
  S.types.forEach(t => add("type", t, cap(t)));
  if (S.days) add("days", undefined, DAYS[S.days] || S.days + "d");
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
function renderArchiveBar() {
  const bar = $("#archive-bar");
  if (!ARCHIVE.count || ARCHIVE.loaded) { bar.classList.add("hidden"); bar.innerHTML = ""; return; }
  const w = (META && META.window_days) || 60;
  bar.classList.remove("hidden");
  bar.innerHTML = `Showing the last ${w} days · ${ALL.length.toLocaleString()} items.
    <button class="load" id="load-archive">Load full archive (${ARCHIVE.count.toLocaleString()} older)</button>`;
  $("#load-archive").onclick = loadArchive;
}
async function loadArchive() {
  const btn = $("#load-archive"); if (btn) { btn.disabled = true; btn.textContent = "Loading…"; }
  let a;
  try { a = await fetch("data-archive.json").then(r => r.json()); }
  catch { toast("Couldn't load archive"); if (btn) btn.disabled = false; return; }
  ALL = ALL.concat(a);
  ARCHIVE.loaded = true;
  renderArchiveBar();
  loadColumns();
  toast(`Loaded ${a.length.toLocaleString()} older items`);
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
function currentState() { const { group_by, sort, firms, units, types, topics, q, days, unread, starred } = S; return { group_by, sort, firms:[...firms], units:[...units], types:[...types], topics:[...topics], q, days, unread, starred }; }
function buildPresets() {
  const el = $("#presets"), items = ls.get("agg.presets", []);
  el.innerHTML = `<button class="bar-btn"><svg class="ic sm"><use href="#i-bookmark"/></svg>Views</button>
    <div class="dd-panel">
      ${items.length ? items.map((p, i) => `<div class="dd-item preset" data-i="${i}"><span style="flex:1">${esc(p.name)}</span><button class="del" data-del="${i}" title="Delete">✕</button></div>`).join("") : `<div class="dd-item" style="color:var(--faint);cursor:default">No saved views</div>`}
      <div class="dd-actions"><button data-save>＋ Save current view…</button></div></div>`;
  $(".bar-btn", el).onclick = (e) => { e.stopPropagation(); const open = el.classList.contains("open"); closeDropdowns(null); if (!open) el.classList.add("open"); };
  $$(".preset", el).forEach(p => p.onclick = (e) => { if (e.target.dataset.del !== undefined) return; const s = ls.get("agg.presets", [])[+p.dataset.i]; if (s) { Object.assign(S, s.state); refreshFilterUI(); reload(); el.classList.remove("open"); } });
  $$("[data-del]", el).forEach(b => b.onclick = (e) => { e.stopPropagation(); const a = ls.get("agg.presets", []); a.splice(+b.dataset.del, 1); ls.set("agg.presets", a); buildPresets(); el.classList.add("open"); });
  $("[data-save]", el).onclick = () => { const name = prompt("Name this view:"); if (!name) return; const a = ls.get("agg.presets", []); a.push({ name, state: currentState() }); ls.set("agg.presets", a); buildPresets(); el.classList.remove("open"); toast("View saved"); };
}

/* ── digest ─────────────────────────────────────────────────────────────── */
async function exportDigest() {
  const items = ALL.filter(it => STAR.has(it.id)).sort((a, b) => sk(b) < sk(a) ? -1 : 1);
  if (!items.length) return toast("No starred items");
  const today = new Date().toLocaleDateString("en-US", { year:"numeric", month:"long", day:"numeric" });
  const md = [`# Insights digest — ${today}`, ""].concat(items.map(it =>
    `- **[${it.title}](${it.url || it.audio_url})** — ${it.firm} · ${it.source_name} · ${absTime(it)}` + (it.summary ? `\n  ${it.summary}` : ""))).join("\n");
  try { await navigator.clipboard.writeText(md); toast(`Copied ${items.length} starred items`); }
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

  let facets, data, meta, synth, drift;
  try {
    [facets, data, meta, synth, drift] = await Promise.all([
      fetch("facets.json").then(r => r.json()),
      fetch("data.json").then(r => r.json()),
      fetch("meta.json").then(r => r.json()).catch(() => null),
      fetch("synthesis.json").then(r => r.json()).catch(() => null),
      fetch("drift.json").then(r => r.json()).catch(() => null),
    ]);
  } catch {
    $("#grid").innerHTML = `<div class="empty-col">Couldn't load data. Check your connection and refresh.</div>`;
    return;
  }
  FACETS = facets; ALL = data; SYNTH = synth; DRIFT = drift; META = meta;
  ARCHIVE = { count: (meta && meta.archive_count) || 0, loaded: false };
  facets.firms.forEach(f => { FIRMCOLOR[f.firm] = f.color; FIRMSHORT[f.firm] = f.short || f.firm; });
  (facets.categories || []).forEach(c => CATLABEL[c.key] = c.label);
  if (meta && meta.generated_at) {
    const ago = relTime({ published_at: meta.generated_at });
    const total = meta.count != null ? meta.count : data.length;
    $("#freshness").textContent = `${total.toLocaleString()} items · updated ${ago} ago` + (API ? " · sync ●" : "");
  }
  buildFilters();
  renderArchiveBar();

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
    else if (rm === "days") S.days = "";
    else if (rm === "unread") S.unread = false;
    else if (rm === "starred") S.starred = false;
    else if (rm === "q") S.q = "";
    refreshFilterUI(); reload();
  };

  // bar actions
  $("#t-star").onclick = () => { S.starred = !S.starred; refreshFilterUI(); reload(); };
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
  if (!INT) openOnboarding();   // first visit
}
boot();
