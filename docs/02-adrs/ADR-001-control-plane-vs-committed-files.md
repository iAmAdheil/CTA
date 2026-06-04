---
status: accepted
date: 2026-05-28
supersedes: null
superseded_by: null
---

# ADR-001: Control-plane state lives in the main worktree, not in git

## Context

Workers run in isolated git worktrees (`../wt-<task-id>` on a `task/<task-id>` branch) so
they can edit code without colliding. This was validated end-to-end on 2026-05-28 (see
`docs/09-onboarding/harness-test-playbook.md`), and the test surfaced a structural problem with
how task files cross the worktree boundary.

A git worktree is a separate physical directory checked out **from a commit**. Two facts follow:

1. **Uncommitted/untracked files in the main worktree are not visible in a new worktree.** The
   orchestrator creates a task file (breakdown writes `tasks/<feature>/backlog/<id>.yaml`, the
   orchestrator `mv`s it to `tasks/<feature>/in-progress/` and edits frontmatter) but never commits
   it. A worktree cut from `main`'s HEAD therefore contains *neither* copy. A worker told to `Read
   tasks/<feature>/in-progress/<id>.yaml` (a relative path resolving inside its worktree) fails outright.

2. **A worktree's edits are trapped on its branch.** If the worker edited its *own* worktree copy
   of the task file and set `status: pr-opened`, that change would live only on `task/<id>` (or
   only in that worktree's working dir). The orchestrator reads the **main** worktree on `main`,
   so it would never observe the status flip. The signal can't get home without a git round-trip.

The underlying mistake is making the task file serve two masters at once: **source code** (should
travel with branches, flow through PRs, live in history) and **orchestrator control-plane state**
(a live status the orchestrator polls while a worker holds a branch). Those have opposite
requirements, and conflating them is what produced both failures.

Absolute paths are the escape hatch: a branch is a pointer to a commit, not a folder, so an
absolute path always addresses one physical file in the main worktree regardless of any branch.
But applying that ad hoc (e.g. injecting an absolute path into one spawn prompt) only patches one
symptom.

## Decision

Split every harness-touched path into two buckets by a single test:

> **Does the file change *during* a worker's run and need to be read/written live by another
> agent across the worktree boundary?**

- **Yes → control-plane state.** It is gitignored, lives **only in the main worktree**, and every
  agent (orchestrator and worker alike) addresses it by **absolute path**. A worker self-locates
  the main worktree with `MAIN=$(dirname "$(git rev-parse --git-common-dir)")` and reads/writes
  `$MAIN/tasks/...`. Workers never commit control-plane files.
- **No → committed project file.** Ordinary repo content. The human/agents commit it on their
  normal schedule; the worktree carries a frozen snapshot from the commit it was cut from. Workers
  read these via relative paths inside their worktree.

Classification:

| Bucket | Paths |
|---|---|
| **Control-plane** (gitignored, main-worktree-only, absolute path) | `orchestrator-state.yaml`, `tasks/`, `docs/active-features/<feature>/{progress.md,decisions.md,qa-report.md}` |
| **Committed** (normal repo files, worktree snapshot, relative path) | source code, `CLAUDE.md`, `docs/specs/`, `docs/adrs/`, `docs/api/`, `docs/runbooks/`, `docs/architecture/` |

The only live-shared signal that must cross the boundary is the task file's `status` field; it
goes through the main worktree. Everything a worker needs as *context* (spec, `CLAUDE.md`, the
codebase) is committed, so the worktree gets a clean, consistent snapshot to build against — which
is the entire point of using worktrees.

`setup.sh` gitignores the control-plane paths. The `/orchestrator` skill passes the absolute
task-file path in the spawn prompt; the `/worker` skill reads/writes its task file by absolute path
and reads spec/`CLAUDE.md`/code from its worktree.

## Consequences

- **No new commit discipline for the human.** Approving a spec (`status: approved`) and committing
  it is one motion, the same as committing any file — not a harness-imposed gate. Control-plane
  state is never committed, so it never appears in `git status` noise.
- **Commit timing stops being a correctness issue.** A worker's worktree snapshots `main`'s HEAD
  at spawn, so committed context is what it sees. Because control-plane state is read by absolute
  path from the main worktree, the status handoff is independent of any branch or commit.
- **Workers reach outside their worktree** for control-plane files. This is deliberate and the one
  exception to worktree isolation; it is confined to gitignored state, never code.
- **The durable record of tasks is not git.** Local task files are working state; history/audit
  lives in the spec, ADRs, and (Stage 5) Linear. Completed task files are archived to `tasks/<feature>/done/`
  locally.
- **`docs/active-features/` is control-plane, not a PR deliverable.** The orchestrator/QA/
  blocker-resolver exchange these live. The blocker-resolver's decision still reaches a running
  worker via the tmux `nudge`, not by the worker reading `decisions.md` across the boundary.

Related: `harness-test-playbook.md`, build-roadmap "Docs + cleanup", memory
`harness-worker-runtime-gaps`.
