# File System Structure

Two directory trees: the **repo** (agent-facing) and the **vault** (human-facing). They're separate but linked — the vault has a reference view of task files, and the CLAUDE.md lives in both places.

---

## Repo Structure

Lives at the root of each project that uses this harness (not in the harness repo itself).

```
project-root/
│
├── CLAUDE.md                      ← agent operating manual (mirrored to vault 09-onboarding/)
├── orchestrator-state.yaml        ← live orchestrator state, never committed
│
├── tasks/
│   ├── backlog/
│   │   └── TASK-051.yaml
│   ├── in-progress/
│   │   └── TASK-053.yaml
│   ├── review/
│   │   └── TASK-050.yaml
│   └── done/
│       └── TASK-049.yaml
│
├── docs/                          ← agent-written docs, auto-updated
│   ├── specs/                     ← symlinked from vault 01-specs/ (or mirrored)
│   ├── adrs/                      ← symlinked from vault 02-adrs/
│   └── active-features/
│       └── data-export/
│           ├── progress.md        ← worker writes here
│           ├── decisions.md       ← micro-decisions during execution
│           └── qa-report.md       ← QA agent writes here
│
├── harness/                       ← the harness code itself
│   ├── orchestrator.py
│   ├── tmux_manager.py
│   ├── state_manager.py
│   ├── telegram_notifier.py
│   ├── linear_client.py
│   └── file_watcher.py
│
├── agent-prompts/                 ← prompt templates for each agent type
│   ├── worker.md
│   ├── task-breakdown.md
│   ├── qa-agent.md
│   ├── review-agent.md
│   ├── doc-closeout.md
│   ├── blocker-resolver.md
│   └── backlog-triage.md
│
├── templates/                     ← file templates for new tasks, specs, ADRs
│   ├── task.md
│   ├── progress.md
│   └── qa-report.md
│
└── scripts/
    ├── setup.sh                   ← initializes the directory structure for a new project
    ├── monitor.sh                 ← runs the monitor dashboard in window 7
    └── run-orchestrator.sh        ← starts the orchestrator loop
```

---

## Vault Structure

Lives at `agent-harness/docs/`. Human navigation layer over the system.

```
docs/
│
├── 00-inbox/                      ← raw ideas, unstructured
│   └── idea-{feature}.md
│
├── 01-specs/                      ← approved specs, agent contracts
│   ├── _template.md
│   └── feature-{name}.md
│
├── 02-adrs/                       ← architecture decisions, permanent
│   ├── _template.md
│   ├── ADR-001-{title}.md
│   └── ADR-002-{title}.md
│
├── 03-tasks/                      ← reference view of task files
│   └── README.md                  ← explains task files live in the repo
│
├── 04-active-features/            ← living docs during active development
│   └── {feature}/
│       ├── spec.md                (link to 01-specs/)
│       ├── progress.md            (worker writes here)
│       ├── decisions.md           (micro-decisions)
│       └── qa-report.md           (QA agent writes here)
│
├── 05-runbooks/                   ← how to operate things in production
│   └── {feature}-runbook.md
│
├── 06-api/                        ← endpoint docs, auto-maintained by Doc Closeout Agent
│   └── endpoints/
│       └── {resource}.md
│
├── 07-postmortems/                ← what broke, why, what changed
│   └── {date}-{incident}.md
│
├── 08-architecture/               ← system design (this section)
│   ├── agent-map.md
│   ├── feature-lifecycle.md
│   ├── file-system.md
│   ├── orchestrator-design.md
│   ├── system-overview.md         ← high-level, project-specific
│   ├── data-model.md              ← schema, updated by Doc Closeout Agent
│   └── diagrams/
│
├── 09-onboarding/                 ← how to use the harness
│   ├── README.md
│   ├── CLAUDE.md                  ← mirrored from project repo root
│   ├── setup-guide.md
│   ├── harness-projects.md
│   └── doc-maintenance.md
│
├── 10-product/                    ← PRDs, user stories, roadmap docs
│   ├── _prd-template.md
│   └── prd-{area}.md
│
├── 11-misc/                       ← reference files linked from tasks and specs
│   ├── README.md
│   ├── wireframes/                ← HTML/SVG/image wireframes
│   ├── screenshots/               ← design mockups, "build it like this" references
│   └── flows/                     ← user flows, sequence diagrams, state diagrams
│
└── _kanban/
    ├── features.md                ← one card per feature, links to spec
    └── bugs.md
```

---

## Who Reads/Writes What

| File / Directory | Written by | Read by |
|---|---|---|
| `00-inbox/` | You | You |
| `01-specs/` | You | Orchestrator, Task Breakdown Agent, Workers, QA Agent, Review Agent, Opus |
| `02-adrs/` | You (stubs from Orchestrator) | Workers, Opus, Task Breakdown Agent |
| `tasks/backlog/` | Task Breakdown Agent | Orchestrator |
| `tasks/in-progress/` | Orchestrator (moves files) | Orchestrator, Workers |
| `tasks/review/` | Orchestrator (moves files) | Orchestrator, QA Agent |
| `tasks/done/` | Orchestrator (moves files) | Orchestrator (dependency resolution) |
| `04-active-features/*/progress.md` | Workers | Orchestrator (watches for BLOCKER), You |
| `04-active-features/*/decisions.md` | Workers, Orchestrator (Opus output) | Workers, Review Agent |
| `04-active-features/*/qa-instructions-<id>.md` | Worker (the QA recipe) | QA Agent |
| `04-active-features/*/qa-report-<id>.md` | QA Agent (verdict in `status:`) | Orchestrator, You |
| `orchestrator-state.yaml` | Orchestrator | Orchestrator |
| `CLAUDE.md` | You | Every agent at session start |
| `10-product/` | You | You, Opus (during ADR check and spec review) |
| `11-misc/` | You (drop files in) | Workers (via `references` field in task file) |
| `_kanban/features.md` | You (spec approval), Orchestrator (transitions) | You |

---

## The orchestrator-state.yaml Schema

```yaml
active_workers:
  - task_id: TASK-051
    worktree: ../project-export-core
    window: 1
    started: "2026-05-27T14:30:00"
    model: sonnet
  - task_id: TASK-053
    worktree: ../project-export-ui
    window: 2
    started: "2026-05-27T14:35:00"
    model: sonnet

max_workers: 3

queue:
  - TASK-052              # waiting for a worker slot

last_action: "spawned TASK-053"
last_action_time: "2026-05-27T14:35:12"
cycle_count: 47
```

This file is never committed — add it to `.gitignore`. It's ephemeral operational state.

---

## The Task File Schema

```yaml
# tasks/backlog/TASK-051.yaml
id: TASK-051
title: "Export service core"
spec: "docs/specs/feature-data-export.md"
status: backlog          # backlog | in-progress | pr-opened | pr-updated | qa-failed | done | blocked | blocked-escalated
priority: high           # critical | high | medium | low
depends_on: []
blocks: [TASK-052, TASK-054]
can_parallelize_with: [TASK-053]
assigned_to: null        # orchestrator fills this
model: sonnet
linear_id: "LIN-51"
worktree: null           # orchestrator fills this when starting
window: null             # orchestrator fills this when starting
started: null
pr_url: null
pr_number: null
expected_behavior:       # QA rubric (task-breakdown authors; observable, spec-derived)
  - "given valid input, the export downloads as a CSV with one row per record"
branch: null             # worker fills task/<id> at PR time; empty = fix-mode signal
qa_instructions: null    # worker fills (path to its QA recipe)
qa_report: null          # QA agent fills (path to its report; its status: is the verdict signal)
failure_reason: null     # orchestrator fills on escalate/block
qa_failure_count: 0      # verdict-ladder pass counter (one retry: 0 or 1)
qa_run_attempts: 0       # orchestrator's infra-retry counter for dead QA runs (escalates at 2)
qa_out_of_scope_bugs: [] # reserved + UNUSED (random-bugs subsystem is future)
references:              # files the worker reads alongside the spec — wireframes, mockups, etc.
  - docs/11-misc/wireframes/export-modal.html
  - docs/11-misc/screenshots/export-design-v2.png
```

---

## Key Rule: Source of Truth

- The **spec** is the source of truth for what should be built
- The **task file** is the source of truth for execution state
- The **orchestrator-state.yaml** is the source of truth for what the orchestrator is doing right now
- The **kanban** is navigation, not state — it mirrors task file statuses, it does not own them
