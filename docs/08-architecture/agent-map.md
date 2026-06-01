# Agent Map

All agents in the system, their roles, models, and concurrency constraints.

---

## tmux Layout at Peak Load

```
window 0  — Orchestrator          (always running)
window 1  — Worker: TASK-NNN      (feature/branch-a)
window 2  — Worker: TASK-NNN      (feature/branch-b)
window 3  — Worker: TASK-NNN      (feature/branch-c)
window 4  — QA Agent              (current PR)
window 5  — Review Agent          (same PR, parallel to QA)
window 6  — Shared slot           (Opus / Task Breakdown / Doc Closeout)
window 7  — Monitor               (live state, logs, cost)
```

---

## Always-On

### Orchestrator
- **Window:** 0
- **Model:** Sonnet
- **Concurrent:** 1, always running
- **Role:** The state machine. Reads files, takes one action per cycle, writes state, sleeps. Dispatches all other agents. The only agent that calls Linear and Telegram.
- **Inputs:** `orchestrator-state.yaml`, all task files, `progress.md` files
- **Outputs:** `orchestrator-state.yaml`, Linear API calls, Telegram messages, spawns/tears down workers
- **Lifespan:** Runs indefinitely. Stateless — can restart at any point without losing progress.

---

## Up to 3–4 Simultaneously

### Worker Agents
- **Windows:** 1, 2, 3 (up to max_workers)
- **Model:** Sonnet (Haiku for trivial tasks: copy changes, config tweaks)
- **Concurrent:** Max 3–4 (configurable in `orchestrator-state.yaml`)
- **Role:** Execute a single task end-to-end. Read spec + task file + CLAUDE.md. Write code. Open PR. Signal done.
- **Inputs:** Task file, spec, CLAUDE.md, relevant ADRs
- **Outputs:** Code commits, PR via `gh pr create`, `progress.md` updates, `decisions.md` entries
- **Lifespan:** 30 minutes to a few hours per task. Torn down after PR is opened.
- **Worktree:** Each runs in its own `git worktree` — isolated working directory, no conflicts with other workers.

---

## Sequential, One at a Time

### QA Agent  (behavioral, async — issue #4)
- **Model:** Sonnet
- **Mode:** Async tracked agent (in `active_workers` with `role: qa`), its own worktree on the `task/<id>` branch. Bounded by `max_workers` like a worker — within one spec, several tasks can be in QA at once.
- **Role:** **RUN the feature** and judge observed behaviour against the task's `expected_behavior` rubric. Two checks: does the recipe execute as claimed; does the claimed/observed behaviour satisfy the rubric. **Never reviews the diff.**
- **Inputs:** task `expected_behavior` (rubric, the grading truth), worker's `qa_instructions` recipe (the map), the project run/launch skill, its worktree
- **Outputs:** `qa-report-<id>.md` whose `status:` is `WIP` → `looks-good` / `needs-changes` / `escalate` (the durable verdict the orchestrator polls). Does NOT set the task `status`.
- **Triggered by:** Orchestrator at `status: pr-opened` / `pr-updated` (step 4D)

### Opus Fixer  (worker fix-mode — issue #4)
- **Model:** Opus
- **Mode:** Async tracked agent (`role: fixer`), its own worktree on the existing `task/<id>` branch.
- **Role:** On a `needs-changes` verdict, fix only what QA flagged, push to the **same** branch (no new PR), set `status: pr-updated` → re-QA. The verdict ladder is **one** retry.
- **Triggered by:** Orchestrator at `status: qa-failed` (step 4D)

### Review Agent  (deferred — build order D)
- **Model:** Sonnet
- **Role:** A *code* reviewer that reads the diff vs the spec and posts PR comments via `gh pr review`. Separate from QA (behavioral) and **not** part of the retry ladder.
- **Status:** not yet built.

---

## On Demand, Short-Lived (Shared Slot — Window 6)

These never run simultaneously. The orchestrator queues them into the same window.

### Task Breakdown Agent
- **Model:** Sonnet
- **Role:** Decompose an approved spec into atomic task files with dependency frontmatter.
- **Inputs:** Approved spec, existing ADRs, CLAUDE.md
- **Outputs:** N task files written to `tasks/backlog/`, Linear epic + issues created
- **Lifespan:** ~2 minutes, single shot
- **Triggered by:** Orchestrator detects `status: approved` on a new spec

### Opus — Senior Agent
- **Model:** Opus
- **Role:** Judgment calls only. Architectural questions, blocker resolution, decisions that affect >2 files or the schema.
- **Inputs:** The specific question + spec + relevant ADRs + decisions so far
- **Outputs:** Decision written to `decisions.md`, sent to blocked worker via tmux send-keys
- **Lifespan:** Single shot, ~1 minute
- **Triggered by:** Worker writes a BLOCKER to `progress.md` that the orchestrator can't resolve from existing docs

### Doc Closeout Agent
- **Model:** Haiku (mechanical writing)
- **Role:** Post-merge documentation cleanup.
- **Inputs:** Merged PR diff, spec
- **Outputs:** Updated `06-api/` if new endpoints, `05-runbooks/` stub if ops-relevant, `08-architecture/data-model.md` if schema changed, archives `04-active-features/{feature}/`
- **Lifespan:** ~5 minutes
- **Triggered by:** Orchestrator detects merge

---

## Scheduled

### Backlog Triage Agent
- **Model:** Opus
- **Window:** 6 (shared slot, run at 6am daily)
- **Role:** Re-prioritize the backlog. Read all backlog tasks, current Linear state, recent decisions. Reorder task priority fields. Post daily plan to Telegram.
- **Inputs:** All `tasks/backlog/*.yaml`, Linear state, recent `decisions.md` entries
- **Outputs:** Updated priority fields on task files, Telegram daily plan message
- **Lifespan:** ~5 minutes, once daily

---

## What's Never Running Simultaneously

- Two QA agents (sequential — one PR at a time)
- Two Opus invocations (orchestrator queues them if two blockers hit at once)
- Task Breakdown + workers on the same feature (breakdown must complete before workers start)
- Two agents in the shared slot (window 6 is a single-occupancy slot)

---

## Invocation Hierarchy

```
You
└── Orchestrator
    ├── Worker Agents (up to 3-4)
    ├── QA Agent
    ├── Review Agent
    ├── Task Breakdown Agent
    ├── Doc Closeout Agent
    └── Opus (Senior Agent)
        └── Backlog Triage (scheduled)
```

Nothing invokes anything except the orchestrator. Workers never spin up other agents. Clean hierarchy with one dispatcher.
