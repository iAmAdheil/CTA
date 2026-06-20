# Runbook — debug a harness run with the history viewer

When a run goes sideways, you no longer have to guess. Every harness launch is
recorded — mechanically, with no LLM in the loop — to a central, append-only
store you can browse in your web browser after the fact.

> **TL;DR**
> ```bash
> # runs are recorded automatically whenever you launch the harness:
> bash ~/agent-harness/scripts/harness-up.sh
> # …later, to see what happened:
> bash ~/agent-harness/scripts/harness-history.sh        # opens the viewer in your browser
> ```

---

## What gets recorded, and how

A **run = one `harness-up.sh` launch → teardown** (stop the tmux session and the
run ends). Each run gets an id minted at launch (`<timestamp>-<short-uuid>`), and
everything in that launch is recorded under it. Restarting the harness starts a
new run.

Recording is done by a small **history watcher** — a pane in the harness tmux
session (labelled `history (recording …)`), started for you by `harness-up.sh`.
It produces events three mechanical ways:

| Source | Events |
|---|---|
| **Agent transcripts** — the pinned orchestrator + every spawned worker/QA/fixer | `agent_start` / `agent_stop`, `tool` (each call: name, summary, success flag `ok`, a short result tail, and `dur_ms`) |
| **Control-plane diff** — task files + QA reports | `task_move` (backlog→in-progress→review→done), `qa_verdict` (looks-good / needs-changes / escalate) |
| **Git poll** | `git` (commit / branch / merge / worktree) |
| **State** — `orchestrator-state.yaml` | `state` (per-cycle snapshot: last action, active agents) |
| **Derived friction** | `problem` (qa needs-changes/escalate, push rejected, merge conflict, permission denied, agent stall, zero-write worker) |

Two design rules make the data trustworthy:

- **One clock.** Every event's `ts` is the watcher's *observation* time, so the
  stream is monotonic and durations are real. A tool event also keeps the
  transcript's own time in `src_ts`.
- **Accounting on stop.** `agent_stop` carries the agent's `model`, token usage,
  **`cost_usd`** (tokens × model price), `turns`, `tool_calls`, `errors`,
  `files_written`, `active_dur_s`, and a ≤280-char `final_note` (the agent's last
  message). This is where per-agent cost comes from.

It only ever tails session UUIDs the harness itself recorded (the orchestrator's
pinned id + each task's `assigned_to`), so an ad-hoc `claude` session you open by
hand in the same project is **not** captured. Depth stays "events + tool calls":
each call's summary + a short result tail (plus a `ref` to the transcript line),
not full IO or agent reasoning — except each agent's single final note.

Nothing here is written by an agent. The store is plain JSON you can also `grep`.

---

## Where it lives

Outside your project (so it survives `git clean`, worktree teardown, even
deleting the repo), under `~/.agent-harness-history/`:

```
~/.agent-harness-history/
  index.json                         # outcome-oriented roll-up the landing page reads
  <project-slug>/
    project.json                     # path, name, repo
    runs/
      <run-id>/
        run.json                     # started / ended / status / loop interval
        events.ndjson                # the timeline — one JSON event per line, in order
        summary.json                 # derived per-run rollup (cost, time, per-task stories)
        snapshots/                   # state.yaml / board copies as they changed
```

`summary.json` is a **pure function of `events.ndjson`** — recomputed every time
the index is built (`harness.history build-index`), never hand-edited. It holds
the outcome (shipped? tasks done), cost (`by_task` / `by_role` / `by_model`),
time (wall vs active), quality (QA counts, round-trips, fixer cycles), the
`problems` list, per-agent accounting, and a per-task `lifecycle` with stage
durations. The viewer's Overview + Tasks tabs read it directly.

Override the location with `HARNESS_HISTORY_DIR`. Scope is **project → run →
events**; `feature` rides along on events so the viewer can filter by it too.

---

## Browsing a run

```bash
bash ~/agent-harness/scripts/harness-history.sh
```

This starts a **local, on-demand** web server rooted at the history store (it is
*not* a background daemon — Ctrl-C stops it when you're done), copies the
viewer assets in, rebuilds `index.json` + each `summary.json`, and opens your
browser.

- **Landing**: every project → its runs, newest first. Each card leads with the
  **outcome** (✓ shipped N/M), **cost**, duration (wall · active), QA pass-rate,
  tool failures, and a ⚠ problem count — not raw event volume.
- **Run view** has three tabs:
  - **Overview** — KPI cards, a cost breakdown by task / role, a wall-vs-active
    time split, the **agent timeline** (one lane per agent across the run), and
    the problems list.
  - **Tasks** — the story of each task: its **lifecycle ribbon** (with stage
    durations and QA-bounce arrows), verdicts, a per-agent stat table, and the
    agent's final note.
  - **Timeline** — every event in order. Filter by type/agent (the agent list
    shows each agent's **cost**), search across summary *and result text*, or flip
    **failures only**. Click a tool/error row to reveal its result tail +
    transcript ref.

Env: `HARNESS_HISTORY_PORT` to pin the port (default: a free port near 8787).

---

## Tips

- **"Did this run ship, and what did it cost?"** → the landing card and Overview
  answer both at a glance; the cost-by-role bar shows where the money went (the
  orchestrator's loop is usually the biggest line item).
- **"What broke?"** → Overview's problems list, or the Timeline's **failures
  only** toggle (failed tool calls + `problem` events). Expand a row for the
  result tail; its `ref` points at the exact transcript line.
- **"What's the story of TASK-003?"** → the Tasks tab: lifecycle ribbon, QA
  verdicts, fixer cycles, per-agent cost, and the final note.
- **"What did the orchestrator decide?"** → filter the Timeline to the
  `orchestrator`; its `tool` calls are the task moves, spawns, and merges, and
  its `agent_stop` final note summarizes the cycle.

## Retro-fitting an old run

`summary.json` and the new `tool`/`agent_stop` fields are derived from
transcripts, so a run recorded by an older watcher can be upgraded in place:

```bash
python -m harness.history_watcher --project <repo> --run-id <id> --rebuild
```

This backs the original up to `events.ndjson.bak`, then **merges** the preserved
control-plane / git / QA events with transcript-derived events re-stamped with
their transcript time (so the merge stays ordered), and recomputes `summary.json`.
It leaves `run.json` timestamps intact.

The recorder is best-effort and self-isolating: a bad tick is logged and skipped,
never crashing the run. If `python3`+`pyyaml` (or the `harness` conda env) is
missing, `harness-up.sh` prints a warning and launches without recording — the
harness itself is unaffected.
