"use strict";

const $ = (s) => document.querySelector(s);
const REGIONS = {
  AR: "Argentina", AU: "Australia", AT: "Austria", BD: "Bangladesh", BE: "Belgium", BR: "Brazil", CA: "Canada",
  CL: "Chile", CO: "Colombia", DK: "Denmark", EG: "Egypt", FI: "Finland", FR: "France", DE: "Germany",
  HK: "Hong Kong", IN: "India", ID: "Indonesia", IE: "Ireland", IL: "Israel", IT: "Italy", JP: "Japan",
  KR: "South Korea", MY: "Malaysia", MX: "Mexico", NL: "Netherlands", NZ: "New Zealand", NG: "Nigeria",
  NO: "Norway", PK: "Pakistan", PE: "Peru", PH: "Philippines", PL: "Poland", PT: "Portugal", SA: "Saudi Arabia",
  SG: "Singapore", ZA: "South Africa", ES: "Spain", LK: "Sri Lanka", SE: "Sweden", CH: "Switzerland",
  TW: "Taiwan", TH: "Thailand", TR: "Turkey", AE: "United Arab Emirates", GB: "United Kingdom",
  US: "United States", VN: "Vietnam",
};
const EXAMPLES = ["Inception", "3 Idiots", "Spirited Away", "The Godfather", "Parasite", "Nosferatu"];

const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode */ } },
};

/** Build DOM without innerHTML (all data is treated as text). */
function el(tag, attrs = {}, ...children) {
  const n = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") n.className = v;
    else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
    else n.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null && c !== false) n.append(c);
  return n;
}

async function api(path) {
  const r = await fetch(path);
  if (!r.ok) {
    let msg = `Request failed (${r.status})`;
    try { msg = (await r.json()).detail || msg; } catch { /* not json */ }
    throw new Error(msg);
  }
  return r.json();
}

// ---------------------------------------------------------------- region
const regionSel = $("#region");
function initRegion() {
  const guess = (navigator.language || "").split("-")[1]?.toUpperCase();
  const saved = store.get("region");
  const current = saved || (REGIONS[guess] ? guess : "US");
  Object.entries(REGIONS).sort((a, b) => a[1].localeCompare(b[1]))
    .forEach(([code, name]) => regionSel.append(el("option", { value: code, selected: code === current }, name)));
  regionSel.addEventListener("change", () => {
    store.set("region", regionSel.value);
    watchCache.clear();
    document.querySelectorAll(".watch").forEach((w) => w.remove());
    document.querySelectorAll(".card .btn").forEach((b) => { b.hidden = false; });
    if ($("#free-only").checked) applyFreeOnly();
  });
}

// ---------------------------------------------------------------- helpers
function initials(title) {
  return title.replace(/[^\p{L}\p{N}\s]/gu, "").split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "?";
}
function hue(s) { let h = 0; for (const c of s) h = (h * 31 + c.codePointAt(0)) % 360; return h; }
function posterEl(m) {
  const h = hue(m.title);
  const p = el("div", { class: "poster", "aria-hidden": "true" }, initials(m.title));
  p.style.background = `linear-gradient(150deg, hsl(${h} 55% 42%), hsl(${(h + 40) % 360} 60% 22%))`;
  return p;
}
function facts(m) {
  return [m.year, m.genres, m.runtime ? `${m.runtime} min` : null].filter(Boolean).join(" · ");
}

const detailCache = new Map();
async function loadDetails(m, posterNode, overviewNode) {
  if (!detailCache.has(m.id)) detailCache.set(m.id, api(`/api/details?id=${encodeURIComponent(m.id)}`).catch(() => ({})));
  const d = await detailCache.get(m.id);
  if (d.poster && posterNode) {
    const img = el("img", { src: d.poster, alt: `${m.title} poster`, loading: "lazy" });
    img.addEventListener("load", () => { posterNode.textContent = ""; posterNode.classList.add("has-img"); posterNode.append(img); });
  }
  if (d.overview && overviewNode) overviewNode.textContent = d.overview;
}

// ---------------------------------------------------------------- where to watch
const watchCache = new Map();
function getWatch(id) {
  const key = `${id}|${regionSel.value}`;
  if (!watchCache.has(key)) {
    watchCache.set(key, api(`/api/watch?id=${encodeURIComponent(id)}&region=${regionSel.value}`)
      .catch((e) => { watchCache.delete(key); throw e; }));
  }
  return watchCache.get(key);
}

function watchPanel(w) {
  const free = w.offers.filter((o) => o.kind === "free" || o.kind === "ads");
  const paid = w.offers.filter((o) => !(o.kind === "free" || o.kind === "ads"));
  const link = (o, cls) => el(o.url ? "a" : "span",
    { class: `badge ${cls}`, href: o.url, target: o.url ? "_blank" : null, rel: o.url ? "noopener" : null },
    o.provider + (o.kind === "ads" ? " (ads)" : ""));
  const panel = el("div", { class: "watch" });
  if (free.length) panel.append(el("div", { class: "row" }, el("span", { class: "label" }, "🆓 Free and legal"), free.map((o) => link(o, "free"))));
  const kinds = { flatrate: "Subscription", rent: "Rent", buy: "Buy" };
  for (const [kind, label] of Object.entries(kinds)) {
    const list = paid.filter((o) => o.kind === kind);
    if (list.length) panel.append(el("div", { class: "row" }, el("span", { class: "label" }, label), list.slice(0, 6).map((o) => link(o, "paid"))));
  }
  const checked = w.notes.some((n) => n.includes("JustWatch"));
  if (!w.offers.length) {
    panel.append(el("div", { class: "note" }, checked
      ? `No streaming offers found in ${REGIONS[w.region] || w.region}.`
      : "Check where it's streaming:"));
  }
  if (w.tmdb_link) panel.append(el("a", { href: w.tmdb_link, target: "_blank", rel: "noopener", class: "note" }, "All options on TMDB / JustWatch →"));
  else panel.append(el("div", { class: "row" }, el("span", { class: "label" }, "Search"),
    w.search_links.map((l) => el("a", { class: "badge paid", href: l.url, target: "_blank", rel: "noopener" }, l.name))));
  return panel;
}

async function showWatch(m, container, btn) {
  btn.disabled = true;
  btn.textContent = "Checking…";
  try {
    const w = await getWatch(m.id);
    container.querySelector(".watch")?.remove();
    container.append(watchPanel(w));
    btn.hidden = true;
  } catch (e) {
    btn.textContent = "Try again";
    btn.title = e.message;
  } finally {
    btn.disabled = false;
    if (!btn.hidden && btn.textContent === "Checking…") btn.textContent = "Where to watch";
  }
}

// ---------------------------------------------------------------- results
let current = null;

function card(m) {
  const poster = posterEl(m);
  const body = el("div", { class: "body" });
  const btn = el("button", { class: "btn", type: "button" }, "Where to watch");
  btn.addEventListener("click", () => showWatch(m, body, btn));
  body.append(
    el("h3", {}, el("button", { type: "button", title: `More like ${m.title}`, onclick: () => go(m.id, m.title) }, m.title)),
    el("div", { class: "facts" }, facts(m)),
    m.director ? el("div", { class: "facts" }, `Dir. ${m.director.split(", ").slice(0, 2).join(", ")}`) : null,
    m.score != null ? el("div", { class: "match" }, `${Math.round(m.score * 100)}% match`) : null,
    btn,
  );
  const c = el("article", { class: "card", "data-id": m.id }, poster, body);
  loadDetails(m, poster, null);
  return c;
}

function renderMatched(m) {
  const poster = posterEl(m);
  const overview = el("p", { class: "overview" });
  const box = el("div", {});
  const btn = el("button", { class: "btn", type: "button" }, "Where to watch");
  btn.style.padding = "8px 14px";
  btn.addEventListener("click", () => showWatch(m, box, btn));
  box.append(
    el("h2", {}, m.title),
    el("div", { class: "facts" }, facts(m)),
    m.director ? el("div", { class: "facts" }, `Directed by ${m.director}`) : null,
    overview,
    btn,
  );
  $("#matched").replaceChildren(poster, box);
  loadDetails(m, poster, overview);
}

async function go(id, label, push = true) {
  const status = $("#status");
  $("#result").hidden = false;
  $("#hero").classList.add("compact");
  $("#suggestions").hidden = true;
  status.textContent = `Finding films like ${label || "that"}…`;
  $("#grid").replaceChildren();
  try {
    const qs = id ? `id=${encodeURIComponent(id)}` : `q=${encodeURIComponent(label)}`;
    const data = await api(`/api/recommend?${qs}&top=24`);
    current = data;
    renderMatched(data.matched);
    $("#grid").replaceChildren(...data.results.map(card));
    status.textContent = data.results.length ? "" : "No similar films found.";
    document.title = `Like ${data.matched.title} · Recommend-It`;
    $("#q").value = data.matched.title;
    if (push) history.pushState({ id: data.matched.id }, "", `/film/${encodeURIComponent(data.matched.id)}`);
    if ($("#free-only").checked) applyFreeOnly();
    window.scrollTo({ top: 0, behavior: "smooth" });
  } catch (e) {
    status.textContent = e.message;
    $("#matched").replaceChildren();
  }
}

async function applyFreeOnly() {
  const on = $("#free-only").checked;
  const cards = [...document.querySelectorAll("#grid .card")];
  const status = $("#status");
  if (!on) { cards.forEach((c) => { c.hidden = false; }); status.textContent = ""; return; }
  if (!current) return;
  status.textContent = "Checking which films stream free in your country…";
  let shown = 0;
  await Promise.all(current.results.map(async (m, i) => {
    const c = cards[i];
    try {
      const w = await getWatch(m.id);
      const free = w.offers.some((o) => o.kind === "free" || o.kind === "ads");
      c.hidden = !free;
      if (free) {
        shown++;
        const body = c.querySelector(".body");
        if (!body.querySelector(".watch")) { body.append(watchPanel(w)); body.querySelector(".btn").hidden = true; }
      }
    } catch { c.hidden = true; }
  }));
  if ($("#free-only").checked) {
    status.textContent = shown ? `${shown} of ${cards.length} stream free and legally in ${REGIONS[regionSel.value]}.`
      : `None of these stream free in ${REGIONS[regionSel.value]} right now. Turn off the filter to see paid options.`;
  }
}

// ---------------------------------------------------------------- search box
const input = $("#q");
const list = $("#suggestions");
let items = [], active = -1, timer = null, seq = 0;

function renderSuggestions() {
  list.replaceChildren(...items.map((m, i) => el("li", {
    role: "option", id: `opt-${i}`, "aria-selected": i === active ? "true" : "false",
    onmousedown: (e) => { e.preventDefault(); go(m.id, m.title); },
  }, el("span", {}, m.title), el("span", { class: "meta" }, [m.year, m.director?.split(", ")[0]].filter(Boolean).join(" · ")))));
  list.hidden = !items.length;
  input.setAttribute("aria-expanded", String(!list.hidden));
  input.setAttribute("aria-activedescendant", active >= 0 ? `opt-${active}` : "");
}

input.addEventListener("input", () => {
  clearTimeout(timer);
  const q = input.value.trim();
  if (q.length < 2) { items = []; renderSuggestions(); return; }
  timer = setTimeout(async () => {
    const mine = ++seq;
    try {
      const res = await api(`/api/suggest?q=${encodeURIComponent(q)}&limit=8`);
      if (mine === seq) { items = res; active = -1; renderSuggestions(); }
    } catch { /* ignore */ }
  }, 160);
});
input.addEventListener("keydown", (e) => {
  if (list.hidden) return;
  if (e.key === "ArrowDown") { active = Math.min(active + 1, items.length - 1); renderSuggestions(); e.preventDefault(); }
  else if (e.key === "ArrowUp") { active = Math.max(active - 1, -1); renderSuggestions(); e.preventDefault(); }
  else if (e.key === "Escape") { items = []; renderSuggestions(); }
});
input.addEventListener("blur", () => setTimeout(() => { list.hidden = true; }, 100));
$("#search-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const m = active >= 0 ? items[active] : null;
  if (m) go(m.id, m.title);
  else if (input.value.trim()) go(null, input.value.trim());
});
$("#free-only").addEventListener("change", applyFreeOnly);

// ---------------------------------------------------------------- boot
initRegion();
$("#examples").append("Try:", ...EXAMPLES.map((t) => el("button", { class: "chip", type: "button", onclick: () => go(null, t) }, t)));
function route(push) {
  const m = location.pathname.match(/^\/film\/([^/]+)/);
  if (m) go(decodeURIComponent(m[1]), "", push);
}
window.addEventListener("popstate", () => {
  if (location.pathname === "/") { $("#result").hidden = true; $("#hero").classList.remove("compact"); }
  else route(false);
});
route(false);
