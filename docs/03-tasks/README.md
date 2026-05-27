# Tasks

Task files live in the **project repo**, not in this vault. This directory is a reference.

```
project-root/
└── tasks/
    ├── backlog/    ← tasks waiting to be picked up
    ├── in-progress/ ← tasks a worker is currently executing
    ├── review/     ← tasks where PR is open, QA running
    └── done/       ← completed tasks
```

The orchestrator reads and moves task files between these directories. Do not edit task files manually while a worker is active on them.

## Task File Format

See [[file-system]] for the full schema. Key fields:

- `status` — the orchestrator watches this field for transitions
- `depends_on` — list of task IDs that must be in `done/` before this task can start
- `linear_id` — the Linear issue ID, filled in by the orchestrator after task breakdown

## Reading Task State

```bash
# All active tasks
ls tasks/in-progress/

# Full state
cat orchestrator-state.yaml

# A specific task
cat tasks/in-progress/TASK-051.yaml
```
