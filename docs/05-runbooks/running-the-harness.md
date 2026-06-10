# Runbook — initialize, run, and use the harness in a project

This is the end-to-end operator guide: take a fresh project repo from nothing to
a self-driving agent pipeline. It assumes the harness itself is cloned at
`~/agent-harness`. Adjust that path if yours differs.

> **TL;DR**
> ```bash
> cd /path/to/your/project                       # a git repo
> bash ~/agent-harness/scripts/setup.sh           # one-time init
> git add .claude/skills && git commit -m "harness: base agent skills"
> # …write an approved spec into docs/specs/…    (see step 3)
> HARNESS_ORCH_LOOP=30s bash ~/agent-harness/scripts/harness-up.sh
> tmux attach -t harness                          # watch it work
> ```

---

## 0. Prerequisites (machine-level, once)

| Tool | Why | Check |
|---|---|---|
| `claude` CLI | runs every agent (orchestrator, worker, QA, …) | `command -v claude` |
| `conda` | the harness Python wrappers run in a dedicated `harness` env | `command -v conda` |
| `tmux` | headed launch + agent panes | `command -v tmux` |
| `git` | the project must be a git repo (workers use worktrees) | `git status` |
| `gh` (optional) | only if workers open real GitHub PRs | `gh auth status` |

`setup.sh` creates the conda env for you; you don't create it by hand.

---

## 1. Initialize a project with the harness (one-time)

Run from the **root of your project repo** (it must already be a git repo):

```bash
cd /path/to/your/project
bash ~/agent-harness/scripts/setup.sh
```

This is **idempotent** — re-running never clobbers your `CLAUDE.md`, specs, or
task files. It:

- creates `tasks/` (per-feature workspaces are created later, per spec)
- creates `docs/{specs,adrs,active-features}/`
- copies the base agent skills (`orchestrator`, `task-breakdown`, `worker`,
  `qa-agent`, `advisor`) from the harness repo into the project's
  `.claude/skills/`
- copies the spec template to `docs/specs/_template.md`
- writes a starter `CLAUDE.md` (only if missing)
- writes an empty `orchestrator-state.yaml` (control-plane state)
- seeds the persistent Kanban boards under `docs/_kanban/`
- adds `.gitignore` entries for the control-plane files (state, `tasks/`,
  `docs/active-features/`, `docs/_kanban/`)
- creates + editable-installs the `harness` conda env

### Two required follow-ups after setup

1. **Commit the skills.** Workers and QA agents run inside git worktrees, which
   only contain *committed* files — so the skills must be committed to load
   there:
   ```bash
   git add .claude/skills && git commit -m "harness: base agent skills"
   ```
   (Edit skills in the harness repo's `skills/`, **not** in `.claude/skills/` —
   the latter is overwritten on every `setup.sh` run.)

2. **Fill out `CLAUDE.md`.** This is the operating manual loaded into every
   agent session — stack, conventions, how to run tests, definition of done,
   footguns. The more precise it is, the better the agents behave.

---

## 2. Confirm the harness can talk to its Python wrappers

```bash
conda run -n harness python -m harness.state_manager read
```

You should get JSON back (the empty state). If you get
`EnvironmentLocationNotFound` or an import error, re-run `setup.sh`.

---

## 3. Write a spec to kick things off

The pipeline is driven by **specs**. Copy the template and fill it in:

```bash
cp docs/specs/_template.md docs/specs/feature-my-thing.md
```

Frontmatter that matters:

```yaml
---
id: feature-my-thing      # becomes the feature workspace name: tasks/feature-my-thing/
title: "Short title"
status: approved          # draft | approved | in-breakdown | blocked | superseded
priority: high            # critical | high | medium | low
---
```

- The orchestrator only acts on a spec once its `status:` is **`approved`**.
  Leave it `draft` while you're still writing; flip to `approved` when ready.
- Write clear, testable **Acceptance Criteria** — these become the QA rubric.
- Point `## References` at the source files agents should read.
- Serial-per-spec: the harness breaks down **one approved spec at a time**,
  highest priority first.

---

## 4. Run the harness

The orchestrator is **single-shot by design**: one invocation = one cycle (read
state → react to transitions → write state → exit). Cadence is supplied by an
external driver. Pick the launch path that fits what you're doing.

### 4a. Headed in tmux — the normal way (recommended)

```bash
# self-driving by default: re-fires a cycle every 60s until you stop it
bash ~/agent-harness/scripts/harness-up.sh
tmux attach -t harness          # detach: Ctrl-b then d

# override the interval:           HARNESS_ORCH_LOOP=30s bash ~/agent-harness/scripts/harness-up.sh
# single cycle (then idle at TUI): HARNESS_ORCH_LOOP=off  bash ~/agent-harness/scripts/harness-up.sh
```

This creates a tmux session named `harness`, **detached**, and starts everything
for you — you do **not** create the session or type the Claude command yourself.

```
tmux session "harness"
├─ window 0 "orchestrator"
│   ┌─────────────────────────────┬──────────────────────────────┐
│   │ pane 0: claude /orchestrator│ pane 1: orch-watch.sh -f      │
│   │   (headed TUI control loop) │   (live dashboard, auto-       │
│   │                             │    refresh every 3s)           │
│   └─────────────────────────────┴──────────────────────────────┘
└─ window 1 "agents"   ← appears on first spawn; one pane per worker/QA/fixer
                          switch to it with: Ctrl-b then 1
```

- **Pane 1 (the right dashboard) opens automatically** — you don't launch it.
- By default it loops every **60s** (`HARNESS_ORCH_LOOP`, default `60s`), so the
  pipeline self-advances. Override the interval (`HARNESS_ORCH_LOOP=30s`) or opt
  out with `HARNESS_ORCH_LOOP=off` for a single cycle that then idles at the TUI.
  Don't run a single cycle for a real feature — finished agents don't self-exit
  yet (see Caveats), so one cycle would look hung after the first worker finishes.
- If a `harness` session already exists, the script refuses to clobber it.

### 4b. Drive a feature to completion, then stop — `harness-drive.sh`

A standalone bash loop that fires cycles until **every task is terminal**, then
exits. Paces itself: waits for an agent to produce a result before the next
cycle; fires promptly when work is queued.

```bash
bash ~/agent-harness/scripts/harness-drive.sh
# logs to .harness-logs/drive.log
```

Knobs: `HARNESS_DRIVE_MAX_CYCLES` (40), `HARNESS_DRIVE_WAIT` (900s per agent
turn), `HARNESS_DRIVE_POLL` (15s).

### 4c. One observable cycle — `orch-run.sh`

Runs exactly one cycle with streaming, timestamped activity logging (useful for
debugging what a cycle actually did):

```bash
bash ~/agent-harness/scripts/orch-run.sh           # foreground, streams to terminal
# or background it:
nohup bash ~/agent-harness/scripts/orch-run.sh &
# writes .harness-logs/orch-activity-latest.log + orch-stream-latest.jsonl
```

### 4d. One bare cycle — `run-orchestrator.sh`

The minimal "tick once" path (interactive TUI, no streaming log). You drive
cadence yourself (`/loop`, cron, file-watcher):

```bash
bash ~/agent-harness/scripts/run-orchestrator.sh
```

> **Which do I use?** Day-to-day, watch it live → **4a**. Unattended "finish this
> feature and stop" → **4b**. Debugging one cycle → **4c**.

---

## 5. Watch what's happening

```bash
bash ~/agent-harness/scripts/orch-watch.sh         # one snapshot
bash ~/agent-harness/scripts/orch-watch.sh -f       # follow (refresh 3s)
bash ~/agent-harness/scripts/orch-watch.sh -f 5     # follow, refresh 5s
```

The dashboard shows: whether a cycle is running, live agent panes (+ the attach
command), git branch & recent commits, task files grouped by state, the
control-plane state, and a tail of the latest activity log.

Other vantage points:
- `tmux attach -t harness` then `Ctrl-b 1` to see the live agent panes.
- `tasks/<feature>/board.md` — the per-feature Kanban board (open in Obsidian).
- `docs/_kanban/specs-board.md` — spec-level board.
- `docs/active-features/<feature>/progress.md` and `qa-report-*.md`.

---

## 6. How the pipeline flows (what "using it" looks like)

You mostly **write specs and review PRs**; the harness does the middle.

```
spec (status: approved)
      │  orchestrator runs /advisor (ADR gate) then /task-breakdown
      ▼
tasks/<feature>/backlog/*.yaml         ← N task files, dependency-ordered
      │  orchestrator spawns a worker (worktree + tmux pane) per runnable task
      ▼
in-progress  ──(worker commits + opens PR)──▶  pr-opened
      │  orchestrator reaps the pane, spawns QA against the rubric
      ▼
   QA verdict
   ├─ looks-good  ──▶ orchestrator merges to the feature branch ──▶ done
   ├─ needs-changes ──▶ qa-failed ──▶ Opus fixer ──▶ back to QA
   └─ escalate ──▶ human-review (your call)
```

Your touch points:
- **Approve specs** — flip `status: approved` when a spec is ready.
- **Resolve blocks** — a spec/task in `blocked` or `blocked-escalated` wants a
  human decision (often an ADR in `docs/adrs/`).
- **Merge the feature branch** — the harness merges each task's PR into the
  per-spec **feature branch**; promoting that feature branch to `master`/`main`
  is a **human** action.

Concurrency is capped by `max_workers` in `orchestrator-state.yaml` (default 3).

---

## 7. Stop / pause / reset

```bash
tmux kill-session -t harness            # stop the headed launch + all agent panes
```

- **Pause** the harness by simply not firing cycles — state lives on disk, so
  the next cycle resumes cleanly. There's nothing to "shut down" beyond tmux.
- **Reset state** (nuclear): re-running `setup.sh` is safe and won't wipe your
  work; to truly start over, remove `orchestrator-state.yaml`, `tasks/`, and
  `docs/active-features/` (all gitignored control-plane) and re-run `setup.sh`.

---

## 8. Caveats / known sharp edges (read this)

These are tracked in `~/agent-harness/todo.md` as 🔴 CRITICAL — until fixed, the
operator works around them:

1. **A single `/orchestrator` does ONE cycle and then looks hung.** Finished
   workers don't self-exit; they sit holding their pane until the *next* cycle
   reaps them. So a bare `run-orchestrator.sh` / `claude /orchestrator` will
   appear stuck after the first worker completes. **Always run with a driver** —
   `HARNESS_ORCH_LOOP` (4a) or `harness-drive.sh` (4b).

2. **A worker that crashes mid-task strands its task at `in-progress`.** If a
   worker pane dies before writing a terminal status, the orchestrator drops it
   from `active_workers` but will **not** auto-re-spawn. Manual recovery: remove
   the stale worktree + branch, move the task file back to `backlog/`, null out
   `assigned_to`/`pane`/`worktree`/`started`, set `status: backlog`; the next
   cycle spawns a fresh worker.

3. **Skills must be committed.** If workers/QA can't find the harness skills,
   you forgot `git add .claude/skills && git commit` (step 1).

---

## Quick command reference

| Goal | Command |
|---|---|
| Init a project | `bash ~/agent-harness/scripts/setup.sh` |
| Sanity-check wrappers | `conda run -n harness python -m harness.state_manager read` |
| Run headed + continuous | `HARNESS_ORCH_LOOP=30s bash ~/agent-harness/scripts/harness-up.sh` |
| Attach / detach | `tmux attach -t harness` / `Ctrl-b d` |
| Drive to completion | `bash ~/agent-harness/scripts/harness-drive.sh` |
| One observable cycle | `bash ~/agent-harness/scripts/orch-run.sh` |
| One bare cycle | `bash ~/agent-harness/scripts/run-orchestrator.sh` |
| Dashboard | `bash ~/agent-harness/scripts/orch-watch.sh -f` |
| Stop everything | `tmux kill-session -t harness` |
