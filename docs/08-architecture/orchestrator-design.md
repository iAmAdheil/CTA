# Orchestrator Design

The orchestrator is the heart of the harness. Understanding its design principles is essential for building and debugging it.

---

## The Core Principle: Stateless Loop

The orchestrator does not hold history in its context window. It does not remember what it did in previous cycles. All state lives in files.

```
wake up
→ read orchestrator-state.yaml
→ read all task files
→ compute what needs to happen
→ take ONE action
→ write updated orchestrator-state.yaml
→ sleep N seconds
→ repeat
```

This solves the context window problem entirely. The orchestrator can run for days or weeks. It can crash and restart mid-session without losing a single task. The state is on disk, not in memory.

---

## The Single-Action-Per-Cycle Rule

Each cycle, the orchestrator takes **one action** and then re-evaluates. It does not batch actions.

Why:
- Each action changes the state. The right next action depends on the new state.
- Batching actions means acting on stale state — which leads to double-spawning workers, missed signals, and hard-to-debug behavior.
- Single-action cycles are trivially debuggable: the log shows exactly what happened and why.

Example cycle log:
```
cycle 47: no runnable tasks, 2 workers active, sleeping
cycle 48: no runnable tasks, 2 workers active, sleeping
cycle 49: TASK-051 changed status to pr-opened → triggering async QA agent
cycle 50: QA agent running (review-file WIP), sleeping
cycle 51: QA agent running (review-file WIP), sleeping
cycle 52: qa-report-TASK-051.md verdict looks-good → set qa-passed, board → Human Review
```

---

## State Transitions the Orchestrator Watches

| Event (file change) | Action taken |
|---|---|
| New file in `01-specs/` with `status: approved` | Invoke Task Breakdown Agent |
| Task file status → `in-progress` (set by orchestrator) | Spawn worker in new worktree + a pane in the shared agents window |
| `progress.md` contains BLOCKER section | Check if answerable from docs; if not, invoke Opus |
| Task file status → `pr-opened` (set by worker) | Tear down worker pane + worktree, spawn **async QA agent** (own worktree on `task/<id>`). Review agent is a later, separate addition. |
| Task file status → `pr-updated` (set by Opus fixer) | Trigger QA again (re-QA) on the same branch |
| `qa-report-<ID>.md` verdict — `looks-good` | Set task `qa-passed`, board → Human Review, notify you |
| `qa-report-<ID>.md` verdict — `needs-changes` | Set `qa-failed`, spawn **Opus fixer** on the same task (pushes to existing `branch:`, sets `pr-updated`) |
| `qa-report-<ID>.md` verdict — `needs-changes` on the **2nd** (re-QA) pass | Terminal: anything but `looks-good` now → `escalate` |
| `qa-report-<ID>.md` verdict — `escalate` (fundamental mismatch) | Set `blocked-escalated`, write `failure_reason` into the task file, board → Human Review |
| QA agent window died, review file stuck at `WIP` | Failed run: retry QA spawn up to N=2; on exhaustion `escalate` (`blocked-escalated` + `failure_reason`) |
| Out-of-scope bug noted by QA | **Ignored** by the loop (deferred to the future random-bugs subsystem) |
| Task file status → `done` (set by the separate merge/archival agent post-merge) | Check dependency graph for newly unblocked tasks; trigger Doc Closeout |

### Task Status State Machine

```
backlog
  → in-progress       (orchestrator spawns worker)
      → pr-opened     (worker signals done, fills branch:, opens PR)
          → [async QA]
              → qa-passed         (looks-good → awaiting your manual merge)
              → qa-failed         (needs-changes: bug, or trivial mismatch → Opus fixer)
              → blocked-escalated (escalate: fundamental mismatch, or QA run failed ×N → you)
      → blocked       (worker writes BLOCKER, waiting for Opus/you)

qa-failed
  → pr-updated        (Opus fixer pushes to the existing branch)
      → [re-QA — 2nd pass is terminal]
          → qa-passed         (looks-good)
          → blocked-escalated (anything else — no further retries)

qa-passed
  → done              (separate agent detects your manual merge, then archives)
```

The verdict ladder is **one retry**: `needs-changes` buys exactly one Opus pass; the re-QA either passes or escalates.

---

## Linear and Telegram as Side Effects

Linear API calls and Telegram messages are **side effects of state transitions**, not primary actions. The orchestrator handles them inline at each gate:

```
TASK STARTS
  orchestrator reads TASK-051.yaml
  → spins up worker agent in a pane (%7) of the shared agents window
  → Linear: LIN-51 → "In Progress"         ← side effect
  → Telegram: "🔧 LIN-51 started"           ← side effect
  → writes orchestrator-state.yaml

PR OPENED
  orchestrator detects status: pr-opened
  → Linear: LIN-51 → "In Review"           ← side effect
  → Linear: attach PR link                 ← side effect
  → Telegram: "🔀 PR #91 opened"           ← side effect
  → triggers QA agent
  → writes orchestrator-state.yaml

MERGED (detected by the separate merge/archival agent, not the orchestrator)
  agent detects the human merged the PR (gh pr view --json state,mergedAt)
  → sets task status: done
  → Linear: LIN-51 → "Done"               ← side effect
  → Telegram: "✅ LIN-51 merged"           ← side effect
  → triggers Doc Closeout
  orchestrator (next cycle) sees status: done
  → archives the task file, checks dependency graph
  → writes orchestrator-state.yaml
```

No separate agent for Linear. No separate agent for Telegram. The orchestrator does it in 4–5 lines per transition.

---

## Worker Management

The orchestrator tracks workers in `orchestrator-state.yaml`. Before spawning a new worker:

```python
if len(active_workers) >= max_workers:
    # add task to queue, don't spawn
else:
    # spawn worker, add to active_workers
```

Workers are spun up with:
1. `git worktree add {path} {branch}`
2. `tmux split-window` into the shared `agents` window (the first agent creates the window) — its pane id (e.g. `%7`) is the worker's durable identity
3. Claude Code started in that pane with the task handoff prompt

Workers are torn down when they signal `pr-opened`:
1. `tmux kill-pane -t {pane}` (killing the last pane closes the agents window; it's recreated on the next spawn)
2. `git worktree remove {path}` (the `task/<id>` branch + open PR survive)
3. Remove from `active_workers` in state

The QA agent and the Opus fixer are tracked the same way (in `active_workers`, the QA one with `role: qa`), each spawning its **own** fresh worktree on the existing `task/<id>` branch and torn down on completion (Option B — worktree stays 1:1 with an `active_workers` entry, so the crash-safe teardown invariant holds unchanged).

---

## Blocker Detection

The orchestrator watches `progress.md` files for a BLOCKER section header:

```markdown
## Blockers
BLOCKER: [description]
```

When detected:
1. Check if the blocker is answerable from: spec, ADRs, `decisions.md` for this feature
2. If yes: construct the answer, send via `tmux send-keys -t main:{window} "{answer}" Enter`
3. If no (judgment call): invoke Opus with the blocker + context, write decision to `decisions.md`, send decision to worker

The worker protocol requires workers to write blockers in a specific format so the orchestrator can parse them reliably.

---

## Context Window Management

The orchestrator never accumulates conversation history. Each cycle is a fresh read of files, a computation, and a write.

For invoked agents (Task Breakdown, QA, Review, Opus), each invocation is a new Claude Code session with a prompt that includes all necessary context directly. No session continuity needed — the context is in the files.

For long-running workers, `/compact` is used when the context window gets heavy. Workers are designed to be re-startable from their task file + progress.md if they crash — those two files contain enough context to resume.

---

## Parallelism Rules

```
max_workers: 3           # hard cap on simultaneous workers
                         # set lower (1-2) while debugging
                         # set to 0 to pause all new work

# Orchestrator checks before spawning:
# 1. len(active_workers) < max_workers
# 2. All task.depends_on are in tasks/done/
# 3. The task belongs to the current spec — the harness works one spec at a
#    time (serial-per-spec), so under normal operation every active worker is
#    on the same feature. See feature-lifecycle.md "One Spec at a Time".

# QA agents and Opus fixers are tracked agents too (active_workers, role: qa for QA),
# bounded by the same slot mechanism — within one spec, several tasks can each be in
# their own QA/fix at once, up to max_workers. Across specs, serial-per-spec means no
# overlap (the gate holds the next spec until this one's tasks clear the QA loop).

# Never run simultaneously:
# - Two QA agents on the SAME task
# - Two Opus fixers on the same task
# - Task Breakdown + workers on same feature
```

---

## Failure Modes and Recovery

| Failure | Recovery |
|---|---|
| Orchestrator crashes | Restart — reads state from `orchestrator-state.yaml`, continues where it left off |
| Worker crashes mid-task | Orchestrator detects no progress for N minutes → re-queue task, reset status to `backlog` |
| Worker opens a bad PR | Behavioral QA runs the feature → `needs-changes` (bug) → Opus fixer pushes to the same branch and re-QA runs |
| QA agent window dies with no verdict (review file stuck at `WIP`) | Retry the QA spawn up to N=2; on exhaustion `escalate` → `blocked-escalated` + `failure_reason` |
| Opus invocation fails | Orchestrator retries once, then pauses the task and notifies you via Telegram |
| Linear API down | Log the failure, continue — Linear sync is a side effect, not load-bearing |
| Telegram down | Log the failure, continue — you can read state from `orchestrator-state.yaml` directly |

The only truly blocking failures are: worker loops without making progress (handled by timeout), and a spec with an unresolvable blocker (escalates to you via Telegram).
