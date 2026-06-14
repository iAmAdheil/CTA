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
| **Agent transcripts** — the pinned orchestrator + every spawned worker/QA/fixer | `agent_start` / `agent_stop`, `tool` (each tool call: name + short summary), `error` (failed tool calls) |
| **Control-plane diff** — task files + QA reports | `task_move` (backlog→in-progress→review→done), `qa_verdict` (looks-good / needs-changes / escalate) |
| **Git poll** | `git` (commit / branch / merge / worktree) |
| **State** — `orchestrator-state.yaml` | `state` (per-cycle snapshot: last action, active agents) |

It only ever tails session UUIDs the harness itself recorded (the orchestrator's
pinned id + each task's `assigned_to`), so an ad-hoc `claude` session you open by
hand in the same project is **not** captured. Depth is "events + tool calls":
each tool call's name + a one-line summary (plus a `ref` back to the transcript
line for manual drill-down), and tool *errors* — not full tool inputs/outputs or
agent reasoning.

Nothing here is written by an agent. The store is plain JSON you can also `grep`.

---

## Where it lives

Outside your project (so it survives `git clean`, worktree teardown, even
deleting the repo), under `~/.agent-harness-history/`:

```
~/.agent-harness-history/
  index.json                         # roll-up the viewer's landing page reads
  <project-slug>/
    project.json                     # path, name, repo
    runs/
      <run-id>/
        run.json                     # started / ended / status / loop interval
        events.ndjson                # the timeline — one JSON event per line, in order
        snapshots/                   # state.yaml / board copies as they changed
```

Override the location with `HARNESS_HISTORY_DIR`. Scope is **project → run →
events**; `feature` rides along on events so the viewer can filter by it too.

---

## Browsing a run

```bash
bash ~/agent-harness/scripts/harness-history.sh
```

This starts a **local, on-demand** web server rooted at the history store (it is
*not* a background daemon — Ctrl-C stops it when you're done), copies the
viewer assets in, rebuilds `index.json`, and opens your browser.

- **Landing**: every project → its runs, newest first. Each run card shows event
  count, agents, **errors** (red), and the features touched.
- **Run view**: the full timeline. Filter from the sidebar by **event type** or
  by **agent** (orchestrator / each worker / QA / fixer), search by tool/summary/
  task, or flip **errors only** to jump straight to what broke. Click any tool
  row to reveal the transcript path + message id for manual drill-down.

Env: `HARNESS_HISTORY_PORT` to pin the port (default: a free port near 8787).

---

## Tips

- **"What broke?"** → open the run, toggle **errors only**. Each `error` is a
  failed tool call with a truncated message; the row's `ref` points at the exact
  transcript line.
- **"What did the orchestrator decide?"** → filter to the `orchestrator` agent;
  its `tool` calls are the task moves, spawns, and merges.
- **"Why did QA fail this task?"** → filter to the task's `qa` agent and read its
  tool calls up to the `qa_verdict`.
- The recorder is best-effort and self-isolating: a bad tick is logged and
  skipped, never crashing the run. If `python3`+`pyyaml` (or the `harness` conda
  env) is missing, `harness-up.sh` prints a warning and launches without
  recording — the harness itself is unaffected.
