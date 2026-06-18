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
const FIRMCOLOR = {}; const CATLABEL = {}; const DEFAULT_COLOR = "#8A93A6";
const READ = new Set(ls.get("agg.read", []));
const STAR = new Set(ls.get("agg.star", []));
const pins = new Set(ls.get("agg.pins", []));
const favs = new Set(ls.get("agg.favs", []));

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
function colKey(it, g) { return g === "firm" ? it.firm : g === "category" ? (CATLABEL[it.category] || it.category || "—") : g === "business_unit" ? (it.business_unit || "—") : g === "content_type" ? it.content_type : "all"; }

function computeColumns(group_by, F) {
  const filtered = ALL.filter(it => matchItem(it, F));
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
    <div class="col-list"></div>
  </section>`;
}
function loadColumns() {
  syncURL();
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
  const io = new IntersectionObserver(es => es.forEach(e => { if (e.isIntersecting) loadMore(); }), { root: list, rootMargin: "300px" });
  list.appendChild(sentinel); io.observe(sentinel); loadMore();
}

/* ── filters UI ─────────────────────────────────────────────────────────── */
const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
function buildFilters() {
  const cats = FACETS.categories || [];
  $("#f-category").innerHTML = `<button class="cat-tab" data-cat="">All firms</button>`
    + cats.map(c => `<button class="cat-tab" data-cat="${esc(c.key)}">${esc(c.label)}</button>`).join("");
  $("#f-firm").innerHTML = FACETS.firms.map(f => `<button class="filt-chip" data-dot data-v="${esc(f.firm)}" data-cat="${esc(f.category || "")}" style="--dot:${esc(f.color)}">${esc(f.short || f.firm)}</button>`).join("");
  $("#f-type").innerHTML = FACETS.content_types.map(t => `<button class="filt-chip" data-v="${esc(t)}">${esc(cap(t))}</button>`).join("");
  buildDropdown("#f-unit", "Business line", FACETS.business_units, "units");
  buildDropdown("#f-topic", "Topic", FACETS.topics, "topics");
  buildPresets(); refreshFilterUI();
}
function buildDropdown(sel, label, options, key) {
  const el = $(sel);
  el.innerHTML = `<button class="dd-btn"><span class="lbl">${label}</span><svg class="ic sm"><use href="#i-chev"/></svg></button>
    <div class="dd-panel">${options.map(o => `<div class="dd-item" data-v="${esc(o)}"><span class="dd-check"><svg class="ic sm"><use href="#i-check"/></svg></span>${esc(cap(o))}</div>`).join("")}
      <div class="dd-actions"><button data-clear>Clear</button></div></div>`;
  $(".dd-btn", el).onclick = (e) => { e.stopPropagation(); closeDropdowns(el); el.classList.toggle("open"); };
  $$(".dd-item", el).forEach(it => it.onclick = () => { toggleArr(S[key], it.dataset.v); refreshFilterUI(); reload(); });
  $("[data-clear]", el).onclick = () => { S[key] = []; refreshFilterUI(); reload(); };
}
function closeDropdowns(except) { $$(".dropdown.open").forEach(d => { if (d !== except) d.classList.remove("open"); }); }
function refreshFilterUI() {
  $$("#f-category .cat-tab").forEach(t => t.classList.toggle("active", (t.dataset.cat || "") === S.category));
  $$("#f-firm .filt-chip").forEach(c => {
    c.style.display = (!S.category || c.dataset.cat === S.category) ? "" : "none";
    c.classList.toggle("active", S.firms.includes(c.dataset.v));
  });
  $$("#f-type .filt-chip").forEach(c => c.classList.toggle("active", S.types.includes(c.dataset.v)));
  syncDropdown("#f-unit", "units"); syncDropdown("#f-topic", "topics");
  $$("#f-date .chip").forEach(c => c.classList.toggle("active", String(c.dataset.days) === String(S.days)));
  $("#t-unread").classList.toggle("active", S.unread);
  $("#t-star").classList.toggle("active", S.starred);
  $("#q").value = S.q; $("#sort").value = S.sort;
  $$(".group-by .seg").forEach(b => b.classList.toggle("active", b.dataset.group === S.group_by));
  const active = S.category || S.firms.length || S.units.length || S.types.length || S.topics.length || S.q || S.days || S.unread || S.starred;
  $("#reset-btn").classList.toggle("hidden", !active);
}
function syncDropdown(sel, key) {
  const el = $(sel), n = S[key].length;
  $(".dd-btn", el).classList.toggle("has-sel", n > 0);
  $(".dd-btn .lbl", el).textContent = (el.id === "f-unit" ? "Business line" : "Topic") + (n ? ` · ${n}` : "");
  $$(".dd-item", el).forEach(it => it.classList.toggle("on", S[key].includes(it.dataset.v)));
}
function resetFilters() { S.category = ""; S.firms = []; S.units = []; S.types = []; S.topics = []; S.q = ""; S.days = ""; S.unread = false; S.starred = false; refreshFilterUI(); reload(); }

/* ── presets ────────────────────────────────────────────────────────────── */
function currentState() { const { group_by, sort, firms, units, types, topics, q, days, unread, starred } = S; return { group_by, sort, firms:[...firms], units:[...units], types:[...types], topics:[...topics], q, days, unread, starred }; }
function buildPresets() {
  const el = $("#presets"), items = ls.get("agg.presets", []);
  el.innerHTML = `<button class="dd-btn"><svg class="ic sm"><use href="#i-bookmark"/></svg><span>Views</span></button>
    <div class="dd-panel">
      ${items.length ? items.map((p, i) => `<div class="dd-item preset" data-i="${i}"><span style="flex:1">${esc(p.name)}</span><button class="del" data-del="${i}" title="Delete">✕</button></div>`).join("") : `<div class="dd-item" style="color:var(--faint);cursor:default">No saved views</div>`}
      <div class="dd-actions"><button data-save>＋ Save current view…</button></div></div>`;
  $(".dd-btn", el).onclick = (e) => { e.stopPropagation(); closeDropdowns(el); el.classList.toggle("open"); };
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
function setFlag(id, which, val) { const set = which === "read" ? READ : STAR; val ? set.add(id) : set.delete(id); ls.set(which === "read" ? "agg.read" : "agg.star", [...set]); }
function markRead(item, val) { item.classList.toggle("read", val); setFlag(item.dataset.id, "read", val); }
function onGridClick(e) {
  const tool = e.target.closest(".col-tool");
  if (tool) { const key = tool.closest(".col").dataset.key, set = tool.dataset.t === "pin" ? pins : favs; set.has(key) ? set.delete(key) : set.add(key); ls.set("agg.pins", [...pins]); ls.set("agg.favs", [...favs]); loadColumns(); return; }
  const item = e.target.closest(".item"); if (!item) return;
  const act = e.target.closest("[data-act]");
  if (act) {
    e.preventDefault(); const id = item.dataset.id;
    if (act.dataset.act === "open") { window.open(item.dataset.url, "_blank", "noopener"); markRead(item, true); }
    else if (act.dataset.act === "read") markRead(item, !item.classList.contains("read"));
    else if (act.dataset.act === "star") setFlag(id, "star", act.classList.toggle("on"));
    return;
  }
  if (e.target.closest("a")) { markRead(item, true); return; }
  item.classList.toggle("open");
}
let selEl = null;
function select(dir) { const l = $$(".item"); if (!l.length) return; let i = l.indexOf(selEl); i = dir === 0 ? 0 : Math.min(l.length - 1, Math.max(0, i + dir)); if (selEl) selEl.classList.remove("sel"); selEl = l[i]; selEl.classList.add("sel"); selEl.scrollIntoView({ block:"nearest", behavior:"smooth" }); }
function onKey(e) {
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

/* ── boot ───────────────────────────────────────────────────────────────── */
async function boot() {
  applyTheme(ls.get("agg.theme", "light"));
  seenBefore = ls.get("agg.lastVisit", null);
  ls.set("agg.lastVisit", new Date().toISOString());
  readURL();
  const [facets, data, meta] = await Promise.all([
    fetch("facets.json").then(r => r.json()),
    fetch("data.json").then(r => r.json()),
    fetch("meta.json").then(r => r.json()).catch(() => null),
  ]);
  FACETS = facets; ALL = data;
  facets.firms.forEach(f => FIRMCOLOR[f.firm] = f.color);
  (facets.categories || []).forEach(c => CATLABEL[c.key] = c.label);
  if (meta && meta.generated_at) {
    const ago = relTime({ published_at: meta.generated_at });
    $("#freshness").textContent = `${data.length.toLocaleString()} items · updated ${ago} ago`;
  }
  buildFilters();
  $$(".group-by .seg").forEach(b => b.onclick = () => { S.group_by = b.dataset.group; refreshFilterUI(); reload(); });
  $("#f-category").onclick = (e) => { const t = e.target.closest(".cat-tab"); if (!t) return;
    S.category = t.dataset.cat || "";
    if (S.category) S.firms = S.firms.filter(fm => { const ff = FACETS.firms.find(x => x.firm === fm); return ff && ff.category === S.category; });
    refreshFilterUI(); reload(); };
  $("#f-firm").onclick = (e) => { const c = e.target.closest(".filt-chip"); if (c) { toggleArr(S.firms, c.dataset.v); refreshFilterUI(); reload(); } };
  $("#f-type").onclick = (e) => { const c = e.target.closest(".filt-chip"); if (c) { toggleArr(S.types, c.dataset.v); refreshFilterUI(); reload(); } };
  $("#f-date").onclick = (e) => { const c = e.target.closest(".chip"); if (c) { S.days = c.dataset.days || ""; refreshFilterUI(); reload(); } };
  $("#t-unread").onclick = () => { S.unread = !S.unread; refreshFilterUI(); reload(); };
  $("#t-star").onclick = () => { S.starred = !S.starred; refreshFilterUI(); reload(); };
  $("#sort").onchange = (e) => { S.sort = e.target.value; reload(); };
  let qT; $("#q").oninput = (e) => { S.q = e.target.value.trim(); clearTimeout(qT); qT = setTimeout(reload, 220); };
  $("#theme-btn").onclick = () => applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
  $("#export-btn").onclick = exportDigest;
  $("#reset-btn").onclick = resetFilters;
  $("#grid").addEventListener("click", onGridClick);
  document.addEventListener("click", () => closeDropdowns(null));
  document.addEventListener("keydown", onKey);
  loadColumns();
}
boot();
