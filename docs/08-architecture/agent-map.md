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

### QA Agent
- **Window:** 4
- **Model:** Sonnet
- **Concurrent:** 1 (QA is sequential — one PR at a time)
- **Role:** Verify the PR against the spec using automated tests + browser navigation.
- **Inputs:** PR diff, spec file, staging URL, pre-existing failure list
- **Outputs:** `qa-report.md` with verdict (PASS / CONDITIONAL / FAIL), Linear comment
- **Lifespan:** 10–30 minutes per PR
- **Tools:** Playwright + Stagehand (or Browser Use) for dynamic navigation
- **Triggered by:** Orchestrator when a worker signals `status: pr-opened`

### Review Agent
- **Window:** 5
- **Model:** Sonnet
- **Concurrent:** 1 (runs alongside QA, same PR)
- **Role:** Read the diff, check it against the spec, flag anything wrong before human review.
- **Inputs:** PR diff, spec, `decisions.md`
- **Outputs:** GitHub PR review comment via `gh pr review`, summary to Linear
- **Lifespan:** ~5 minutes
- **Triggered by:** Same event as QA agent (PR opened)

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
