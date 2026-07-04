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
function card(d, i) {
  const generating = d.status === "queued" || d.status === "generating";
  const img = d.file
    ? `<img src="/${d.file}" loading="lazy" alt="${esc(d.phrase)}">`
    : `<div class="placeholder ${generating ? "working" : ""}">${generating ? "in press…" : "no image"}</div>`;
  const buttons = {
    pending: `<button class="gilt" onclick="act(this,${d.id},'approve')">✓ Approve</button><button onclick="act(this,${d.id},'reject')">✕ Reject</button><button onclick="act(this,${d.id},'regenerate')">↻ Regenerate</button>`,
    approved: (stat.printify_ready
        ? `<button class="gilt" onclick="act(this,${d.id},'publish')">Publish to Printify</button>`
        : `<button disabled>Publish to Printify</button><span class="tag">Printify not configured</span>`) +
      (d.print_file ? '<span class="tag ok">print-ready ✓</span>' : '<span class="tag">upscaling…</span>'),
    failed: `<button onclick="act(this,${d.id},'retry')">↻ Retry</button>`,
    rejected: `<button onclick="act(this,${d.id},'retry')">↻ Re-queue</button>`,
  }[d.status] || "";
  return `<div class="card" style="animation-delay:${Math.min(i * 40, 400)}ms"><div class="frame">${img}</div><div class="body"><div class="phrase">${esc(d.phrase)}</div>` +
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
  document.getElementById("grid").innerHTML = shown.map(card).join("") ||
    `<div class="empty"><span class="fleuron">❦</span>Nothing here yet — commission some ideas above.</div>`;
}
function renderLibrary() {
  document.getElementById("lib_count").textContent =
    designs.length === 1 ? "1 design" : `${designs.length} designs`;
  document.getElementById("lib_grid").innerHTML =
    designs.map(card).join("") ||
    `<div class="empty"><span class="fleuron">❦</span>Nothing here yet.</div>`;
}
async function refresh() {
  if (busy) return;
  try {
    const [status, list] = await Promise.all([api("/api/status"), api("/api/designs")]);
    stat = status;
    designs = list;
    document.getElementById("status_text").textContent = status.local
      ? `local GPU · ${status.queued} in press`
      : `today: ${status.today}/${status.cap} images · ${status.queued} in press` +
        (status.has_key ? "" : " · ⚠ no API key") +
        (status.paused ? " · daily cap reached — resumes tomorrow" : "");
    document.querySelector("#statusbar .dot").classList.toggle("live", status.queued > 0);
    document.getElementById("key_state").textContent = status.has_key ? "key saved ✓" : "no key saved";
    render();
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
