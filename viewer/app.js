/* Harness History viewer — static, read-only. Served by harness-history.sh with
 * the history root as docroot; the viewer lives at /_viewer/ and fetches data
 * with root-absolute URLs (/index.json, /<slug>/runs/<run>/events.ndjson). */

const $ = (sel, el = document) => el.querySelector(sel);
const el = (tag, cls, txt) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (txt != null) e.textContent = txt;
  return e;
};

// ---- event-kind presentation -------------------------------------------------
const KIND = {
  run_start:   { glyph: "●", color: "#4ea1ff", label: "run start", life: true },
  run_end:     { glyph: "■", color: "#8b96a5", label: "run end", life: true },
  agent_start: { glyph: "▸", color: "#3fb950", label: "agent", life: true },
  agent_stop:  { glyph: "◂", color: "#8b96a5", label: "agent end", life: true },
  tool:        { glyph: "⚙", color: "#56d4dd", label: "tool" },
  error:       { glyph: "✖", color: "#f85149", label: "error" },
  task_move:   { glyph: "↪", color: "#bc8cff", label: "task move" },
  qa_verdict:  { glyph: "❖", color: "#d29922", label: "QA verdict" },
  git:         { glyph: "⎇", color: "#56d4dd", label: "git" },
  state:       { glyph: "◷", color: "#8b96a5", label: "cycle" },
};
const ORDER = ["agent_start", "agent_stop", "tool", "error", "task_move", "qa_verdict", "git", "state", "run_start", "run_end"];

// ---- help dialog content (legend glyphs/colors come from KIND, so they never drift) ----
const KIND_HELP = [
  ["run_start",   "the harness launch began — one run = one harness-up → teardown"],
  ["agent_start", "a harness agent started: orchestrator, worker, QA, or fixer"],
  ["tool",        "a tool call by an agent — its name + a short summary. Click the row for the transcript ref"],
  ["error",       "a tool call returned an error — your first stop for “what broke”"],
  ["task_move",   "a task changed state: backlog → in-progress → review → done"],
  ["qa_verdict",  "behavioral QA’s call on a task: looks-good / needs-changes / escalate"],
  ["git",         "a git event: commit, branch, merge, or worktree add/remove"],
  ["state",       "an orchestrator cycle tick — a snapshot of the last action + active agents"],
  ["agent_stop",  "an agent finished, or was reaped by the orchestrator at a handoff"],
  ["run_end",     "the harness was stopped and the run was torn down"],
];
const ROLE_HELP = [
  ["orchestrator", "the control loop — moves tasks, spawns agents, merges PRs"],
  ["worker",       "implements one task, commits, opens the PR"],
  ["qa",           "drives the feature behaviorally and renders a verdict"],
  ["fixer",        "Opus fix pass after a needs-changes verdict"],
];

// ---- fetch helpers -----------------------------------------------------------
async function fetchJSON(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error(`${r.status} ${url}`);
  return r.json();
}
async function fetchNDJSON(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error(`${r.status} ${url}`);
  const text = await r.text();
  const out = [];
  for (const line of text.split("\n")) {
    const t = line.trim();
    if (!t) continue;
    try { out.push(JSON.parse(t)); } catch { /* skip half-written tail */ }
  }
  return out;
}

// ---- time formatting ---------------------------------------------------------
const hhmmss = (iso) => {
  if (!iso) return "";
  const d = new Date(iso);
  return isNaN(d) ? "" : d.toTimeString().slice(0, 8);
};
const dateStr = (iso) => {
  if (!iso) return "—";
  const d = new Date(iso);
  return isNaN(d) ? iso : d.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
};
function durStr(a, b) {
  if (!a || !b) return "";
  const ms = new Date(b) - new Date(a);
  if (isNaN(ms) || ms < 0) return "";
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

// ---- routing -----------------------------------------------------------------
window.addEventListener("hashchange", route);
window.addEventListener("DOMContentLoaded", () => {
  $("#brand").onclick = () => (location.hash = "");
  $("#refresh").onclick = () => route(true);
  $("#help").onclick = openHelp;
  route();
});
window.addEventListener("keydown", (e) => { if (e.key === "Escape") closeHelp(); });

// ---- help dialog ----
function closeHelp() { const m = $("#help-modal"); if (m) m.remove(); }
function openHelp() {
  if ($("#help-modal")) return;
  const back = el("div", "modal-backdrop");
  back.id = "help-modal";
  back.onclick = (e) => { if (e.target === back) closeHelp(); };
  const legend = KIND_HELP.map(([k, d]) => {
    const m = KIND[k] || { glyph: "•", color: "#8b96a5", label: k };
    return `<div class="g" style="color:${m.color}">${m.glyph}</div>` +
           `<div class="d"><b>${m.label || k}</b> — ${d}</div>`;
  }).join("");
  const roles = ROLE_HELP.map(([r, d]) =>
    `<div><span class="role ${r}">${r}</span></div><div class="d">${d}</div>`).join("");
  back.innerHTML = `
    <div class="modal" role="dialog" aria-modal="true">
      <div class="modal-head">
        <h2>How to read these logs</h2>
        <button class="close" title="Close (Esc)">×</button>
      </div>
      <p>Each <b>run</b> is one harness launch (<code>harness-up.sh</code> → teardown). The
      timeline is every event in that run, in order — recorded mechanically by tailing each
      harness agent's transcript, diffing the task files, and polling git. No agent writes these
      logs, so they're a faithful record of what actually happened.</p>

      <h3>Event icons</h3>
      <div class="legend">${legend}</div>

      <h3>Agent roles (badge colours)</h3>
      <div class="roles">${roles}</div>

      <h3>Filtering &amp; drill-down</h3>
      <ul>
        <li><b>Sidebar → Event types</b>: click to toggle a kind on/off.</li>
        <li><b>Sidebar → Agents</b>: click an agent to isolate just its events.</li>
        <li><b>Search</b>: matches tool name, summary, task, or feature.</li>
        <li><b>errors only</b>: jump straight to failed tool calls.</li>
        <li><b>Click a tool / error row</b> → reveals the <code>transcript</code> path + message id.
        Open that <code>.jsonl</code> to read the full tool input/output — the timeline stores
        summaries, not full IO.</li>
      </ul>

      <h3>Debugging recipes</h3>
      <ul>
        <li><b>“What broke?”</b> → <b>errors only</b>, read the error, then open its <code>ref</code>.</li>
        <li><b>“What did the orchestrator decide?”</b> → filter to the
        <span class="role orchestrator">orchestrator</span> — its tool calls <em>are</em> the task
        moves, spawns, and merges.</li>
        <li><b>“Why did QA fail a task?”</b> → filter to that task's
        <span class="role qa">qa</span> agent and read up to its <b>QA verdict</b>.</li>
        <li><b>“A task got stuck”</b> → follow its <b>task move</b> events; one that entered
        <code>in-progress</code> but never reached <code>review</code> means the worker stalled or crashed.</li>
      </ul>

      <h3>Good to know</h3>
      <ul>
        <li>Only <b>harness-spawned</b> agents are recorded — an ad-hoc <code>claude</code> you open by hand is ignored.</li>
        <li>Depth is <b>events + tool calls</b> (plus errors), not full reasoning or tool IO.</li>
        <li>Data lives at <code>~/.agent-harness-history/&lt;project&gt;/runs/&lt;run-id&gt;/events.ndjson</code> — plain JSON, greppable.</li>
        <li>A run still marked <code>running</code> after you stopped it just means the teardown
        marker didn't land (best-effort) — harmless.</li>
      </ul>
    </div>`;
  back.querySelector(".close").onclick = closeHelp;
  document.body.appendChild(back);
}

function setCrumbs(parts) {
  const nav = $("#crumbs");
  nav.innerHTML = "";
  parts.forEach((p, i) => {
    if (i) { const s = el("span", "sep", "›"); nav.appendChild(s); }
    if (p.hash != null) {
      const a = el("a", null, p.label);
      a.onclick = () => (location.hash = p.hash);
      nav.appendChild(a);
    } else nav.appendChild(el("span", null, p.label));
  });
}

async function route() {
  const app = $("#app");
  const h = decodeURIComponent(location.hash.replace(/^#/, ""));
  app.innerHTML = `<div class="loading">Loading…</div>`;
  try {
    if (!h) return renderLanding(await fetchJSON("/index.json"));
    const [slug, run] = h.split("/");
    return renderRun(slug, run);
  } catch (e) {
    app.innerHTML = `<div class="empty">Could not load data.<br><span class="muted">${e.message}</span><br><br>` +
      `Make sure you opened this via <code>harness-history.sh</code> (it serves the history dir and builds the index).</div>`;
  }
}

// ---- landing -----------------------------------------------------------------
function renderLanding(index) {
  setCrumbs([{ label: "projects" }]);
  const app = $("#app");
  app.innerHTML = "";
  const projects = index.projects || [];
  if (!projects.length) {
    app.innerHTML = `<div class="empty">No runs recorded yet.<br><span class="muted">Launch the harness with <code>harness-up.sh</code> and a run will appear here.</span></div>`;
    return;
  }
  app.appendChild(el("div", "section-title", `${projects.length} project${projects.length > 1 ? "s" : ""}`));
  for (const p of projects) {
    const wrap = el("div", "project");
    wrap.appendChild(el("h2", null, p.name || p.slug));
    wrap.appendChild(el("div", "path", p.path || ""));
    const runs = el("div", "runs");
    for (const r of (p.runs || [])) runs.appendChild(runCard(p, r));
    wrap.appendChild(runs);
    app.appendChild(wrap);
  }
}

function runCard(project, r) {
  const card = el("div", "run-card");
  card.onclick = () => (location.hash = `${project.slug}/${r.run}`);
  const top = el("div", "rid-row");
  top.style.display = "flex"; top.style.justifyContent = "space-between"; top.style.alignItems = "center";
  top.appendChild(el("span", "rid", r.run));
  top.appendChild(el("span", `status ${r.status || "ended"}`, r.status || "ended"));
  card.appendChild(top);
  const dur = durStr(r.started, r.ended || r.last_ts);
  card.appendChild(el("div", "when", `${dateStr(r.started)}${dur ? " · " + dur : ""}`));
  const stats = el("div", "stats");
  const pill = (label, val, cls) => {
    const e = el("span", "pill" + (cls ? " " + cls : ""));
    e.appendChild(el("b", null, String(val))); e.appendChild(document.createTextNode(" " + label));
    return e;
  };
  stats.appendChild(pill("events", r.events ?? 0));
  stats.appendChild(pill("agents", r.agents ?? 0));
  if (r.errors) stats.appendChild(pill("errors", r.errors, "err"));
  if (r.counts && r.counts.tool) stats.appendChild(pill("tools", r.counts.tool));
  card.appendChild(stats);
  if (r.features && r.features.length) {
    const chips = el("div", "chips");
    r.features.forEach((f) => chips.appendChild(el("span", "chip", f)));
    card.appendChild(chips);
  }
  return card;
}

// ---- run view ----------------------------------------------------------------
const view = { slug: null, run: null, meta: null, events: [], agents: new Map(),
  filt: { kinds: new Set(), agent: null, feature: null, q: "", errorsOnly: false }, expanded: new Set() };

async function renderRun(slug, run) {
  view.slug = slug; view.run = run;
  let meta = {};
  try { meta = await fetchJSON(`/${slug}/runs/${run}/run.json`); } catch { /* optional */ }
  view.meta = meta;
  view.events = await fetchNDJSON(`/${slug}/runs/${run}/events.ndjson`);

  // derive agents + reset filters
  view.agents = new Map();
  for (const ev of view.events) {
    if (!ev.session) continue;
    if (!view.agents.has(ev.session))
      view.agents.set(ev.session, { session: ev.session, role: ev.role, task: ev.task, feature: ev.feature, model: ev.model, n: 0 });
    const a = view.agents.get(ev.session);
    a.n++;
    if (ev.role && !a.role) a.role = ev.role;
    if (ev.task && !a.task) a.task = ev.task;
  }
  view.filt = { kinds: new Set(), agent: null, feature: null, q: "", errorsOnly: false };
  view.expanded = new Set();

  setCrumbs([{ label: "projects", hash: "" }, { label: meta.project_name || slug, hash: "" }, { label: run }]);
  drawRun();
}

function drawRun() {
  const app = $("#app");
  app.innerHTML = "";
  const m = view.meta || {};

  // header
  const head = el("div", "runhead");
  head.appendChild(el("h1", null, view.run));
  const meta = el("div", "meta");
  const dur = durStr(m.started, m.ended);
  meta.appendChild(el("span", null, `📁 ${m.project_name || view.slug}`));
  meta.appendChild(el("span", null, `🕐 ${dateStr(m.started)}`));
  if (dur) meta.appendChild(el("span", null, `⏱ ${dur}`));
  if (m.interval) meta.appendChild(el("span", null, `loop ${m.interval}`));
  const st = el("span", `status ${m.status || "ended"}`, m.status || "ended");
  meta.appendChild(st);
  head.appendChild(meta);
  app.appendChild(head);

  // layout: sidebar + timeline
  const layout = el("div", "layout");
  layout.appendChild(buildSidebar());
  const right = el("div");
  right.appendChild(buildToolbar());
  const tl = el("div", "timeline");
  tl.id = "timeline";
  right.appendChild(tl);
  layout.appendChild(right);
  app.appendChild(layout);

  renderTimeline();
}

function kindCounts() {
  const c = {};
  for (const ev of view.events) c[ev.kind] = (c[ev.kind] || 0) + 1;
  return c;
}

function buildSidebar() {
  const side = el("div", "sidebar");
  // event types
  const counts = kindCounts();
  const box1 = el("div", "box");
  box1.appendChild(el("h3", null, "Event types"));
  for (const k of ORDER) {
    if (!counts[k]) continue;
    const meta = KIND[k] || { glyph: "•", color: "#8b96a5", label: k };
    const row = el("div", "filter-row" + (view.filt.kinds.size && !view.filt.kinds.has(k) ? " off" : ""));
    const dot = el("span", "dot"); dot.style.background = meta.color;
    row.appendChild(dot);
    row.appendChild(el("span", null, meta.label));
    row.appendChild(el("span", "cnt", String(counts[k])));
    row.onclick = () => {
      const s = view.filt.kinds;
      if (s.has(k)) s.delete(k); else if (s.size) s.add(k); else { ORDER.forEach(x => counts[x] && s.add(x)); s.delete(k); }
      drawRun();
    };
    box1.appendChild(row);
  }
  side.appendChild(box1);

  // agents
  if (view.agents.size) {
    const box2 = el("div", "box");
    box2.appendChild(el("h3", null, `Agents (${view.agents.size})`));
    const allRow = el("div", "filter-row" + (view.filt.agent ? " off" : ""));
    allRow.appendChild(el("span", null, "all agents"));
    allRow.onclick = () => { view.filt.agent = null; drawRun(); };
    box2.appendChild(allRow);
    for (const a of view.agents.values()) {
      const row = el("div", "filter-row" + (view.filt.agent && view.filt.agent !== a.session ? " off" : ""));
      const r = el("span", "role " + (a.role || ""), a.role || "?");
      row.appendChild(r);
      row.appendChild(el("span", null, a.task || a.session.slice(0, 8)));
      row.appendChild(el("span", "cnt", String(a.n)));
      row.onclick = () => { view.filt.agent = view.filt.agent === a.session ? null : a.session; drawRun(); };
      box2.appendChild(row);
    }
    side.appendChild(box2);
  }
  return side;
}

function buildToolbar() {
  const bar = el("div", "toolbar");
  const search = el("input");
  search.type = "search"; search.placeholder = "Filter by tool, summary, task…";
  search.value = view.filt.q;
  search.oninput = () => { view.filt.q = search.value.toLowerCase(); renderTimeline(); };
  bar.appendChild(search);
  const lbl = el("label");
  const cb = el("input"); cb.type = "checkbox"; cb.checked = view.filt.errorsOnly;
  cb.onchange = () => { view.filt.errorsOnly = cb.checked; renderTimeline(); };
  lbl.appendChild(cb); lbl.appendChild(document.createTextNode("errors only"));
  bar.appendChild(lbl);
  return bar;
}

function passes(ev) {
  const f = view.filt;
  if (f.errorsOnly && ev.kind !== "error") return false;
  if (f.kinds.size && !f.kinds.has(ev.kind)) return false;
  if (f.agent && ev.session !== f.agent) return false;
  if (f.q) {
    const hay = `${ev.kind} ${ev.tool || ""} ${ev.summary || ""} ${ev.task || ""} ${ev.feature || ""} ${ev.action || ""} ${ev.verdict || ""} ${ev.role || ""}`.toLowerCase();
    if (!hay.includes(f.q)) return false;
  }
  return true;
}

function renderTimeline() {
  const tl = $("#timeline");
  if (!tl) return;
  tl.innerHTML = "";
  const rows = view.events.filter(passes);
  if (!rows.length) { tl.appendChild(el("div", "empty", "No events match the current filters.")); return; }
  for (const ev of rows) tl.appendChild(renderEvent(ev));
}

function mainLine(ev) {
  // returns {tool, summary} text for the body line, per kind
  switch (ev.kind) {
    case "tool":  return { tool: ev.tool || "tool", summary: ev.summary || "" };
    case "error": return { tool: (ev.tool ? ev.tool + " " : "") + "error", summary: ev.summary || "" };
    case "task_move": return { tool: ev.task, summary: `${ev.from || "—"} → ${ev.to}${ev.status ? "  (" + ev.status + ")" : ""}` };
    case "qa_verdict": return { tool: ev.task, summary: `QA: ${ev.verdict}` };
    case "git": return { tool: "git " + (ev.action || ""), summary: [ev.sha, ev.branch, ev.summary].filter(Boolean).join("  ") };
    case "state": return { tool: "cycle", summary: `${ev.last_action || ""}${ev.active && ev.active.length ? "  · active: " + ev.active.map(w => w.task + (w.role ? ":" + w.role : "")).join(", ") : ""}` };
    case "agent_start": return { tool: `▸ ${ev.role || "agent"} started`, summary: ev.task || "" };
    case "agent_stop": return { tool: `◂ ${ev.role || "agent"} ended`, summary: ev.task || "" };
    case "run_start": return { tool: "run started", summary: ev.interval ? "loop " + ev.interval : "" };
    case "run_end": return { tool: "run ended", summary: ev.reason || "" };
    default: return { tool: ev.kind, summary: ev.summary || "" };
  }
}

function renderEvent(ev) {
  const meta = KIND[ev.kind] || { glyph: "•", color: "#8b96a5" };
  const life = meta.life;
  const row = el("div", "evt " + ev.kind + (life ? " lifecycle" : "") + (ev.ref ? " has-ref" : ""));
  row.appendChild(el("div", "time", hhmmss(ev.ts)));
  const gut = el("div", "gut", meta.glyph); gut.style.color = meta.color;
  row.appendChild(gut);
  const body = el("div", "body");
  const line = el("div", "line");
  if (ev.role && !life) line.appendChild(el("span", "role " + ev.role, ev.role));
  if (ev.task && ev.kind === "tool") line.appendChild(el("span", "tag", ev.task));
  const ml = mainLine(ev);
  if (ml.tool) line.appendChild(el("span", "tool", ml.tool));
  if (ml.summary) {
    const exp = view.expanded.has(ev.seq);
    line.appendChild(el("span", "summary" + (exp ? " full" : ""), ml.summary));
  }
  if (ev.feature && (ev.kind === "task_move" || ev.kind === "qa_verdict" || ev.kind === "agent_start"))
    line.appendChild(el("span", "tag", ev.feature));
  body.appendChild(line);

  if (ev.ref) {
    row.onclick = () => {
      if (view.expanded.has(ev.seq)) view.expanded.delete(ev.seq); else view.expanded.add(ev.seq);
      renderTimeline();
    };
    if (view.expanded.has(ev.seq)) {
      const ref = el("div", "ref");
      ref.innerHTML = `transcript: <code>${ev.ref.transcript || ""}</code>` + (ev.ref.uuid ? `<br>message: <code>${ev.ref.uuid}</code>` : "");
      body.appendChild(ref);
    }
  }
  row.appendChild(body);
  return row;
}
