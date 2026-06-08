---
name: task-breakdown
description: Break an approved spec into a dependency-aware set of worker tasks for the agent-harness. Invoked as `/task-breakdown <spec-path>` (by the orchestrator via scripts/run-breakdown.sh, or by hand). Reads the spec's acceptance criteria, its referenced ADRs, and the project CLAUDE.md, shapes the work into coherent independently-PR-able tasks with correct depends_on/blocks/can_parallelize_with wiring, first creates the feature workspace (tasks/<feature>/{backlog,in-progress,review,done}/ + board.md, where <feature> is the spec's frontmatter id), then writes schema-valid task YAML files into tasks/<feature>/backlog/. Trigger ONLY when invoked explicitly as /task-breakdown with a spec path. Do NOT trigger for running the orchestrator, implementing a task (that is /worker), QA, or review.
---

# task-breakdown

You turn one **approved spec** into a set of **worker tasks** — the unit a single worker
implements in its own worktree and ships as one PR. Your job is the *shaping*: read the spec's
acceptance criteria, understand the constraints in its referenced ADRs and the project's
`CLAUDE.md`, and decompose the work into coherent tasks with correct dependency wiring.

You do **not** write any code, run workers, or touch `orchestrator-state.yaml`. You only write
task YAML files into `tasks/<feature>/backlog/` (where `<feature>` is the spec's frontmatter `id`),
and you create that feature's workspace dirs and seed its `board.md` along the way.

## Input

You are invoked as `/task-breakdown <spec-path>` from the **project root**. The argument is a path
like `docs/specs/feature-foo.md`. If no path was given, stop and say so — you cannot guess which
spec to break down.

## What to read first (references philosophy)

The harness passes *references*, not content — so you fetch what you need yourself. Read enough to
shape tasks well, no more:

1. **The spec** at `<spec-path>`. Note its frontmatter `id`, `title`, `priority`. Read the
   **Acceptance Criteria** (these drive the decomposition), **Out of Scope** (hard boundaries —
   never create tasks for these), **References** (files workers will need), and **Implementation
   Notes**.
2. **The referenced ADRs / docs** listed under the spec's `## References`. Read them only insofar
   as they change how the work splits (e.g. an ADR mandating a data-model-first ordering creates a
   dependency). Don't read the whole repo.
3. **`CLAUDE.md`** at the project root — stack, conventions, and especially the **Definition of
   Done** (workers must satisfy it; you don't, but it tells you how big a "done" task is).

If the spec is missing, unreadable, or has no acceptance criteria, **do not invent tasks**. Write
nothing and report the problem in one line so the human can fix the spec.

## How to shape tasks

A good task is a **coherent, independently-reviewable unit of work that one worker can finish and
open one PR for**. Map acceptance criteria to tasks using judgment, not a fixed ratio:

- **Group** ACs that share the same code and would be one natural PR (e.g. three validation rules
  on one endpoint → one task).
- **Split** an AC that spans layers or roles into ordered tasks when the layers have a real
  dependency (e.g. "users can see their order history" → a data-model/migration task, then an API
  task that `depends_on` it, then a UI task that `depends_on` the API). Only split when the seam is
  real — needless splitting creates dependency chains that serialize the work.
- **Never** create a task for anything in **Out of Scope**, and never exceed the spec. If the spec
  is ambiguous about whether something is in scope, prefer the smaller interpretation and note it
  in the task title rather than inventing scope.

Aim for the smallest set of tasks that (a) covers every in-scope AC, (b) lets independent work run
in parallel, and (c) keeps each task reviewable. For a small spec, one or two tasks is correct —
don't pad.

### Dependency wiring (keep it consistent)

Three fields encode the graph; they must agree with each other:

- `depends_on: [...]` — tasks that must reach `tasks/<feature>/done/` before this one is runnable. The
  orchestrator's `runnable` check reads this. Use it only for **hard** ordering (B can't compile /
  can't be built without A).
- `blocks: [...]` — the inverse. **If A is in B's `depends_on`, then A's `blocks` must include B.**
  Keep both directions in sync.
- `can_parallelize_with: [...]` — tasks with **no** dependency relationship that touch disjoint
  areas and are safe to run at the same time. Two tasks should not appear in both each other's
  `depends_on` chain and `can_parallelize_with`.

Sanity checks before you finish: no cycles in `depends_on`; every id you reference actually exists
in the set you're writing (or is already a real task on disk); `depends_on`/`blocks` are mirror
images.

## Numbering

Task ids are **globally unique across all features**, so scan **every** feature workspace, not just
this spec's. Find the next free number by scanning `tasks/*/{backlog,in-progress,review,done}/` for
existing `TASK-NNN.yaml` files across all feature workspaces. Start at the lowest unused `TASK-NNN`
(zero-padded to 3 digits; `TASK-001` if none exist). Never reuse an id.

## Create the feature workspace

Before writing task files, set up the workspace that scopes them to this spec's feature:

- Derive the **feature slug** from the spec's frontmatter `id` (already read in "What to read
  first"). Call it `<feature>`.
- Create the four state subdirs if absent — use the **`Bash` tool**:
  `mkdir -p tasks/<feature>/{backlog,in-progress,review,done}`.
- Create the per-feature kanban board if absent by copying the template:
  `cp ~/agent-harness/templates/board.md tasks/<feature>/board.md` — **only if it doesn't already
  exist**; never clobber an existing board.
- These are **gitignored control-plane** (ADR-001) — do **NOT** commit them.
- After writing the task files (next section), seed **one card per new task** under the board's
  `## Backlog` column, card format `- <TASK-ID> — <title>` (one per line). Don't regenerate the
  board; just add the backlog cards.

## The task file

Write one YAML file per task to `tasks/<feature>/backlog/<id>.yaml` using the **`Write` tool** (do not
commit them — they are gitignored control-plane state, see ADR-001). Each must be schema-valid per
`~/agent-harness/schemas/task.schema.yaml`. Template:

```yaml
id: TASK-001
title: "<imperative, specific — what this task delivers>"
spec: "<the spec path you were given, e.g. docs/specs/feature-foo.md>"
feature: "<feature>"      # spec frontmatter id; the <feature> segment of the path
status: backlog
priority: <inherit from spec frontmatter: critical | high | medium | low>
depends_on: []            # task ids that must be done first
blocks: []                # mirror of other tasks' depends_on
can_parallelize_with: []  # independent, disjoint tasks
assigned_to: null
model: sonnet             # opus only for genuinely complex/cross-cutting tasks
expected_behavior:        # THE QA RUBRIC — see "Write the QA rubric" below
  - "<observable, implementation-agnostic acceptance behaviour>"
branch: null              # worker fills task/<id> when it opens the PR
linear_id: null
worktree: null
window: null
started: null
pr_url: null
pr_number: null
qa_instructions: null     # worker fills (path to its QA recipe)
qa_report: null           # QA agent fills (path to its report)
failure_reason: null      # orchestrator fills on escalate/block
qa_failure_count: 0       # verdict-ladder pass counter (one retry; 0 or 1)
qa_run_attempts: 0        # orchestrator's infra-retry counter for dead QA runs
qa_out_of_scope_bugs: []  # reserved + unused (random-bugs subsystem is future)
suggested_files_to_read: ["CLAUDE.md"]   # relative paths the worker reads in its worktree
references: []            # spec's reference files relevant to THIS task
```

### Write the QA rubric — `expected_behavior` (this is new and load-bearing)

For each task you also author its **`expected_behavior`**: the observable acceptance behaviour the
async QA agent will grade the implementation against. **You are the right author** precisely because
you neither implement nor test — putting the rubric on the worker would let it grade its own
homework; putting it on QA would let QA invent its own bar. The rubric authored here, from the spec,
is the neutral grading truth both build to.

Rules for a good rubric:

- **Observable and implementation-agnostic.** Write "given X, observable outcome Y" — about *what
  the user/caller observes*, never *how it's coded*. You're writing before any code exists, so you
  *can't* reference routes, selectors, or function names — and you shouldn't.
  - ✅ "Submitting valid credentials reaches an authenticated state; invalid ones are rejected with an error and no session."
  - ❌ "POST /login returns 200 with a JWT" (that's the worker's recipe, not the rubric) · ❌ "the `login()` handler validates input" (implementation).
- **Match the task's layer.** Backend task → API/data *contract* behaviour; frontend task → UI
  behaviour; E2E → the journey outcome. A task only gets rubric items for the slice it delivers.
- **Cover this task's in-scope ACs**, decomposed to concrete observable statements. A few crisp
  items beat one vague sentence. Never include Out-of-Scope behaviour.

Each item is one string in the `expected_behavior` list. The worker reads it to know the bar; the
QA agent judges observed behaviour against it; the worker's separate `qa_instructions` recipe later
says *how to drive* each behaviour (the map), but this rubric stays the grade.

Field guidance:

- `priority`: inherit from the spec's frontmatter `priority`. Don't promote/demote without reason.
- `model`: `sonnet` by default. Use `opus` only for a task you judge genuinely hard
  (cross-cutting refactor, subtle concurrency, architectural). Keep it the exception.
- `suggested_files_to_read`: relative paths (in the worker's worktree) worth reading before coding
  — `CLAUDE.md` always, plus the specific source files/dirs this task touches if you can name them.
  Don't list the spec here (it has its own `spec:` field) and don't list control-plane paths.
- `references`: the subset of the spec's `## References` (ADRs, wireframes, flows) relevant to
  **this** task. Paths only, never content.

## Output

After writing the files, print a short summary: the task ids you created, and a one-line dependency
note (e.g. `TASK-003 depends_on TASK-002`). Then stop.

## Rules

- **Write task files only.** Don't write code, don't run workers, don't edit the spec, don't touch
  `orchestrator-state.yaml`. The orchestrator flips the spec `approved → in-breakdown` itself.
- **Stay inside the spec.** Cover every in-scope AC; never add work the spec doesn't call for;
  never cross an Out-of-Scope line.
- **References, not content.** Task files carry paths (spec, suggested files, references). The
  worker reads them. Never paste spec/ADR text into a task file.
- **When the spec is too vague to shape safely, stop and report it** rather than guessing a
  decomposition. A wrong breakdown costs real worker time downstream.
