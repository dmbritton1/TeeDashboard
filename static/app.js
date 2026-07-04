const VIEWS = ["dashboard", "add", "library", "settings"];
function currentView() {
  const h = location.hash.replace("#", "");
  return VIEWS.includes(h) ? h : "dashboard";
}
function showView() {
  const v = currentView();
  VIEWS.forEach(name => {
    document.getElementById("view-" + name).hidden = name !== v;
    document.querySelector(`.navlink[data-view="${name}"]`).classList.toggle("active", name === v);
  });
  render();
}
window.addEventListener("hashchange", showView);

// effective tags: style text split on commas ∪ manual tags (lowercased)
function tagsOf(d) {
  const split = s => (s || "").split(",").map(x => x.trim().toLowerCase()).filter(Boolean);
  return [...new Set([...split(d.filters), ...split(d.tags)])];
}

function renderDashboard() {
  const counts = {};
  designs.forEach(d => {
    const t = d.status === "generating" ? "queued" : d.status;
    counts[t] = (counts[t] || 0) + 1;
  });
  const cards = [
    { stage: "pending", label: "Awaiting review", n: counts.pending || 0, alert: true },
    { stage: "queued", label: "In queue", n: counts.queued || 0 },
    { stage: "approved", label: "Ready to publish", n: counts.approved || 0 },
    { stage: "failed", label: "Failed", n: counts.failed || 0 },
  ];
  document.getElementById("snapshot").innerHTML = cards.map(c =>
    `<button class="stat-card ${c.alert && c.n ? "alert" : ""}" onclick="tab='${c.stage}';render()">` +
    `<div class="num">${c.n}</div><div class="lbl">${c.label}</div></button>`).join("");
  renderCharts();
}
function isoWeek(dateStr) {
  const d = new Date(dateStr + "Z");
  const t = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()));
  t.setUTCDate(t.getUTCDate() + 4 - (t.getUTCDay() || 7));
  const yearStart = new Date(Date.UTC(t.getUTCFullYear(), 0, 1));
  const week = Math.ceil(((t - yearStart) / 86400000 + 1) / 7);
  return `${t.getUTCFullYear()}-W${String(week).padStart(2, "0")}`;
}

function renderCharts() {
  // quality: approved (incl published) vs rejected per week, last 8 weeks with data
  const weeks = {};
  designs.forEach(d => {
    const ok = d.status === "approved" || d.status === "published";
    const bad = d.status === "rejected";
    if (!ok && !bad) return;
    const w = isoWeek(d.reviewed_at || d.created_at);
    weeks[w] = weeks[w] || { ok: 0, bad: 0 };
    weeks[w][ok ? "ok" : "bad"]++;
  });
  const keys = Object.keys(weeks).sort().slice(-8);
  const qEl = document.getElementById("chart_quality");
  qEl.innerHTML = `<div class="chart-title">Approval rate by week</div>` + (keys.length
    ? keys.map(w => {
        const { ok, bad } = weeks[w];
        const pct = Math.round(100 * ok / (ok + bad));
        return `<div style="display:flex;align-items:center;gap:10px;margin:7px 0;font:11px var(--mono);color:var(--stone)">` +
          `<span style="width:70px">${w.slice(5)}</span>` +
          `<svg width="100%" height="10" style="flex:1"><rect width="${pct}%" height="10" fill="var(--gold)"/>` +
          `<rect x="${pct}%" width="${100 - pct}%" height="10" fill="var(--mist)"/></svg>` +
          `<span style="width:78px;text-align:right">${pct}% of ${ok + bad}</span></div>`;
      }).join("")
    : `<div class="chart-empty">No reviewed designs yet — approve or reject a few and this fills in.</div>`);

  // styles: top 8 tags by count, with approval share
  const tags = {};
  designs.forEach(d => tagsOf(d).forEach(t => {
    tags[t] = tags[t] || { n: 0, ok: 0, judged: 0 };
    tags[t].n++;
    if (["approved", "published", "rejected"].includes(d.status)) {
      tags[t].judged++;
      if (d.status !== "rejected") tags[t].ok++;
    }
  }));
  const top = Object.entries(tags).sort((a, b) => b[1].n - a[1].n).slice(0, 8);
  const max = top.length ? top[0][1].n : 1;
  const sEl = document.getElementById("chart_styles");
  sEl.innerHTML = `<div class="chart-title">Top styles</div>` + (top.length
    ? top.map(([t, v]) =>
        `<div style="display:flex;align-items:center;gap:10px;margin:7px 0;font:11px var(--mono);color:var(--stone)">` +
        `<span style="width:110px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(t)}">${esc(t)}</span>` +
        `<svg width="100%" height="10" style="flex:1"><rect width="${Math.round(100 * v.n / max)}%" height="10" fill="var(--gold-soft)"/></svg>` +
        `<span style="width:110px;text-align:right">${v.n}${v.judged ? ` · ${Math.round(100 * v.ok / v.judged)}% kept` : ""}</span></div>`)
      .join("")
    : `<div class="chart-empty">Tags appear once you have designs.</div>`);
}

const STAGES = [
  { id: "pending",   name: "To review" },
  { id: "queued",    name: "In press" },
  { id: "approved",  name: "Approved" },
  { id: "published", name: "Published" },
  { id: "failed",    name: "Failed" },
  { id: "rejected",  name: "Rejected" },
];
let tab = "pending", designs = [];
let stat = {}, busy = 0;

async function api(path, opts) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let detail = r.statusText;
    try { detail = (await r.json()).detail || detail; } catch (e) {}
    throw new Error(detail);
  }
  return r.json();
}
// minimal CSV: two columns, handles quoted cells with commas/newlines
function parseCSV(text) {
  const rows = [];
  let cell = "", row = [], q = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (q) {
      if (c === '"' && text[i + 1] === '"') { cell += '"'; i++; }
      else if (c === '"') q = false;
      else cell += c;
    } else if (c === '"') q = true;
    else if (c === ",") { row.push(cell); cell = ""; }
    else if (c === "\n" || c === "\r") {
      if (c === "\r" && text[i + 1] === "\n") i++;
      row.push(cell); cell = "";
      if (row.some(x => x.trim())) rows.push(row);
      row = [];
    } else cell += c;
  }
  row.push(cell);
  if (row.some(x => x.trim())) rows.push(row);
  if (rows.length && rows[0][0].trim().toLowerCase() === "phrase") rows.shift();
  return rows
    .map(r => [(r[0] || "").trim(), (r[1] || "").trim()])
    .filter(([p]) => p);
}

function parseLines(text) {
  return text.split("\n")
    .map(l => l.split("|").map(s => s.trim()))
    .map(([p, f]) => [p || "", f || ""])
    .filter(([p]) => p);
}

function findDuplicates(items) {
  const known = new Set(designs.map(d => d.phrase.trim().toLowerCase()));
  return items.filter(([p]) => known.has(p.trim().toLowerCase()));
}

async function queueItems(items) {
  const dups = findDuplicates(items);
  if (dups.length) {
    const list = dups.slice(0, 5).map(([p]) => `• ${p}`).join("\n");
    const skip = confirm(
      `${dups.length} of these look like designs you already have:\n${list}` +
      (dups.length > 5 ? "\n…" : "") +
      `\n\nOK = skip the duplicates, Cancel = queue everything anyway`);
    if (skip) {
      const dupSet = new Set(dups.map(([p]) => p.trim().toLowerCase()));
      items = items.filter(([p]) => !dupSet.has(p.trim().toLowerCase()));
    }
  }
  if (!items.length) { flash("Nothing new to queue"); return; }
  const text = items.map(([p, f]) => f ? `${p} | ${f}` : p).join("\n");
  await api("/api/generate", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({text})});
  flash(`Queued ${items.length} idea${items.length === 1 ? "" : "s"} (2 variations each)`);
  refresh();
}

async function generate() {
  const items = parseLines(document.getElementById("input").value);
  if (!items.length) return;
  try {
    await queueItems(items);
    document.getElementById("input").value = "";
  } catch (e) { alert(e.message); }
}

document.getElementById("csv_file").addEventListener("change", async (ev) => {
  const file = ev.target.files[0];
  if (!file) return;
  const state = document.getElementById("csv_state");
  try {
    const items = parseCSV(await file.text());
    if (!items.length) { state.textContent = "No ideas found in that file"; return; }
    state.textContent = `${items.length} ideas found`;
    await queueItems(items);
  } catch (e) { state.textContent = "Couldn't read that file: " + e.message; }
  ev.target.value = "";
});
async function saveSettings() {
  const body = {
    gemini_api_key: document.getElementById("gemini_key").value,
    printify_api_token: document.getElementById("printify_token").value,
    printify_shop_id: document.getElementById("printify_shop").value,
  };
  try { await api("/api/settings", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)}); }
  catch (e) { alert(e.message); }
  document.getElementById("gemini_key").value = "";
  document.getElementById("printify_token").value = "";
  refresh();
}
async function removeDesign(btn, id, verb) {
  if (verb === "delete" && !confirm("Delete this design and its image files permanently?")) return;
  btn.disabled = true;
  busy++;
  try { await api(`/api/designs/${id}`, {method: "DELETE"}); }
  catch (e) { alert(e.message); }
  finally { busy--; }
  refresh();
}

async function act(btn, id, action) {
  btn.disabled = true;
  busy++;
  try { await api(`/api/designs/${id}/${action}`, {method: "POST"}); }
  catch (e) { alert(e.message); }
  finally { busy--; }
  refresh();
}
function esc(s) {
  return (s || "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}
const selected = new Set();
function togglePick(id, on) {
  on ? selected.add(id) : selected.delete(id);
  render();
}
function selectAllPending(on) {
  selected.clear();
  if (on) designs.filter(d => d.status === "pending").forEach(d => selected.add(d.id));
  render();
}
async function bulkAct(action) {
  const ids = [...selected];
  selected.clear();
  busy++;
  try { await Promise.all(ids.map(id => api(`/api/designs/${id}/${action}`, {method: "POST"}))); }
  catch (e) { alert(e.message); }
  finally { busy--; }
  flash(`${ids.length} design${ids.length === 1 ? "" : "s"} ${action === "approve" ? "approved" : "rejected"}`);
  refresh();
}

function card(d, i) {
  const generating = d.status === "queued" || d.status === "generating";
  const pick = d.status === "pending"
    ? `<input type="checkbox" class="pick" ${selected.has(d.id) ? "checked" : ""} onclick="togglePick(${d.id}, this.checked)">`
    : "";
  const img = d.file
    ? `<img src="/${d.file}" loading="lazy" alt="${esc(d.phrase)}">`
    : `<div class="placeholder ${generating ? "working" : ""}">${generating ? "in press…" : "no image"}</div>`;
  const buttons = {
    pending: `<button class="gilt" onclick="act(this,${d.id},'approve')">✓ Approve</button><button onclick="act(this,${d.id},'reject')">✕ Reject</button><button onclick="act(this,${d.id},'regenerate')">↻ Regenerate</button>`,
    approved: (stat.printify_ready
        ? `<button class="gilt" onclick="act(this,${d.id},'publish')">Publish to Printify</button>`
        : `<button disabled>Publish to Printify</button><span class="tag">Printify not configured</span>`) +
      (d.print_file ? '<span class="tag ok">print-ready ✓</span>' : '<span class="tag">upscaling…</span>'),
    queued: `<button onclick="removeDesign(this,${d.id},'cancel')">✕ Cancel</button>`,
    failed: `<button onclick="act(this,${d.id},'retry')">↻ Retry</button><button onclick="removeDesign(this,${d.id},'delete')">🗑 Delete</button>`,
    rejected: `<button onclick="act(this,${d.id},'retry')">↻ Re-queue</button><button onclick="act(this,${d.id},'unreview')">↩ Back to review</button><button onclick="removeDesign(this,${d.id},'delete')">🗑 Delete</button>`,
  }[d.status] || "";
  return `<div class="card${selected.has(d.id) ? " selected" : ""}" data-id="${d.id}" style="animation-delay:${Math.min(i * 40, 400)}ms"><div class="frame">${pick}${img}</div><div class="body"><div class="phrase">${esc(d.phrase)}</div>` +
    `<div class="filters">${esc(d.filters)}</div>` +
    (d.error ? `<div class="error">${esc(d.error)}</div>` : "") +
    `</div><div class="actions">${buttons}</div></div>`;
}
function render() {
  renderDashboard();
  renderLibrary();
  const counts = {};
  designs.forEach(d => {
    const t = d.status === "generating" ? "queued" : d.status;
    counts[t] = (counts[t] || 0) + 1;
  });
  document.getElementById("tabs").innerHTML = STAGES.map(s =>
    `<button class="plaque ${s.id === tab ? "active" : ""}" onclick="tab='${s.id}';render()" aria-pressed="${s.id === tab}">` +
    `<span class="name">${s.name}</span><span class="count">${counts[s.id] || 0}</span></button>`).join("");
  const stage = STAGES.find(s => s.id === tab);
  const n = counts[tab] || 0;
  document.getElementById("page_title").textContent = stage.name;
  document.getElementById("page_count").textContent = n === 1 ? "1 design" : `${n} designs`;
  const shown = designs.filter(d => d.status === tab || (tab === "queued" && d.status === "generating"));
  const bulkbar = tab === "pending" && shown.length
    ? `<div id="bulkbar" style="grid-column:1/-1">
         <label class="hint"><input type="checkbox" onclick="selectAllPending(this.checked)" ${selected.size && selected.size === shown.length ? "checked" : ""}> Select all</label>
         ${selected.size ? `<button class="gilt" onclick="bulkAct('approve')">✓ Approve ${selected.size}</button>
         <button onclick="bulkAct('reject')">✕ Reject ${selected.size}</button>` : `<span class="hint">tick designs to act on several at once</span>`}
       </div>`
    : "";
  const legend = `<div id="keylegend" style="grid-column:1/-1"><b>→/←</b> move · <b>A</b> approve · <b>R</b> reject · <b>U</b> undo · <b>space</b> zoom</div>`;
  let cardsHtml;
  if (tab === "pending") {
    const groups = new Map();
    shown.forEach(d => {
      const k = d.phrase + "|" + d.filters;
      if (!groups.has(k)) groups.set(k, []);
      groups.get(k).push(d);
    });
    cardsHtml = [...groups.values()].map(g =>
      g.length > 1
        ? `<div class="vargroup"><div class="vg-head">${esc(g[0].phrase)}<span class="vg-style">${esc(g[0].filters)}</span></div>` +
          `<div class="vg-cards">${g.map(card).join("")}</div></div>`
        : card(g[0], 0)
    ).join("");
  } else {
    cardsHtml = shown.map(card).join("");
  }
  document.getElementById("grid").innerHTML =
    (tab === "pending" && cardsHtml ? legend : "") + bulkbar + (cardsHtml ||
    `<div class="empty"><span class="fleuron">❦</span>Nothing here yet — commission some ideas above.</div>`);
  kbHighlight();
}

let kbIndex = -1, lastAction = null;
function pendingIds() { return designs.filter(d => d.status === "pending").map(d => d.id); }
function kbHighlight() {
  document.querySelectorAll(".card.kbfocus").forEach(c => c.classList.remove("kbfocus"));
  const ids = pendingIds();
  if (kbIndex < 0 || kbIndex >= ids.length) return;
  const el = document.querySelector(`.card[data-id="${ids[kbIndex]}"]`);
  if (el) { el.classList.add("kbfocus"); el.scrollIntoView({block: "nearest", behavior: "smooth"}); }
}
async function kbAct(action) {
  const ids = pendingIds();
  if (kbIndex < 0 || kbIndex >= ids.length) return;
  const id = ids[kbIndex];
  lastAction = { id, action };
  busy++;
  try { await api(`/api/designs/${id}/${action}`, {method: "POST"}); }
  catch (e) { alert(e.message); }
  finally { busy--; }
  flash(`${action === "approve" ? "Approved" : "Rejected"} — press U to undo`, "Undo", undoLast);
  await refresh();
  kbIndex = Math.min(kbIndex, pendingIds().length - 1);
  kbHighlight();
}
async function undoLast() {
  if (!lastAction) return;
  try { await api(`/api/designs/${lastAction.id}/unreview`, {method: "POST"}); flash("Moved back to review"); }
  catch (e) { alert(e.message); }
  lastAction = null;
  refresh();
}
document.addEventListener("keydown", (ev) => {
  if (lbId !== null) return; // lightbox has its own keys
  if (["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) return;
  if (currentView() !== "dashboard" || tab !== "pending") return;
  const ids = pendingIds();
  if (!ids.length) return;
  const k = ev.key.toLowerCase();
  if (k === "arrowright" || k === "j") { kbIndex = Math.min(kbIndex + 1, ids.length - 1); kbHighlight(); }
  else if (k === "arrowleft" || k === "k") { kbIndex = Math.max(kbIndex - 1, 0); kbHighlight(); }
  else if (k === "a") kbAct("approve");
  else if (k === "r") kbAct("reject");
  else if (k === "u") undoLast();
  else if (k === " " && kbIndex >= 0) { ev.preventDefault(); openLightbox(ids[kbIndex]); }
});
const libState = { q: "", statuses: new Set(), tags: new Set(), minRating: 0, from: "", to: "", sort: "new" };
function libReset() {
  Object.assign(libState, { q: "", minRating: 0, from: "", to: "", sort: "new" });
  libState.statuses.clear(); libState.tags.clear();
  document.getElementById("lib_q").value = "";
  document.getElementById("lib_sort").value = "new";
  document.getElementById("lib_minrating").value = "0";
  document.getElementById("lib_from").value = "";
  document.getElementById("lib_to").value = "";
  renderLibrary();
}
["lib_q", "lib_sort", "lib_minrating", "lib_from", "lib_to"].forEach(id =>
  document.getElementById(id).addEventListener("input", () => {
    libState.q = document.getElementById("lib_q").value.trim().toLowerCase();
    libState.sort = document.getElementById("lib_sort").value;
    libState.minRating = +document.getElementById("lib_minrating").value;
    libState.from = document.getElementById("lib_from").value;
    libState.to = document.getElementById("lib_to").value;
    renderLibrary();
  }));
function toggleSet(set, v) { set.has(v) ? set.delete(v) : set.add(v); renderLibrary(); }

function libFiltered() {
  let out = designs.filter(d => {
    if (libState.q && !(d.phrase + " " + d.filters).toLowerCase().includes(libState.q)) return false;
    const st = d.status === "generating" ? "queued" : d.status;
    if (libState.statuses.size && !libState.statuses.has(st)) return false;
    if (libState.tags.size) {
      const t = tagsOf(d);
      for (const need of libState.tags) if (!t.includes(need)) return false;
    }
    if ((d.rating || 0) < libState.minRating) return false;
    const day = (d.created_at || "").slice(0, 10);
    if (libState.from && day < libState.from) return false;
    if (libState.to && day > libState.to) return false;
    return true;
  });
  const by = {
    new: (a, b) => b.id - a.id,
    old: (a, b) => a.id - b.id,
    rating: (a, b) => (b.rating || 0) - (a.rating || 0) || b.id - a.id,
    az: (a, b) => a.phrase.localeCompare(b.phrase),
  }[libState.sort];
  return out.sort(by);
}

function stars(d) {
  let s = "";
  for (let i = 1; i <= 5; i++)
    s += `<span class="${i <= (d.rating || 0) ? "lit" : ""}" onclick="setRating(${d.id},${i})">★</span>`;
  return `<span class="stars" title="click to rate">${s}</span>`;
}
async function setRating(id, n) {
  const d = designs.find(x => x.id === id);
  const rating = d && d.rating === n ? 0 : n; // click current star again to clear
  try {
    await api(`/api/designs/${id}`, {method: "PATCH", headers: {"Content-Type": "application/json"}, body: JSON.stringify({rating})});
    if (d) d.rating = rating;
    renderLibrary();
  } catch (e) { alert(e.message); }
}

function libCard(d) {
  const st = d.status === "generating" ? "queued" : d.status;
  const img = d.file
    ? `<img src="/${d.file}" loading="lazy" alt="${esc(d.phrase)}" onclick="openLightbox(${d.id})" style="cursor:zoom-in">`
    : `<div class="placeholder">no image</div>`;
  return `<div class="card"><div class="frame">${img}</div><div class="body">` +
    `<div class="phrase">${esc(d.phrase)}</div>` +
    `<div class="filters">${tagsOf(d).map(t => `<span class="chip" onclick="toggleSet(libState.tags,'${esc(t)}')">${esc(t)}</span>`).join("")}</div>` +
    `</div><div class="actions" style="justify-content:space-between">` +
    `${stars(d)}<span class="status-chip">${st}</span></div></div>`;
}

function renderLibrary() {
  if (currentView() !== "library") return;
  const stages = ["pending", "queued", "approved", "published", "failed", "rejected"];
  document.getElementById("lib_status").innerHTML = stages.map(s =>
    `<button class="chip ${libState.statuses.has(s) ? "on" : ""}" onclick="toggleSet(libState.statuses,'${s}')">${s}</button>`).join("");
  const allTags = [...new Set(designs.flatMap(tagsOf))].sort();
  document.getElementById("lib_tags").innerHTML = allTags.slice(0, 30).map(t =>
    `<button class="chip ${libState.tags.has(t) ? "on" : ""}" onclick="toggleSet(libState.tags,'${esc(t)}')">${esc(t)}</button>`).join("") ||
    `<span class="hint">tags appear as you make designs</span>`;
  const rows = libFiltered();
  document.getElementById("lib_count").textContent = rows.length === 1 ? "1 design" : `${rows.length} designs`;
  document.getElementById("lib_grid").innerHTML = rows.map(libCard).join("") ||
    `<div class="empty"><span class="fleuron">❦</span>No designs match — clear a filter or two.</div>`;
}
let lbId = null;
function openLightbox(id) { lbId = id; renderLightbox(); }
function closeLightbox() { lbId = null; document.getElementById("lightbox").hidden = true; }
function lbMove(dir) {
  const rows = libFiltered().filter(d => d.file);
  const i = rows.findIndex(d => d.id === lbId);
  const next = rows[i + dir];
  if (next) { lbId = next.id; renderLightbox(); }
}
async function saveTags(id) {
  try {
    await api(`/api/designs/${id}`, {method: "PATCH", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({tags: document.getElementById("lb_tags").value})});
    const d = designs.find(x => x.id === id);
    if (d) d.tags = document.getElementById("lb_tags").value;
    flash("Tags saved");
    renderLibrary();
  } catch (e) { alert(e.message); }
}
function renderLightbox() {
  const d = designs.find(x => x.id === lbId);
  if (!d) { closeLightbox(); return; }
  const st = d.status === "generating" ? "queued" : d.status;
  const actions = {
    pending: `<button class="gilt" onclick="act(this,${d.id},'approve');closeLightbox()">✓ Approve</button>
              <button onclick="act(this,${d.id},'reject');closeLightbox()">✕ Reject</button>`,
    approved: `<button onclick="act(this,${d.id},'unreview');closeLightbox()">↩ Back to review</button>`,
    rejected: `<button onclick="act(this,${d.id},'unreview');closeLightbox()">↩ Back to review</button>`,
    failed: `<button onclick="act(this,${d.id},'retry');closeLightbox()">↻ Retry</button>`,
  }[st] || "";
  document.getElementById("lightbox_inner").innerHTML =
    `<div>${d.file ? `<img src="/${d.file}" alt="${esc(d.phrase)}">` : `<div class="placeholder">no image</div>`}</div>` +
    `<div><h3>${esc(d.phrase)}</h3>` +
    `<div class="lb-row"><span class="status-chip">${st}</span> · ${(d.created_at || "").slice(0, 10)}</div>` +
    `<div class="lb-row">${stars(d)}</div>` +
    `<div class="lb-row">Style: ${esc(d.filters) || "—"}</div>` +
    `<div class="lb-row">Your tags<br><input type="text" id="lb_tags" value="${esc(d.tags || "")}" placeholder="comma, separated"> ` +
    `<button style="margin-top:6px" onclick="saveTags(${d.id})">Save tags</button></div>` +
    (d.error ? `<div class="lb-row" style="color:var(--clay)">${esc(d.error)}</div>` : "") +
    `<div class="lb-row">` +
    (d.print_file ? `<a href="/${d.print_file}" download><button>Download print file</button></a> ` : "") +
    (d.product_id ? `<a href="https://printify.com/app/products/${encodeURIComponent(d.product_id)}" target="_blank" rel="noopener"><button>Open in Printify</button></a>` : "") +
    `</div><div class="lb-row">${actions}</div></div>`;
  document.getElementById("lightbox").hidden = false;
}
document.addEventListener("keydown", (ev) => {
  if (lbId === null) return;
  if (ev.key === "Escape") closeLightbox();
  if (ev.key === "ArrowRight") lbMove(1);
  if (ev.key === "ArrowLeft") lbMove(-1);
});
async function refresh() {
  if (busy) return;
  try {
    const [status, list] = await Promise.all([api("/api/status"), api("/api/designs")]);
    stat = status;
    designs = list;
    [...selected].forEach(id => { const d = designs.find(x => x.id === id); if (!d || d.status !== "pending") selected.delete(id); });
    document.getElementById("status_text").textContent = status.local
      ? `local GPU · ${status.queued} in press`
      : `today: ${status.today}/${status.cap} images · ${status.queued} in press` +
        (status.has_key ? "" : " · ⚠ no API key") +
        (status.paused ? " · daily cap reached — resumes tomorrow" : "");
    document.querySelector("#statusbar .dot").classList.toggle("live", status.queued > 0);
    document.getElementById("key_state").textContent = status.has_key ? "key saved ✓" : "no key saved";
    render();
    const pending = designs.filter(d => d.status === "pending").length;
    document.title = (pending ? `(${pending}) ` : "") + "The Atelier — T-Shirt Design House";
    document.getElementById("badge_pending").textContent = pending || "";
  } catch (e) { document.getElementById("status_text").textContent = "server unreachable"; }
}
let toastTimer;
function flash(msg, actionLabel, action) {
  const t = document.getElementById("toast");
  t.innerHTML = esc(msg) + (actionLabel ? ` <button onclick="toastAction()">${esc(actionLabel)}</button>` : "");
  window.toastAction = action || null;
  t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, 5000);
}

let promptSaveTimer, promptLoaded = false;
async function loadPrompt() {
  try {
    const s = await api("/api/settings");
    document.getElementById("prompt_box").value = s.prompt_template;
    promptLoaded = true;
  } catch (e) {}
}
document.getElementById("prompt_box").addEventListener("input", () => {
  if (!promptLoaded) return;
  clearTimeout(promptSaveTimer);
  promptSaveTimer = setTimeout(async () => {
    try {
      await api("/api/settings", {method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({prompt_template: document.getElementById("prompt_box").value})});
    } catch (e) { flash("Couldn't save the prompt — " + e.message); }
  }, 600);
});
async function copyPrompt() {
  const el = document.getElementById("prompt_box");
  try { await navigator.clipboard.writeText(el.value); flash("Prompt copied"); }
  catch (e) { el.focus(); el.select(); flash("Press ⌘C to copy"); }
}
loadPrompt();

showView();
refresh();
setInterval(refresh, 3000);
