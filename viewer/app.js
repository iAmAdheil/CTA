/* Harness History viewer — static, read-only. Served by harness-history.sh with
 * the history root as docroot; the viewer lives at /_viewer/ and fetches data
 * with root-absolute URLs (/index.json, /<slug>/runs/<run>/summary.json, …).
 *
 * Three layers feed three views:
 *   index.json    -> landing run cards (outcome headline)
 *   summary.json  -> Overview + Tasks (derived rollup: cost, time, lifecycle)
 *   events.ndjson -> Timeline (raw, chronological)
 */

const $ = (sel, el = document) => el.querySelector(sel);
const el = (tag, cls, txt) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (txt != null) e.textContent = txt;
  return e;
};

// ---- minimal, safe inline markdown (agent notes) ----------------------------
// Escapes HTML first, then renders code spans, bold, italic, links, bare URLs,
// and newlines. Deliberately small — notes are short prose, not documents.
function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
function md(text) {
  let s = escapeHtml(text);
  s = s.replace(/`([^`]+)`/g, (_, c) => `<code>${c}</code>`);
  s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  s = s.replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener">$2</a>');
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^*\w])\*([^*\n]+)\*(?!\w)/g, "$1<em>$2</em>");
  s = s.replace(/\n/g, "<br>");
  return s;
}

// ---- event-kind presentation (timeline) -------------------------------------
const KIND = {
  run_start:   { glyph: "●", color: "#4ea1ff", label: "run start", life: true },
  run_end:     { glyph: "■", color: "#8b96a5", label: "run end", life: true },
  agent_start: { glyph: "▸", color: "#3fb950", label: "agent", life: true },
  agent_stop:  { glyph: "◂", color: "#8b96a5", label: "agent end", life: true },
  tool:        { glyph: "⚙", color: "#56d4dd", label: "tool" },
  error:       { glyph: "✖", color: "#f85149", label: "error" },
  problem:     { glyph: "⚠", color: "#d29922", label: "problem" },
  task_move:   { glyph: "↪", color: "#bc8cff", label: "task move" },
  qa_verdict:  { glyph: "❖", color: "#d29922", label: "QA verdict" },
  git:         { glyph: "⎇", color: "#56d4dd", label: "git" },
  state:       { glyph: "◷", color: "#8b96a5", label: "cycle" },
};
const ORDER = ["agent_start", "agent_stop", "tool", "problem", "error", "task_move",
  "qa_verdict", "git", "state", "run_start", "run_end"];

// ---- fetch helpers ----------------------------------------------------------
async function fetchJSON(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error(`${r.status} ${url}`);
  return r.json();
}
async function fetchJSONopt(url) { try { return await fetchJSON(url); } catch { return null; } }
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

// ---- formatting -------------------------------------------------------------
const hhmmss = (iso) => { if (!iso) return ""; const d = new Date(iso); return isNaN(d) ? "" : d.toTimeString().slice(0, 8); };
const dateStr = (iso) => { if (!iso) return "—"; const d = new Date(iso); return isNaN(d) ? iso : d.toLocaleString([], { dateStyle: "medium", timeStyle: "short" }); };
function durS(s) {
  if (s == null || isNaN(s)) return "";
  s = Math.round(s);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}
function money(v) { return v == null ? "—" : "$" + (v < 10 ? v.toFixed(2) : Math.round(v).toLocaleString()); }
function moneyExact(v) { return v == null ? "—" : "$" + v.toFixed(2); }
function tokens(n) {
  if (!n) return "0";
  if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(0) + "k";
  return String(n);
}
const ROLE_COLOR = { orchestrator: "#4ea1ff", worker: "#3fb950", qa: "#d29922", fixer: "#bc8cff" };

// ---- routing ----------------------------------------------------------------
window.addEventListener("hashchange", route);
window.addEventListener("DOMContentLoaded", () => {
  $("#brand").onclick = () => (location.hash = "");
  $("#refresh").onclick = () => route();
  $("#help").onclick = openHelp;
  route();
});
window.addEventListener("keydown", (e) => { if (e.key === "Escape") closeHelp(); });

function setCrumbs(parts) {
  const nav = $("#crumbs"); nav.innerHTML = "";
  parts.forEach((p, i) => {
    if (i) nav.appendChild(el("span", "sep", "›"));
    if (p.hash != null) { const a = el("a", null, p.label); a.onclick = () => (location.hash = p.hash); nav.appendChild(a); }
    else nav.appendChild(el("span", null, p.label));
  });
}

async function route() {
  const app = $("#app");
  const h = decodeURIComponent(location.hash.replace(/^#/, ""));
  app.innerHTML = `<div class="loading">Loading…</div>`;
  try {
    if (!h) return renderLanding(await fetchJSON("/index.json"));
    const [slug, run, tab] = h.split("/");
    if (slug && run) return renderRun(slug, run, tab || "overview");
    return renderLanding(await fetchJSON("/index.json"));
  } catch (e) {
    app.innerHTML = `<div class="empty">Could not load data.<br><span class="muted">${e.message}</span><br><br>` +
      `Make sure you opened this via <code>harness-history.sh</code> (it serves the history dir and builds the index).</div>`;
  }
}

// ---- landing ----------------------------------------------------------------
function renderLanding(index) {
  setCrumbs([{ label: "projects" }]);
  const app = $("#app"); app.innerHTML = "";
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

function shippedBadge(o) {
  if (!o || !o.tasks_total) return el("span", "badge", "no tasks");
  const done = `${o.tasks_done}/${o.tasks_total}`;
  if (o.shipped) return el("span", "badge ok", `✓ shipped ${done}`);
  return el("span", "badge bad", `▢ ${done} done`);
}

function runCard(project, r) {
  const card = el("div", "run-card");
  card.onclick = () => (location.hash = `${project.slug}/${r.run}`);
  const top = el("div", "rid-row");
  top.appendChild(el("span", "rid", r.run));
  top.appendChild(el("span", `status ${r.status || "ended"}`, r.status || "ended"));
  card.appendChild(top);

  const head = el("div", "headline");
  head.appendChild(shippedBadge(r.outcome));
  if (r.cost_usd != null) head.appendChild(el("span", "badge", money(r.cost_usd)));
  if (r.problems) head.appendChild(el("span", "badge err", `⚠ ${r.problems}`));
  card.appendChild(head);

  const wall = r.wall_s != null ? durS(r.wall_s) : "";
  const act = r.active_s != null ? ` · ${durS(r.active_s)} active` : "";
  card.appendChild(el("div", "when", `${dateStr(r.started)}${wall ? " · " + wall + act : ""}`));

  const stats = el("div", "stats");
  const stat = (label, val, cls) => {
    const e = el("span", "stat" + (cls ? " " + cls : ""));
    e.appendChild(el("b", null, String(val))); e.appendChild(document.createTextNode(" " + label));
    return e;
  };
  stats.appendChild(stat("agents", r.agents ?? 0));
  if (r.tool_calls != null) stats.appendChild(stat("tools", r.tool_calls));
  if (r.tool_failures) stats.appendChild(stat("failed", r.tool_failures, "err"));
  if (r.qa) {
    const passed = r.qa.looks_good || 0, total = passed + (r.qa.needs_changes || 0) + (r.qa.escalate || 0);
    if (total) stats.appendChild(stat("QA pass", `${passed}/${total}`, r.qa.needs_changes || r.qa.escalate ? "warn" : ""));
  }
  card.appendChild(stats);

  if (r.outcome && r.outcome.features && r.outcome.features.length) {
    const chips = el("div", "chips");
    for (const f of r.outcome.features) {
      const done = f.tasks_total && f.tasks_done === f.tasks_total;
      chips.appendChild(el("span", "chip" + (done ? " done" : ""), f.name + (f.tasks_total ? ` ${f.tasks_done}/${f.tasks_total}` : "")));
    }
    card.appendChild(chips);
  }
  return card;
}

// ---- run view ---------------------------------------------------------------
const view = { slug: null, run: null, tab: "overview", summary: null, meta: null, events: null,
  filt: { kinds: new Set(), agent: null, q: "", failOnly: false }, expanded: new Set() };

async function renderRun(slug, run, tab) {
  view.slug = slug; view.run = run; view.tab = tab;
  if (view.summary?.run !== run) {
    view.summary = await fetchJSONopt(`/${slug}/runs/${run}/summary.json`);
    view.meta = await fetchJSONopt(`/${slug}/runs/${run}/run.json`) || {};
    view.events = null; view.expanded = new Set();
    view.filt = { kinds: new Set(), agent: null, q: "", failOnly: false };
  }
  const s = view.summary, m = view.meta || {};
  const name = (m.project_name) || slug;
  setCrumbs([{ label: "projects", hash: "" }, { label: name, hash: "" }, { label: run }]);

  const app = $("#app"); app.innerHTML = "";
  // header
  const head = el("div", "runhead");
  head.appendChild(el("h1", null, run));
  const meta = el("div", "meta");
  meta.appendChild(el("span", null, `📁 ${name}`));
  meta.appendChild(el("span", null, `🕐 ${dateStr(s?.started || m.started)}`));
  if (s?.time?.wall_s != null) meta.appendChild(el("span", null, `⏱ ${durS(s.time.wall_s)} wall · ${durS(s.time.active_s)} active`));
  if (s?.cost?.usd != null) meta.appendChild(el("span", null, `💲 ${moneyExact(s.cost.usd)}`));
  if (m.interval) meta.appendChild(el("span", null, `loop ${m.interval}`));
  meta.appendChild(el("span", `status ${(s?.status || m.status || "ended")}`, s?.status || m.status || "ended"));
  head.appendChild(meta);
  app.appendChild(head);

  // tabs
  const tabs = el("div", "tabs");
  for (const [key, lbl] of [["overview", "Overview"], ["tasks", "Tasks"], ["timeline", "Timeline"]]) {
    const t = el("div", "tab" + (tab === key ? " active" : ""), lbl);
    t.onclick = () => (location.hash = `${slug}/${run}/${key}`);
    tabs.appendChild(t);
  }
  app.appendChild(tabs);

  const body = el("div"); body.id = "tabbody"; app.appendChild(body);
  if (!s && tab !== "timeline") {
    body.appendChild(el("div", "empty", "No summary.json for this run — open the Timeline tab for the raw event stream."));
    return;
  }
  if (tab === "tasks") renderTasks(body, s);
  else if (tab === "timeline") await renderTimelineTab(body);
  else renderOverview(body, s);
}

// ---- overview ---------------------------------------------------------------
function kpi(label, big, sub, cls) {
  const k = el("div", "kpi" + (cls ? " " + cls : ""));
  k.appendChild(el("div", "label", label));
  k.appendChild(el("div", "big", big));
  if (sub) k.appendChild(el("div", "sub", sub));
  return k;
}

function renderOverview(root, s) {
  const o = s.outcome, c = s.cost, t = s.time, q = s.quality;
  // KPI row
  const kpis = el("div", "kpis");
  kpis.appendChild(kpi("Outcome", o.shipped ? "✓ shipped" : `${o.tasks_done}/${o.tasks_total}`,
    `${o.tasks_done}/${o.tasks_total} tasks done`, o.shipped ? "good" : (o.tasks_total ? "bad" : "")));
  kpis.appendChild(kpi("Cost", c.usd != null ? moneyExact(c.usd) : "—", `${tokens(c.tokens)} tokens`));
  kpis.appendChild(kpi("Duration", durS(t.wall_s), `${durS(t.active_s)} active`));
  const qaTotal = q.qa.looks_good + q.qa.needs_changes + q.qa.escalate;
  kpis.appendChild(kpi("QA pass", qaTotal ? `${q.qa.looks_good}/${qaTotal}` : "—",
    `${q.qa_round_trips} round-trip(s) · ${q.fixer_cycles} fixer`, q.qa.needs_changes || q.qa.escalate ? "bad" : ""));
  kpis.appendChild(kpi("Tool failures", String(q.tool_failures || 0), `of ${q.tool_calls} calls`, q.tool_failures ? "err" : ""));
  if (s.problems.length) kpis.appendChild(kpi("Problems", String(s.problems.length), "see below", "err"));
  root.appendChild(kpis);

  // cost breakdown
  if (c.usd != null && c.usd > 0) {
    const p = el("div", "panel"); p.appendChild(el("h3", null, "Where the money went"));
    const two = el("div", "cols2");
    two.appendChild(barGroup("By task", c.by_task, money));
    two.appendChild(barGroup("By role", c.by_role, money, ROLE_COLOR));
    p.appendChild(two);
    root.appendChild(p);
  }

  // time split
  const tp = el("div", "panel"); tp.appendChild(el("h3", null, "Where the time went"));
  const total = Math.max(t.wall_s || 0, (t.active_s || 0));
  const split = el("div", "split");
  const aw = total ? Math.min(100, 100 * (t.active_s || 0) / total) : 0;
  const a = el("span"); a.style.width = aw + "%"; a.style.background = "var(--green)"; split.appendChild(a);
  const idle = el("span"); idle.style.width = (100 - aw) + "%"; idle.style.background = "var(--panel-2)"; split.appendChild(idle);
  tp.appendChild(split);
  const lg = el("div", "legend");
  lg.innerHTML = `<span><i style="background:var(--green)"></i>active ${durS(t.active_s)}</span>` +
    `<span><i style="background:var(--panel-2)"></i>idle / waiting ${durS(t.idle_s)}</span>` +
    `<span class="muted">wall ${durS(t.wall_s)} — active can exceed wall when agents overlap</span>`;
  tp.appendChild(lg);
  root.appendChild(tp);

  // swimlane
  const sw = swimlane(s);
  if (sw) root.appendChild(sw);

  // problems
  if (s.problems.length) {
    const pp = el("div", "panel"); pp.appendChild(el("h3", null, `Problems (${s.problems.length})`));
    for (const pr of s.problems) {
      const row = el("div", "problem" + (pr.severity === "error" ? " err" : ""));
      row.appendChild(el("span", "code", pr.code || "?"));
      const why = el("span", "why", (pr.task ? pr.task + " — " : "") + (pr.reason || ""));
      row.appendChild(why);
      row.onclick = () => (location.hash = `${view.slug}/${view.run}/timeline`);
      pp.appendChild(row);
    }
    root.appendChild(pp);
  }
}

function barGroup(title, obj, fmt, colorMap) {
  const wrap = el("div");
  wrap.appendChild(el("div", "section-title", title));
  const bars = el("div", "bars");
  const entries = Object.entries(obj || {}).sort((a, b) => b[1] - a[1]);
  const max = entries.length ? entries[0][1] : 1;
  if (!entries.length) bars.appendChild(el("div", "muted", "no data"));
  for (const [k, v] of entries) {
    const row = el("div", "bar-row");
    row.appendChild(el("div", "name", k));
    const track = el("div", "bar-track");
    const fill = el("div", "bar-fill");
    fill.style.width = (max ? Math.max(2, 100 * v / max) : 0) + "%";
    if (colorMap && colorMap[k]) fill.style.background = colorMap[k];
    track.appendChild(fill); row.appendChild(track);
    row.appendChild(el("div", "val", fmt ? fmt(v) : String(v)));
    bars.appendChild(row);
  }
  wrap.appendChild(bars);
  return wrap;
}

// novel form: one lane per agent across the run's wall clock
function swimlane(s) {
  const agents = (s.agents || []).filter(a => a.start);
  if (!agents.length) return null;
  const t0 = new Date(s.started).getTime();
  const t1 = new Date(s.ended || s.last_ts).getTime();
  const span = Math.max(1, t1 - t0);
  const p = el("div", "panel"); p.appendChild(el("h3", null, "Agent timeline (who ran when)"));
  const swim = el("div", "swim");
  for (const a of agents) {
    const row = el("div", "swim-row");
    const who = el("div", "who");
    who.appendChild(el("span", "role " + (a.role || ""), a.role || "?"));
    who.appendChild(document.createTextNode(" " + (a.task || a.session?.slice(0, 6) || "")));
    row.appendChild(who);
    const track = el("div", "swim-track");
    const start = new Date(a.start).getTime();
    const durMs = (a.active_dur_s || 0) * 1000;
    const left = 100 * (start - t0) / span;
    const width = Math.max(1.5, 100 * durMs / span);
    const bar = el("div", "swim-bar c-" + (a.role || "worker"));
    bar.style.left = Math.max(0, Math.min(100, left)) + "%";
    bar.style.width = Math.min(100 - left, width) + "%";
    bar.title = `${a.role} ${a.task || ""} · ${durS(a.active_dur_s)} active · ${moneyExact(a.cost_usd)} · ${a.tool_calls || 0} tools`;
    track.appendChild(bar);
    row.appendChild(track);
    swim.appendChild(row);
  }
  // problem markers across a full-width overlay row
  if ((s.problems || []).some(pr => pr.ts)) {
    const mrow = el("div", "swim-row");
    mrow.appendChild(el("div", "who muted", "problems"));
    const track = el("div", "swim-track");
    for (const pr of s.problems) {
      if (!pr.ts) continue;
      const x = 100 * (new Date(pr.ts).getTime() - t0) / span;
      const m = el("div", "swim-mark" + (pr.severity === "error" ? " err" : ""));
      m.style.left = Math.max(0, Math.min(100, x)) + "%";
      m.title = `${pr.code}: ${pr.reason || ""}`;
      track.appendChild(m);
    }
    mrow.appendChild(track); swim.appendChild(mrow);
  }
  p.appendChild(swim);
  const axis = el("div", "swim-axis");
  axis.appendChild(el("span", null, hhmmss(s.started)));
  axis.appendChild(el("span", null, hhmmss(s.ended || s.last_ts)));
  p.appendChild(axis);
  return p;
}

// ---- tasks ------------------------------------------------------------------
function mergeStages(lifecycle) {
  // collapse consecutive same-state moves (status flips inside a dir), summing dur
  const out = [];
  for (const st of lifecycle || []) {
    const last = out[out.length - 1];
    if (last && last.state === st.state) last.dur_s = (last.dur_s || 0) + (st.dur_s || 0);
    else out.push({ state: st.state, dur_s: st.dur_s });
  }
  return out;
}

function renderTasks(root, s) {
  const tasks = s.tasks || [];
  if (!tasks.length) { root.appendChild(el("div", "empty", "No tasks recorded in this run.")); return; }
  for (const tk of tasks) {
    const card = el("div", "task-card");
    const th = el("div", "th");
    th.appendChild(el("span", "tid", tk.id));
    if (tk.feature) th.appendChild(el("span", "chip", tk.feature));
    const out = tk.final_state || tk.outcome;
    if (out) th.appendChild(el("span", "badge" + (out === "done" ? " ok" : ""), out));
    if (tk.cost_usd != null) th.appendChild(el("span", "badge", money(tk.cost_usd)));
    if (tk.fixer_cycles) th.appendChild(el("span", "badge bad", `${tk.fixer_cycles} fixer`));
    card.appendChild(th);

    // lifecycle ribbon
    const stages = mergeStages(tk.lifecycle);
    if (stages.length) {
      const rb = el("div", "ribbon");
      stages.forEach((st, i) => {
        if (i) {
          // a backwards move (e.g. review -> in-progress) reads as a bounce
          const prev = stages[i - 1].state, cur = st.state;
          const bounce = prev === "review" && cur === "in-progress";
          rb.appendChild(el("span", "arrow" + (bounce ? " bounce" : ""), bounce ? "↩" : "→"));
        }
        const cls = "stage" + (st.state === "done" ? " done" : st.state === "review" ? " review" : "");
        const stEl = el("span", cls);
        stEl.appendChild(document.createTextNode(st.state || "?"));
        if (st.dur_s) stEl.appendChild(el("span", "d", " " + durS(st.dur_s)));
        rb.appendChild(stEl);
      });
      card.appendChild(rb);
    }

    // verdicts
    if (tk.verdicts && tk.verdicts.length) {
      const vrow = el("div", "th");
      vrow.appendChild(el("span", "muted", "QA:"));
      tk.verdicts.forEach(v => vrow.appendChild(el("span", "verdict " + v, v)));
      card.appendChild(vrow);
    }

    // agents table
    if (tk.agents && tk.agents.length) {
      const tbl = el("table", "task-agents");
      tbl.innerHTML = "<thead><tr><th>role</th><th>model</th><th class='num'>turns</th>" +
        "<th class='num'>tools</th><th class='num'>files</th><th class='num'>errors</th>" +
        "<th class='num'>active</th><th class='num'>cost</th></tr></thead>";
      const tb = el("tbody");
      for (const a of tk.agents) {
        const tr = el("tr");
        tr.innerHTML = `<td><span class="role ${a.role || ''}">${a.role || '?'}</span></td>` +
          `<td class="mono muted">${(a.model || '').replace('claude-', '')}</td>` +
          `<td class="num">${a.turns ?? '–'}</td><td class="num">${a.tool_calls ?? '–'}</td>` +
          `<td class="num">${a.files_written ?? '–'}</td>` +
          `<td class="num${a.errors ? ' err-num' : ''}">${a.errors ?? '–'}</td>` +
          `<td class="num">${durS(a.active_dur_s)}</td>` +
          `<td class="num">${moneyExact(a.cost_usd)}</td>`;
        tb.appendChild(tr);
      }
      tbl.appendChild(tb); card.appendChild(tbl);
    }

    // per-agent final verdict — what each agent that worked the task concluded
    const noted = (tk.agents || []).filter(a => a.final_note);
    if (noted.length) {
      const notes = el("div", "notes");
      for (const a of noted) {
        const n = el("div", "note");
        n.appendChild(el("span", "role " + (a.role || ""), a.role || "agent"));
        const span = el("span", "note-body"); span.innerHTML = md(a.final_note);
        n.appendChild(span);
        notes.appendChild(n);
      }
      card.appendChild(notes);
    } else if (tk.final_note) {
      const n = el("div", "note"); n.innerHTML = md(tk.final_note); card.appendChild(n);
    }
    root.appendChild(card);
  }
}

// ---- timeline ---------------------------------------------------------------
async function renderTimelineTab(root) {
  if (!view.events) view.events = await fetchNDJSON(`/${view.slug}/runs/${view.run}/events.ndjson`);
  // agent cost lookup from summary
  const costBySession = {};
  for (const a of (view.summary?.agents || [])) costBySession[a.session] = a;

  const layout = el("div", "layout");
  layout.appendChild(buildSidebar(costBySession));
  const right = el("div");
  right.appendChild(buildToolbar());
  const tl = el("div", "timeline"); tl.id = "timeline";
  right.appendChild(tl);
  layout.appendChild(right);
  root.appendChild(layout);
  renderTimeline();
}

function agentsFromEvents() {
  const m = new Map();
  for (const ev of view.events) {
    if (!ev.session) continue;
    if (!m.has(ev.session)) m.set(ev.session, { session: ev.session, role: ev.role, task: ev.task, n: 0 });
    const a = m.get(ev.session); a.n++;
    if (ev.role && !a.role) a.role = ev.role;
    if (ev.task && !a.task) a.task = ev.task;
  }
  return m;
}

function buildSidebar(costBySession) {
  const side = el("div", "sidebar");
  const counts = {};
  for (const ev of view.events) counts[ev.kind] = (counts[ev.kind] || 0) + 1;

  const box1 = el("div", "box"); box1.appendChild(el("h3", null, "Event types"));
  for (const k of ORDER) {
    if (!counts[k]) continue;
    const meta = KIND[k] || { color: "#8b96a5", label: k };
    const row = el("div", "filter-row" + (view.filt.kinds.size && !view.filt.kinds.has(k) ? " off" : ""));
    const dot = el("span", "dot"); dot.style.background = meta.color; row.appendChild(dot);
    row.appendChild(el("span", null, meta.label));
    row.appendChild(el("span", "cnt", String(counts[k])));
    row.onclick = () => {
      const s = view.filt.kinds;
      if (s.has(k)) s.delete(k); else if (s.size) s.add(k); else { ORDER.forEach(x => counts[x] && s.add(x)); s.delete(k); }
      redrawTimeline();
    };
    box1.appendChild(row);
  }
  side.appendChild(box1);

  const agents = agentsFromEvents();
  if (agents.size) {
    const box2 = el("div", "box"); box2.appendChild(el("h3", null, `Agents (${agents.size})`));
    const allRow = el("div", "filter-row" + (view.filt.agent ? " off" : ""));
    allRow.appendChild(el("span", null, "all agents"));
    allRow.onclick = () => { view.filt.agent = null; redrawTimeline(); };
    box2.appendChild(allRow);
    for (const a of agents.values()) {
      const row = el("div", "filter-row" + (view.filt.agent && view.filt.agent !== a.session ? " off" : ""));
      row.appendChild(el("span", "role " + (a.role || ""), a.role || "?"));
      row.appendChild(el("span", null, a.task || a.session.slice(0, 6)));
      const acc = costBySession[a.session];
      const meta = acc && acc.cost_usd != null ? moneyExact(acc.cost_usd)
        : (acc ? tokens((Object.values(acc.tokens || {}).reduce((x, y) => x + (y || 0), 0))) : a.n);
      row.appendChild(el("span", "agent-cost", String(meta)));
      row.onclick = () => { view.filt.agent = view.filt.agent === a.session ? null : a.session; redrawTimeline(); };
      box2.appendChild(row);
    }
    side.appendChild(box2);
  }
  return side;
}

function redrawTimeline() { const b = $("#tabbody"); b.innerHTML = ""; renderTimelineTab(b); }

function buildToolbar() {
  const bar = el("div", "toolbar");
  const search = el("input"); search.type = "search"; search.placeholder = "Filter by tool, summary, result, task…";
  search.value = view.filt.q;
  search.oninput = () => { view.filt.q = search.value.toLowerCase(); renderTimeline(); };
  bar.appendChild(search);
  const lbl = el("label");
  const cb = el("input"); cb.type = "checkbox"; cb.checked = view.filt.failOnly;
  cb.onchange = () => { view.filt.failOnly = cb.checked; renderTimeline(); };
  lbl.appendChild(cb); lbl.appendChild(document.createTextNode("failures only"));
  bar.appendChild(lbl);
  return bar;
}

function isFail(ev) { return ev.kind === "error" || ev.kind === "problem" || (ev.kind === "tool" && ev.ok === false); }

function passes(ev) {
  const f = view.filt;
  if (f.failOnly && !isFail(ev)) return false;
  if (f.kinds.size && !f.kinds.has(ev.kind)) return false;
  if (f.agent && ev.session !== f.agent) return false;
  if (f.q) {
    const hay = `${ev.kind} ${ev.tool || ""} ${ev.summary || ""} ${ev.result || ""} ${ev.task || ""} ${ev.feature || ""} ${ev.action || ""} ${ev.verdict || ""} ${ev.role || ""} ${ev.code || ""} ${ev.reason || ""}`.toLowerCase();
    if (!hay.includes(f.q)) return false;
  }
  return true;
}

function renderTimeline() {
  const tl = $("#timeline"); if (!tl) return;
  tl.innerHTML = "";
  const rows = (view.events || []).filter(passes);
  if (!rows.length) { tl.appendChild(el("div", "empty", "No events match the current filters.")); return; }
  for (const ev of rows) tl.appendChild(renderEvent(ev));
}

function mainLine(ev) {
  switch (ev.kind) {
    case "tool": return { tool: ev.tool || "tool", summary: ev.summary || "" };
    case "error": return { tool: (ev.tool ? ev.tool + " " : "") + "error", summary: ev.summary || ev.reason || "" };
    case "problem": return { tool: ev.code || "problem", summary: ev.reason || "" };
    case "task_move": return { tool: ev.task, summary: `${ev.from || "—"} → ${ev.to}${ev.status ? "  (" + ev.status + ")" : ""}` };
    case "qa_verdict": return { tool: ev.task, summary: `QA: ${ev.verdict}` };
    case "git": return { tool: "git " + (ev.action || ""), summary: [ev.sha, ev.branch, ev.summary].filter(Boolean).join("  ") };
    case "state": return { tool: "cycle", summary: `${ev.last_action || ""}${ev.active && ev.active.length ? "  · active: " + ev.active.map(w => w.task + (w.role ? ":" + w.role : "")).join(", ") : ""}` };
    case "agent_start": return { tool: `▸ ${ev.role || "agent"} started`, summary: [ev.task, (ev.model || "").replace("claude-", "")].filter(Boolean).join("  ") };
    case "agent_stop": return { tool: `◂ ${ev.role || "agent"} ended`, summary: agentStopSummary(ev) };
    case "run_start": return { tool: "run started", summary: ev.interval ? "loop " + ev.interval : "" };
    case "run_end": return { tool: "run ended", summary: ev.reason || "" };
    default: return { tool: ev.kind, summary: ev.summary || "" };
  }
}
function agentStopSummary(ev) {
  const bits = [];
  if (ev.cost_usd != null) bits.push(moneyExact(ev.cost_usd));
  if (ev.turns != null) bits.push(`${ev.turns} turns`);
  if (ev.tool_calls != null) bits.push(`${ev.tool_calls} tools`);
  if (ev.files_written) bits.push(`${ev.files_written} files`);
  if (ev.active_dur_s != null) bits.push(durS(ev.active_dur_s));
  let s = bits.join(" · ");
  if (ev.final_note) s += (s ? "  — " : "") + ev.final_note;
  return s;
}

function renderEvent(ev) {
  const meta = KIND[ev.kind] || { glyph: "•", color: "#8b96a5" };
  const fail = isFail(ev);
  const row = el("div", "evt " + ev.kind + (meta.life ? " lifecycle" : "") + (fail ? " fail" : "") + (ev.ref ? " has-ref" : ""));
  row.appendChild(el("div", "time", hhmmss(ev.ts)));
  const gut = el("div", "gut", fail && ev.kind === "tool" ? "✖" : meta.glyph);
  gut.style.color = fail ? "var(--red)" : meta.color;
  row.appendChild(gut);

  const body = el("div", "body");
  const line = el("div", "line");
  if (ev.role && !meta.life) line.appendChild(el("span", "role " + ev.role, ev.role));
  if (ev.task && (ev.kind === "tool" || ev.kind === "problem")) line.appendChild(el("span", "tag", ev.task));
  const ml = mainLine(ev);
  if (ml.tool) line.appendChild(el("span", "tool", ml.tool));
  if (ml.summary) {
    const exp = view.expanded.has(ev.seq);
    line.appendChild(el("span", "summary" + (exp ? " full" : ""), ml.summary));
  }
  if (ev.dur_ms != null && ev.dur_ms >= 1000) line.appendChild(el("span", "dur-pill", durS(ev.dur_ms / 1000)));
  if (ev.feature && (ev.kind === "task_move" || ev.kind === "qa_verdict" || ev.kind === "agent_start"))
    line.appendChild(el("span", "tag", ev.feature));
  body.appendChild(line);

  const expandable = ev.ref || (ev.result && ev.result.length);
  if (expandable) {
    row.classList.add("has-ref");
    row.onclick = () => { view.expanded.has(ev.seq) ? view.expanded.delete(ev.seq) : view.expanded.add(ev.seq); renderTimeline(); };
    if (view.expanded.has(ev.seq)) {
      if (ev.result) body.appendChild(el("div", "result" + (ev.ok === false ? " fail" : ""), ev.result));
      if (ev.ref) {
        const ref = el("div", "ref");
        ref.innerHTML = `transcript: <code>${ev.ref.transcript || ""}</code>` + (ev.ref.uuid ? `<br>message: <code>${ev.ref.uuid}</code>` : "");
        body.appendChild(ref);
      }
    }
  }
  row.appendChild(body);
  return row;
}

// ---- help dialog ------------------------------------------------------------
function closeHelp() { const m = $("#help-modal"); if (m) m.remove(); }
function openHelp() {
  if ($("#help-modal")) return;
  const back = el("div", "modal-backdrop"); back.id = "help-modal";
  back.onclick = (e) => { if (e.target === back) closeHelp(); };
  const legend = ORDER.map(k => { const m = KIND[k]; return m ? `<div class="g" style="color:${m.color}">${m.glyph}</div><div><b>${m.label}</b></div>` : ""; }).join("");
  back.innerHTML = `
    <div class="modal" role="dialog" aria-modal="true">
      <div class="modal-head"><h2>How to read these logs</h2><button class="close" title="Close (Esc)">×</button></div>
      <p>Each <b>run</b> is one harness launch (<code>harness-up.sh</code> → teardown), recorded mechanically — no agent writes these logs.
      Three views answer three questions:</p>
      <ul>
        <li><b>Overview</b> — did it ship, what did it cost, where did the time go, what went wrong. Cost is split by task / role; the <b>agent timeline</b> shows who ran when.</li>
        <li><b>Tasks</b> — the story of each task: its <b>lifecycle ribbon</b> (backlog → in-progress → review → done with durations), QA verdicts, per-agent stats, and the agent's final note.</li>
        <li><b>Timeline</b> — every event in order. Filter by type/agent, search, or flip <b>failures only</b>. Click a tool/error row to reveal its result tail + transcript ref.</li>
      </ul>
      <h3>Event icons</h3>
      <div class="legend-grid">${legend}</div>
      <h3>Good to know</h3>
      <ul>
        <li>Cost is derived from each agent's token usage × model price; the orchestrator's loop is usually the biggest line item.</li>
        <li>Only <b>harness-spawned</b> agents are recorded — an ad-hoc <code>claude</code> you open by hand is ignored.</li>
        <li>Data lives at <code>~/.agent-harness-history/&lt;project&gt;/runs/&lt;run-id&gt;/</code> — <code>events.ndjson</code> (raw) + <code>summary.json</code> (rollup), plain JSON, greppable.</li>
      </ul>
    </div>`;
  back.querySelector(".close").onclick = closeHelp;
  document.body.appendChild(back);
}
