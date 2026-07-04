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
function renderCharts() {} // filled in Task 8

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
async function generate() {
  const text = document.getElementById("input").value;
  if (!text.trim()) return;
  try {
    await api("/api/generate", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({text})});
    document.getElementById("input").value = "";
  } catch (e) { alert(e.message); }
  refresh();
}
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
showView();
refresh();
setInterval(refresh, 3000);
