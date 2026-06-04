# Tasks

Task files live in the **project repo**, not in this vault. This directory is a reference.

Task files are **feature-scoped**: every feature (one approved spec) gets its own workspace under `tasks/<feature>/`, where `<feature>` is the implementing spec's frontmatter `id` (e.g. spec `docs/specs/feature-data-export.md` with `id: data-export` → `tasks/data-export/`).

```
project-root/
└── tasks/
    └── data-export/        ← one workspace per feature (= spec's frontmatter id)
        ├── backlog/        ← tasks waiting to be picked up
        ├── in-progress/    ← tasks a worker is currently executing
        ├── review/         ← tasks where PR is open, QA running
        ├── done/           ← completed tasks
        └── board.md        ← per-feature TASK kanban (owner-grouped columns)
```

The orchestrator reads and moves each task file between the four state subdirs **of its own feature workspace** (the grandparent dir of any task file is its feature). Do not edit task files manually while a worker is active on them.

### Two kanban levels

- **Global spec board** — `docs/_kanban/features.md`, one card per feature.
- **Per-feature task board** — `tasks/<feature>/board.md`, columns grouped by who owns the next action: **Backlog · In Progress · In Review · QA Failed · Human Review · Blocked · Done** (the `review/` folder's hidden states surface as In Review = pr-opened/pr-updated, QA Failed = qa-failed, Human Review = qa-passed/blocked-escalated). It is seeded by the task-breakdown agent (all tasks start in Backlog); the orchestrator MOVES cards as status changes, never regenerates the board.

## Task File Format

See [[file-system]] for the full schema. Key fields:

- `status` — the orchestrator watches this field for transitions
- `feature` — the implementing spec's frontmatter `id`; determines which `tasks/<feature>/` workspace the file lives in (authored by task-breakdown)
- `depends_on` — list of task IDs that must be in `done/` before this task can start
- `linear_id` — the Linear issue ID, filled in by the orchestrator after task breakdown

## Reading Task State

```bash
# All active tasks for a feature
ls tasks/data-export/in-progress/

# All active tasks across every feature workspace
ls tasks/*/in-progress/

# Full state
cat orchestrator-state.yaml

# A specific task
cat tasks/data-export/in-progress/TASK-051.yaml
```
