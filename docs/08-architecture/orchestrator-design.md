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
cycle 49: TASK-051 changed status to pr-opened → triggering QA agent
cycle 50: QA agent running, sleeping
cycle 51: QA agent running, sleeping
cycle 52: qa-report.md updated with verdict PASS → notifying you + queuing merge
```

---

## State Transitions the Orchestrator Watches

| Event (file change) | Action taken |
|---|---|
| New file in `01-specs/` with `status: approved` | Invoke Task Breakdown Agent |
| Task file status → `in-progress` (set by orchestrator) | Spawn worker in new worktree + window |
| `progress.md` contains BLOCKER section | Check if answerable from docs; if not, invoke Opus |
| Task file status → `pr-opened` (set by worker) | Close worker window, trigger QA + Review agents |
| Task file status → `pr-updated` (set by fix worker) | Trigger QA again on the same PR |
| `qa-report.md` written — PASS | Notify you via Telegram, move to human review |
| `qa-report.md` written — CONDITIONAL, out-of-scope bug | Create BUG-NNN task in backlog, move original task to human review with note |
| `qa-report.md` written — FAIL (1st time) | Set task status `qa-failed`, re-queue to worker with QA report attached |
| `qa-report.md` written — FAIL (2nd time) | Invoke Opus for diagnosis; targeted fix instructions or escalate to you |
| `qa-report.md` written — FAIL (3rd time) | Hard stop, set `blocked-escalated`, Telegram alert to you |
| Task file status → `done` (set by orchestrator post-merge) | Check dependency graph for newly unblocked tasks; trigger Doc Closeout Agent |

### Task Status State Machine

```
backlog
  → in-progress       (orchestrator spawns worker)
      → pr-opened     (worker signals done, PR opened)
          → pr-updated    (fix worker pushes to same PR after QA fail)
          → done          (orchestrator merges after human approval)
          → qa-failed     (QA fail 1st/2nd time, re-queued to worker)
          → blocked-escalated  (QA fail 3rd time, human needed)
      → blocked       (worker writes BLOCKER, waiting for Opus/you)
```

---

## Linear and Telegram as Side Effects

Linear API calls and Telegram messages are **side effects of state transitions**, not primary actions. The orchestrator handles them inline at each gate:

```
TASK STARTS
  orchestrator reads TASK-051.yaml
  → spins up worker agent on window:1
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

MERGED
  orchestrator detects merge
  → Linear: LIN-51 → "Done"               ← side effect
  → Telegram: "✅ LIN-51 merged"           ← side effect
  → triggers Doc Closeout Agent
  → checks dependency graph
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
2. `tmux new-window -t main`
3. Claude Code started in the new window with the task handoff prompt

Workers are torn down when they signal `pr-opened`:
1. `tmux kill-window -t main:{window}`
2. `git worktree remove {path}`
3. Remove from `active_workers` in state

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
# 3. No other active worker is on the same feature

# Never run simultaneously:
# - Two QA agents
# - Two Opus invocations
# - Task Breakdown + workers on same feature
# - Two agents in window 6 (shared slot)
```

---

## Failure Modes and Recovery

| Failure | Recovery |
|---|---|
| Orchestrator crashes | Restart — reads state from `orchestrator-state.yaml`, continues where it left off |
| Worker crashes mid-task | Orchestrator detects no progress for N minutes → re-queue task, reset status to `backlog` |
| Worker opens a bad PR | Review agent flags it, QA fails → orchestrator sends task back to worker with reports attached |
| Opus invocation fails | Orchestrator retries once, then pauses the task and notifies you via Telegram |
| Linear API down | Log the failure, continue — Linear sync is a side effect, not load-bearing |
| Telegram down | Log the failure, continue — you can read state from `orchestrator-state.yaml` directly |

The only truly blocking failures are: worker loops without making progress (handled by timeout), and a spec with an unresolvable blocker (escalates to you via Telegram).
