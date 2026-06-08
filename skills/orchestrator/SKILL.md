---
name: orchestrator
description: Run one cycle of the agent-harness control loop in a project that uses the harness at ~/agent-harness. Reads `orchestrator-state.yaml` and task files under feature-scoped workspaces `tasks/<feature>/{backlog,in-progress,review,done}/`, detects state transitions that need action (approved spec → invoke task-breakdown, runnable backlog task with free worker slots → spawn worker, task status `pr-opened` → tear down worker pane + move to review, task status `done` → archive into `tasks/<feature>/done/`, task status `blocked` → leave for the blocker-resolver), executes those actions via the harness's Python CLI wrappers (`conda run -n harness python -m harness.state_manager`, `conda run -n harness python -m harness.tmux_manager`, `bash ~/agent-harness/scripts/run-breakdown.sh`), and exits. Trigger this skill whenever the user invokes `/orchestrator`, runs `claude /orchestrator`, or says any of: "run the orchestrator", "tick the harness", "orchestrate", "orchestrate one cycle", "advance the loop", "advance the harness", "process state transitions", "check what tasks should run next", "spawn workers for what's ready", "react to status changes", "move task files to where they should be", "run a harness cycle", or any phrasing that means executing one iteration of the harness control loop. Also trigger when the user wants to manually spawn a worker from a task file or move a task file between `tasks/` subdirectories — those are the orchestrator's responsibilities and routing them through this skill keeps state consistent. Do NOT trigger for: writing the worker, breakdown, QA, or review agents themselves; editing the harness Python wrappers; or unrelated work outside the harness control loop.
---

# Orchestrator — one cycle

You are running exactly **one cycle** of the agent-harness control loop. The cadence — every 30 seconds, every 5 minutes, on file-change, manual — is handled by an external driver (cron, `/loop`, a file-watcher, or the user typing `/orchestrator`). You do not loop, sleep, or poll. Read state, react to transitions, write state, exit.

## Why one cycle, not a `while True`

Each cycle is stateless. You have no memory of what happened last time — the truth is on disk in `orchestrator-state.yaml` and the task files. That's deliberate: it means crashes are free (the next cycle picks up where the last left off), and it means a human can pause the harness by simply not invoking it. Don't fight this design by trying to be clever about caching anything across cycles.

## Where you are

The user's current working directory is a **project repo** that uses the harness. It should have:

- `orchestrator-state.yaml` — live state (atomic-written by `harness.state_manager`)
- `tasks/<feature>/{backlog,in-progress,review,done}/` — task YAML files, one workspace per feature (`<feature>` = the spec's frontmatter id); the orchestrator moves a task between the four state subdirs of its own feature workspace
- `docs/specs/` — specs (with frontmatter `status: draft | approved | …`)
- `docs/active-features/<feature>/` — `progress.md`, `decisions.md`, plus per-task `qa-instructions-<id>.md` (worker's QA recipe) and `qa-report-<id>.md` (QA agent's verdict; its `status:` line is what you poll)
- `CLAUDE.md` — project operating manual

The **harness itself** lives at `~/agent-harness/`. You call into it via:

- `conda run -n harness python -m harness.state_manager …`
- `conda run -n harness python -m harness.tmux_manager …`
- `bash ~/agent-harness/scripts/run-breakdown.sh <spec-path>`

The wrappers run inside the dedicated `harness` conda env (created by `setup.sh`), so always invoke them with the `conda run -n harness` prefix — `python -m harness.*` on its own won't resolve the package. If `conda run -n harness …` errors with "EnvironmentLocationNotFound" or similar, the env is missing; tell the user to run `bash ~/agent-harness/scripts/setup.sh`.

If any of those project paths are missing (e.g. there's no `tasks/` directory), stop and tell the user to run `bash ~/agent-harness/scripts/setup.sh` first. Do not try to recover by creating directories yourself — that bypasses setup's other side effects (CLAUDE.md starter, spec template, .gitignore entries).

## What one cycle does, in order

Work through the steps below sequentially. Each step is a check; many cycles will have nothing to do at some steps, and that's fine.

### Board cards — the primitive several steps below use

The project keeps **persistent** Obsidian Kanban boards that mirror live state for a human to glance at. There are **two levels**:

- **One global specs board** — `docs/_kanban/specs-board.md` — columns **Under Review · Approved · Acknowledged · Blocked**. One card per spec/feature.
- **One task board per feature** — `tasks/<feature>/board.md` — columns **Backlog · In Progress · In Review · QA Failed · Human Review · Blocked · Done**. One card per task. This board lives **inside the feature's workspace** (created and seeded by task-breakdown), not in `docs/_kanban/`. A task's board is the one for **its** feature — take `<feature>` from the task file's `feature:` field (equivalently, the grandparent dir of the task file: `tasks/<feature>/<state>/<id>.yaml`).

These are **never regenerated from scratch** — you move one card at a time; every other card and anything a human added by hand stays untouched. If a board file is missing, first create it from the skeleton (frontmatter ```--- kanban-plugin: basic ---``` followed by each `## Column` header) before editing — for a feature task board, copy `~/agent-harness/templates/board.md` to `tasks/<feature>/board.md`.

The only board operation you ever perform is **idempotent**:

> **ensure card `C` is in column `COL`** (on the relevant board — the global specs board for a spec card, the task's own `tasks/<feature>/board.md` for a task card): Read the board. If `C` is already under `## COL`, do nothing. Otherwise remove `C`'s line from whichever column currently holds it and add it under `## COL`. If `C` has no card at all, add it under `## COL`. **Never duplicate a card.**

Idempotency is load-bearing: teardown (step 4) is replayed after a crash, so "ensure in column" must be a safe no-op when the card is already right.

**Card text** — spec (specs board): `- [[<spec-file-stem>]] — <title>` (e.g. `- [[feature-export]] — Data export`); task (its feature board): `- <TASK-ID> — <title>` (e.g. `- TASK-051 — Export service core`).

**Status → column:**

| Specs board | spec `status:` | | Tasks board | task `status:` |
|---|---|---|---|---|
| Under Review | `draft` | | Backlog | `backlog` |
| Approved | `approved` | | In Progress | `in-progress` |
| Acknowledged | `in-breakdown` | | In Review | `pr-opened`, `pr-updated` |
| Blocked | `blocked` | | QA Failed | `qa-failed` |
| | | | Human Review | `qa-passed`, `blocked-escalated` |
| | | | Blocked | `blocked` |
| | | | Done | `done` |

(`superseded` specs won't occur until later stages. The QA-ladder columns — QA Failed (a needs-changes task awaiting/under the Opus fixer) and Human Review (`qa-passed` awaiting your merge, or `blocked-escalated` needing your call) — are live as of the QA loop, step 4.)

### 1. Read state

```bash
conda run -n harness python -m harness.state_manager read
```

If the file is missing, run `conda run -n harness python -m harness.state_manager init` first. The output is JSON — note `active_workers` (list of in-flight workers with task_id/pane/worktree) and `max_workers` (the cap on concurrency).

### 2. Next approved spec → task breakdown (one spec at a time)

**First, sync the specs board.** For each `docs/specs/*.md` (skip `_template.md`), read its `status:` and *ensure its card is in the matching column* (per the table above). This is a read-only reflection of spec state — distinct from the breakdown decision below — and it's what makes your approved backlog visible while serial-per-spec holds the rest back.

The harness works **one spec at a time** (serial-per-spec). For the breakdown *decision*, don't eyeball the directory — ask the gate which spec, if any, to break down now:

```bash
conda run -n harness python -m harness.state_manager next-spec
```

This returns **at most one** spec path, and only when it's safe to start it:

- It returns **nothing** (empty output) while any task has an **active** status —
  `backlog`, `in-progress`, `pr-opened`, `pr-updated`, or `qa-failed` (computed in
  `state_manager`, which sweeps every feature workspace `tasks/*/<state>/`
  regardless of which subdir holds the task). Those last three
  mean the async QA→Opus→re-QA loop is (or will be) running on this spec's tasks,
  so the spec isn't drained. The gate releases the next spec only once every task
  has reached a **"now handled by the human"** state — `qa-passed`, `done`,
  `blocked`, or `blocked-escalated`. It still does **not** wait on your manual
  merge: the principle is "advance when the harness has no automated work left."
- Otherwise it returns the **highest-priority** spec with `status: approved`
  (critical → high → medium → low, tie-broken by filename). Specs and the
  `_template.md` without `status: approved` are skipped.

If it printed nothing, skip this step — either a spec is mid-flight or there are no approved specs.

If it printed a spec path, **first run the architectural advisor, then break down only if it says PROCEED:**

**2a. Architectural advisor (Opus gate).** Before any tasks are created, Opus checks whether the spec forces an architectural decision not already settled by an existing ADR or convention:

```bash
bash ~/agent-harness/scripts/run-advisor.sh <spec-path>
```

This is **synchronous** (`claude --print`, Opus). Capture both its **exit code** and **stdout**. Then decide the verdict from the **durable side effect** (the spec's status on disk), using the stdout marker only as positive confirmation — never trust the prose line alone:

1. **Exit code non-zero** (budget exceeded, crash) → failure. Do not break down; leave the spec `approved` so a later cycle retries the advisor; log it. Stop here.
2. **Re-read the spec's frontmatter `status:`** (the advisor may have rewritten it):
   - **`status: blocked`** → the advisor blocked the spec (it already appended the reason). This is the source of truth — the `ADVISOR_VERDICT: BLOCK` line is just the log. Do **not** run breakdown, do **not** touch the spec. **Board:** ensure the spec's card is in **Blocked**. It's out of the `next-spec` pool until a human resolves it and flips it back to `approved`. Log and move on.
   - **still `status: approved`, AND stdout contains an `ADVISOR_VERDICT: PROCEED` line** → the advisor cleared it (possibly after writing a `status: proposed` ADR + arch-doc updates). Continue to 2b.
   - **still `status: approved` but no `PROCEED` marker** → the advisor exited 0 without a clean verdict (truncated/incomplete). Treat as failure: do not break down, leave `approved`, log, retry next cycle.

The rule: **never fall through to breakdown on an unvetted spec.** Breakdown happens only on the explicit approved-status + PROCEED-marker case.

**2b. Task breakdown (only on PROCEED).**
0. **Create the feature integration branch, then commit the spec onto it — before breakdown.** Every task in this spec is cut from, and PRs into, a per-spec branch `feature/<spec-id>` (`<spec-id>` = the spec's frontmatter `id`, the same `<feature>` the workspace uses); the human merges `feature/<spec-id>` → the release branch once the spec is green. Create + check it out first (from the **release** branch — the helper defaults to main/master, which is right even if you're still on a previous spec's feature branch):

   ```bash
   conda run -n harness python -m harness.tmux_manager ensure-branch \
     --name feature/<spec-id> --checkout
   ```

   This is idempotent (no-op if the branch exists) and leaves your main worktree **on** `feature/<spec-id>`, so the spec commit below lands there and step-3 workers base onto it. Then commit the spec + any advisor ADRs/arch-docs onto the feature branch — workers read the spec/ADRs/`CLAUDE.md` by **relative path from their worktree, a committed git snapshot** of the feature branch, so an *uncommitted* spec is invisible to them (every worker would block with "referenced spec absent"):

   ```bash
   git add docs/specs/<spec>.md docs/adrs/ docs/architecture/ 2>/dev/null
   git commit -m "spec: <spec-id> approved + advisor ADRs" || true   # no-op if nothing to commit
   # Real-remote mode: publish the feature branch so the workers' `gh pr create --base
   # feature/<spec-id>` has a remote base to target, and so merge-branch's `fetch origin
   # feature/<spec-id>` (step 4C) resolves. Safe no-op in local-marker mode (no remote).
   git remote | grep -q . && git push -u origin "feature/<spec-id>" || true
   ```

   `|| true` keeps the commit a safe no-op when the human already committed. The `git push` line only runs when a remote exists; in local-marker mode (the toy/MVH path) there's no remote, so it's skipped and the feature branch stays local — exactly as before. Do **not** commit `tasks/`, `orchestrator-state.yaml`, `docs/active-features/`, or `docs/_kanban/` — those are gitignored control-plane (ADR-001) and aren't in worker worktrees by design.
1. Run `bash ~/agent-harness/scripts/run-breakdown.sh <spec-path>`. This is **synchronous** (`claude --print`) — it blocks until the breakdown skill has created the feature workspace (`tasks/<feature>/{backlog,in-progress,review,done}/` + `board.md`) and finished writing the task YAMLs to `tasks/<feature>/backlog/`. So the task files exist the moment it returns, and step 3 of *this same cycle* will spawn workers for them.
2. Update that spec's frontmatter from `status: approved` to `status: in-breakdown` (use `Edit`). This is the "I've started on this" marker so the gate won't re-select it.
3. **Board:** ensure the spec's card is now in **Acknowledged** (specs board). Task cards are seeded in **Backlog** on the feature's own `tasks/<feature>/board.md` by task-breakdown; just confirm each newly created `tasks/<feature>/backlog/*.yaml` task has a Backlog card there (ensure it, idempotently, if breakdown didn't).
4. The new task files exist with `status: backlog`; you don't touch them yourself — step 3 picks them up.

**Why the gate, not a scan:** serializing on "no task in backlog/in-progress" is exactly the kind of check an LLM eyeballing directories gets subtly wrong, so it's computed deterministically in `state_manager`. And the approved → in-breakdown flip is a one-way latch: breakdown is synchronous within a cycle, but the spec would otherwise still read `approved` next cycle and re-trigger, duplicating tasks. The flip is what makes breakdown run exactly once per spec.

**Why the advisor runs here, not inside breakdown:** keeping it a separate gate step means no nested `claude --print` sessions, the orchestrator stays the sole sequencer, and breakdown stays pure task-shaping. The advisor is still a task-creation-stage agent — it's what writes `status: blocked` — so "the task-creator owns blocking specs" holds.

### 3. Runnable backlog tasks → spawn workers

Compute free slots: `max_workers - len(active_workers)`. If zero, skip this step.

Otherwise:

```bash
conda run -n harness python -m harness.state_manager runnable
```

This prints task IDs (newline-delimited) whose `status: backlog` AND every `depends_on:` entry is `done`. Here `done` means **merged into the feature branch** — which *you* did automatically when that parent passed QA (step 4C), NOT a human merge. So a task is runnable once its parents' code is in `feature/<spec-id>`, which is exactly the branch you'll cut it from — no stacking, no waiting on the human. The list is **already ordered most-critical-first** (by `priority`: critical → high → medium → low, tie-broken by numeric id), so just take the first `free_slots` of them and iterate — you're guaranteed to be picking up the highest-priority runnable work.

> **Order matters: fully set up the task before the worker exists.** Spawning is split into two
> phases — `provision-worker` (create the worktree + reserve an idle **pane** in the shared agents
> window, but *don't* start claude) and `launch-worker` (start claude in that pane). Do **all** your
> bookkeeping — move the file, set `status: in-progress`, write the spawn metadata, record the worker
> in state — *between* the two. A worker launched into a fully-prepared world can never read a
> half-written task file nor race your writes to it. **Field ownership is disjoint:** the worker owns
> `status` / `pr_url` / `pr_number`; you own `tasks/` placement, `worktree` / `pane` / `started` /
> `assigned_to`, and `orchestrator-state.yaml`. After `launch-worker`, **never write the task file
> again** in this cycle — the worker is the only writer from then on.

> **All agents share one tmux window.** Every spawned agent (worker, QA, fixer) is a **pane** in a
> single `agents` window, so you can watch them all together. An agent's durable identity is its tmux
> **pane id** (e.g. `%7`) — a string, globally unique and never reused — not a window index. The
> `provision-worker` JSON returns `pane`; you persist that and pass it to `launch-worker` and
> `kill-pane`. The agents window is created on the first spawn and self-heals away when the last
> pane is killed.

For each runnable task ID, in this exact order:

1. **Build a tiny prompt.** Per the harness's references philosophy, the worker fetches what it needs — you just point at the task file. The task file is **control-plane state living in the main worktree** (gitignored, not in the worker's worktree checkout — see ADR-001), so pass its **absolute path**, not a relative one:

   ```
   You are a worker on the agent-harness. Follow the /worker skill.
   TASK_FILE: <absolute path to tasks/<feature>/in-progress/<task-id>.yaml in this repo>
   Your git worktree is your current directory. Read TASK_FILE (it points at the
   spec, CLAUDE.md, and any reference files you need — those live in your
   worktree, read them by their relative paths). Make your changes and commit on
   your branch, then edit TASK_FILE to set status: pr-opened. If you hit a gap
   you can't resolve from what's documented, set status: blocked in TASK_FILE
   with a reason and exit.
   ```

   The path is `$(pwd)/tasks/<feature>/in-progress/<task-id>.yaml` (you run in the project root; `<feature>` is the task's `feature:` field) — i.e. where the file will be *after* step 3, which is fine because the worker isn't launched until step 6. Write the prompt to a temp file (BSD `mktemp` wants trailing X's and no suffix): `mktemp /tmp/spawn-<task-id>.XXXXXX`.

2. **Provision (worker NOT started yet).** Cut the worker's branch from the feature branch by passing `--base-branch feature/<spec-id>` (the task's `feature:` field gives `<spec-id>`). Every task — dependent or not — bases on the feature branch, which already carries every `done` parent's code, so the worker has what it depends on:

   ```bash
   conda run -n harness python -m harness.tmux_manager provision-worker \
     --task-id <task-id> \
     --base-branch feature/<spec-id> \
     --prompt-file <temp-file>
   ```

   Returns JSON like `{"task_id":"…","session_id":"…","session":"harness","pane":"%7","window_target":"harness:1","worktree":"…","branch":"task/…","prompt_file":"…","launched":false}`. Keep `session_id`, `pane`, `worktree`, `prompt_file` for the next steps.

3. **Move the task file** from `tasks/<feature>/backlog/<task-id>.yaml` to `tasks/<feature>/in-progress/<task-id>.yaml` (use `mv`; `<feature>` is the task's `feature:` field).

4. **Update the task file's frontmatter** (`Edit`) — only the fields you own:
   - `status: in-progress`
   - `worktree: <returned worktree path>`
   - `pane: <returned pane id, e.g. "%7">`
   - `started: <current ISO-8601 timestamp>`
   - `assigned_to: <session_id from provision return>`
   - `pr_base: feature/<spec-id>` — the branch the worker's PR must target (it reads this for `gh pr create --base`). Always the feature branch.

   (Leave `pr_url`/`pr_number` as-is — they're the worker's.)

5. **Update orchestrator-state.yaml:**

   ```bash
   conda run -n harness python -m harness.state_manager add-worker \
     --task-id <task-id> \
     --worktree <worktree> \
     --pane <pane> \
     --started <iso-timestamp> \
     --model sonnet
   ```

6. **Launch the worker** — now, and only now, does the worker start:

   ```bash
   conda run -n harness python -m harness.tmux_manager launch-worker \
     --worktree <worktree> \
     --session-id <session_id> \
     --prompt-file <prompt_file> \
     --pane <pane>
   ```

7. **Board:** ensure the task's card is in **In Progress**. (This edits the board file, not the task file, so it's fine to do after launch.)

The wrapper handles atomic writes; you don't need to. Do not edit the task file after this — a future cycle's step 4 will observe whatever `status` the worker writes (`pr-opened` / `blocked`).

### 4. Agent lifecycle — teardowns, QA verdicts, and the retry loop

This step drives the whole QA loop. It iterates `active_workers` (the durable, crash-surviving list — each entry now carries a `role`: `worker`, `qa`, or `fixer`) and branches on role + the task's current `status`, then spawns the next agent from file state. **Crash-safe ordering everywhere: move the board card before you kill anything; `remove-worker` is the LAST step (the commit point).** Every action is idempotent, so a crash mid-step just replays next cycle.

QA-loop tasks live in `tasks/<feature>/review/` for the whole loop (worker → QA → fixer → re-QA); only `done` leaves (step 5).

**One worktree per task, not per agent.** The worker creates `../wt-<id>` on the `task/<id>` branch; QA, the Opus fixer, and re-QA all **reuse that same worktree** (a fresh pane each, but the same checkout). It is **not** torn down at each role handoff — only the pane is. The worktree persists across the whole loop and is removed exactly once, when the task **leaves its active states** (`in-progress` / `pr-opened` / `pr-updated` / `qa-failed`) for a state the human now owns (`qa-passed` / `blocked-escalated` / `blocked` / `done`). Concretely: 4A and 4B kill the pane but **keep** the worktree; 4C removes it **only when the task goes terminal** — `looks-good` (after the task is merged into the feature branch → `done`) or `escalate`/merge-conflict (→ `blocked-escalated`) — not on `needs-changes`; step 5 removes it at `done` (idempotent backstop); step 7 reconciles any drift. The worker's branch + PR of course survive regardless.

> **Where worktree removal happens — the single rule.** A worktree is removed iff the task's status is **not** active. Everywhere below that says "remove-worktree", that's why; everywhere that keeps it, the task is still active and a later agent will reuse the checkout. Don't remove a worktree just because an agent's pane died — pane death ends an *agent*, status is what ends the *worktree*.

**4A — `role: worker`, task `status: pr-opened`** (worker finished, PR open):
1. **Board:** ensure card → **In Review**.
2. `tmux_manager kill-pane --pane <pane>`
3. `mv tasks/<feature>/in-progress/<task-id>.yaml tasks/<feature>/review/<task-id>.yaml` (skip if already in `review/`)
4. `state_manager remove-worker --task-id <task-id>` ← **LAST**

**Do NOT remove the worktree here.** `pr-opened` is still active — the QA agent (4D) reuses the worker's `../wt-<id>` checkout. Only the pane goes.

**4B — `role: fixer`, task `status: pr-updated`** (Opus fixer pushed its fix):
1. **Board:** ensure card → **In Review**.
2. kill-pane → (file already in `review/`) → `remove-worker` LAST.

Same as 4A: `pr-updated` is active, so **keep the worktree** for re-QA — kill only the pane.

**4C — `role: qa`** (a QA agent). Read the task's `qa_report` file and find its `**status:**` line:

- **Already consumed?** If the task's `status` is already `done` or `blocked-escalated`, a prior cycle routed this verdict (and maybe crashed before teardown). Do **not** re-route or re-merge — just finish the teardown (kill-pane → remove-worktree → remove-worker) and move on.
- **Terminal verdict** (`looks-good` / `needs-changes` / `escalate`) — **consume it exactly once**, route, *then* tear down:
  - **Route first** (you own `status` / `qa_failure_count` / `failure_reason`; `Edit` the task file in `review/`):
    - **`looks-good` → integrate into the feature branch.**
      1. Set `status: qa-passed` (validated; integrating now).
      2. Merge the task's branch into the feature branch (this is the *only* merge you ever do — and it's hard-guarded against touching the release branch):
         ```bash
         conda run -n harness python -m harness.tmux_manager merge-branch \
           --into feature/<spec-id> --from <task-branch> [--pr-number <n>]
         ```
         `<spec-id>` = the task's `feature:` field; `<task-branch>` = its `branch:` field (else `task/<id>`). Pass `--pr-number` **only** when `pr_number` is a real integer (a real GitHub PR) — omit it for the local-marker case (`pr_url: local://…`), where it does a local `git merge`. Read the returned JSON:
         - `merged: true` → set `status: done`; board card → **Done**. (Its dependents are now unblocked — they'll spawn in a later cycle's step 3.)
         - `conflict: true` **or** `refused: true` → set `status: blocked-escalated` and `failure_reason:` to the merge `reason`; board card → **Human Review**. A clean auto-integration wasn't possible, so hand it to the human. (`refused` should never happen here — it only fires if `--into` were the release branch — but route it the same way.)
    - `needs-changes` → `status: qa-failed`; if `qa_failure_count` is 0 set it to 1 (idempotent — don't re-bump if already 1); board card → **QA Failed**.
    - `escalate` → `status: blocked-escalated`; set `failure_reason` to the report's escalate reason; board card → **Human Review**.
  - **Then tear down the QA agent.** Always kill the pane and `remove-worker` (LAST). **Remove the worktree only if the task is now terminal** — `done` (looks-good merged in), or `blocked-escalated` (escalate, or a merge conflict). On `needs-changes` the task is `qa-failed` (still active) — **keep** the worktree; the Opus fixer (4D) reuses it.
    - `done` / `blocked-escalated`: kill-pane → `remove-worktree --path <worktree>` → `remove-worker` LAST.
    - `needs-changes`: kill-pane → `remove-worker` LAST. (Worktree stays.)
    - (Task file stays in `review/` either way; step 5 archives `done` ones.)
- **Still `WIP`, pane alive** (its pane id is in `list-panes`): QA is still running — do nothing.
- **Still `WIP`, pane GONE** (a dead run — no verdict): infra failure. `Edit` the task to increment `qa_run_attempts`, then `remove-worker`. Then:
  - `qa_run_attempts` < **2** → leave `status` as-is (`pr-opened`/`pr-updated`, still active) and **keep the worktree** — 4D respawns QA into the same checkout.
  - `qa_run_attempts` ≥ **2** → `status: blocked-escalated`, `failure_reason: "QA could not complete after 2 attempts"`, board card → **Human Review**, **and now `remove-worktree --path <worktree>`** (the task just went terminal).

**4S — `HARNESS_QA_VERIFY` test scaffold (inject a real bug before the first QA).** This is a
**deliberate, opt-in test hook** — it is the *one* place the orchestrator touches project source,
and only when the operator has explicitly asked it to verify that QA works. It is **off** unless
`printenv HARNESS_QA_VERIFY` is non-empty. Skip this entire sub-step when:

- `HARNESS_QA_VERIFY` is empty/unset, **or**
- `HARNESS_QA_FORCE` is set (forced QA never runs for real, so there's nothing to verify — `FORCE`
  wins; do not inject), **or**
- the task isn't a *fresh* first QA: only act on a task with `status: pr-opened` whose
  `qa_failure_count` is `0` and whose `qa_verify_injected` is not already `true`. (Never inject on
  `pr-updated`/re-QA — that would sabotage the fixer's own fix and the loop would never converge.)

When it **does** apply, for that task, **before** you spawn its QA agent in 4D:

1. **Use the task's persistent worktree directly** — no ephemeral one. The worker's `../wt-<id>` is
   still live (4A keeps it now), already on `task/<id>`. Take its path from the task file's
   `worktree:` field (default `../wt-<id>`). You inject straight into it; there's no pane to open or
   kill, and **nothing to tear down** afterwards — the same worktree goes on to host QA in 4D.
2. **Introduce exactly one small, behavior-visible defect** in that worktree that violates **one**
   item of the task's `expected_behavior` rubric — the smallest change that QA's behavioral run will
   observe. Prefer a one-line change to already-written code (e.g. a handler that drops a response
   field, an off-by-one, a swapped status code, a UI control that no longer calls the API). Do **not**
   break the build/lint/tests in a way that stops the app from running — QA must be able to *run* the
   feature and *see* the wrong behavior. Keep it obviously a planted defect (a `// HARNESS_QA_VERIFY:
   injected test defect` comment on the changed line is good hygiene).
3. **Commit it on the branch** so it's part of what QA runs (QA reuses this very worktree in 4D, so
   committing also keeps the working tree clean for it):
   ```bash
   git -C <worktree> commit -am "test(HARNESS_QA_VERIFY): inject defect for QA to catch"
   ```
   (If the project has a remote and the worker pushed, also `git -C <worktree> push` so the PR branch
   carries it. For the local-marker toy, the commit on the shared branch is enough.)
4. **Record the injection on the task file** (no worktree teardown — it lives on for QA):
   - `Edit` the task file: set `qa_verify_injected: true` (this is the idempotency latch — a crash/replay
     won't double-inject; it's also why you check it in the skip conditions above).
5. **Then fall through to 4D** and spawn QA **normally** — QA is *not* told a bug was planted; it must
   catch the broken behavior on its own. Real QA → `needs-changes` → Opus fixer repairs it →
   `pr-updated` → re-QA → `looks-good` → `qa-passed`. The whole ladder runs on a **genuine** verdict.

This is the inverse of `HARNESS_QA_FORCE`: `FORCE` *bypasses* QA's judgment to test routing;
`VERIFY` *exercises* QA's judgment to test that it actually catches defects. (Test playbook §6.)

**4D — spawn QA / fixer / re-QA from state.** Compute free slots (`max_workers − len(active_workers)`); if zero, skip. Scan every feature's review dir (`tasks/*/review/*.yaml`) (skip any task that already has an `active_workers` entry):

- `status: pr-opened` or `pr-updated` → **spawn the QA agent** (`role: qa`).
- `status: qa-failed` → **spawn the Opus fixer** (`role: fixer`).

**Before launching a QA agent (first QA *or* re-QA), reset the verdict signal to `WIP`.** If the task's `qa_report` file exists from a prior round, overwrite its `**status:**` line back to `WIP` (or delete the file) as part of spawning. This closes a race: the report still holds the *previous* round's terminal verdict until the new agent overwrites it, so without this reset a mid-cycle 4C read could **consume a stale verdict and tear down the live QA agent**. The QA agent also writes `WIP` first (§0 of its skill), but the orchestrator doing it at spawn time is what eliminates the window. (Surfaced by the issue-#4 e2e test.)

Spawn mechanics mirror step 3 (provision → bookkeep → launch), with two differences: `--existing-branch` (QA/fixer reuse the worker's `task/<id>` branch, they don't cut a new one) and a per-role `--label`. **`provision-worker` resolves the worktree from the task id (`../wt-<id>`), not the label, so it reuses the worker's still-live checkout** — `create_worktree` sees the path is already registered and no-ops; only a fresh pane is opened in it. (`--label` titles that pane and names the prompt file; it no longer forks the worktree path.) So `add-worker`'s `--worktree` is the same `../wt-<id>` the worker used. Write the prompt to a temp file (`mktemp /tmp/spawn-<id>.XXXXXX`), then:

*QA agent:*
```bash
conda run -n harness python -m harness.tmux_manager provision-worker \
  --task-id <id> --existing-branch --label qa --prompt-file <tmpfile>
conda run -n harness python -m harness.state_manager add-worker \
  --task-id <id> --worktree <wt> --pane <pane> --started <iso> --model sonnet --role qa
conda run -n harness python -m harness.tmux_manager launch-worker \
  --worktree <wt> --session-id <sid> --prompt-file <tmpfile> --pane <pane>
```
QA prompt (the task file is in `review/` — pass its **absolute** path):
```
You are the QA agent on the agent-harness. Follow the /qa-agent skill.
TASK_FILE: <abs path to tasks/<feature>/review/<id>.yaml>
Your worktree (cwd) is on the PR branch task/<id>. Read TASK_FILE for the
expected_behavior rubric and the qa_instructions recipe path, RUN the feature,
and judge observed behaviour against the rubric. Write your verdict
(looks-good / needs-changes / escalate) into the qa-report status. Do NOT edit
the task status or kill your pane.
```

*Opus fixer:* same three calls, but `--label fixer`, `--role fixer`, and **`--model opus` on `launch-worker`**. Prompt:
```
You are a worker on the agent-harness in FIX MODE. Follow the /worker skill.
TASK_FILE: <abs path to tasks/<feature>/review/<id>.yaml>
This task is qa-failed (QA returned needs-changes). Your worktree (cwd) is
already on the existing branch task/<id>. Read TASK_FILE's qa_report for what to
fix, fix only that, push to the SAME branch, set status: pr-updated. No new PR.
```

`add-worker` runs **before** `launch-worker` so a crash mid-launch still leaves the agent tracked (it'll be reconciled in step 7). Unlike step 3 you do **not** move the task file or change its `status` when spawning QA/fixer — the task is already fully written; QA writes only its `qa_report`, the fixer writes only `status: pr-updated`.

**Why this is crash-safe:** an agent stays in `active_workers` until its `remove-worker`, so any crash in 4A–4C replays next cycle (idempotent). A verdict is consumed exactly once because routing precedes the QA teardown and `remove-worker` is last — a QA agent whose verdict was routed but never torn down simply re-routes (idempotently) next cycle. Because 4D keys off `status` + "no active entry", the worker→QA→fixer→re-QA handoffs can all happen across (or within) cycles without double-spawning.

### 5. Done → archive

`done` means the task's PR has been **merged into the feature branch** (you did that in 4C on the `looks-good` verdict) — it is *not* a human merge to the release branch. Read every `tasks/*/review/*.yaml` (across all feature workspaces). For any with `status: done`:

1. **Board:** ensure the task's card is in **Done**.
2. `tmux_manager remove-worktree --path <worktree>` — reclaim the per-task worktree. 4C already removed it when it set `done`, so this is an **idempotent backstop**. Take `<worktree>` from the task file's `worktree:` field.
3. `mv tasks/<feature>/review/<task-id>.yaml tasks/<feature>/done/<task-id>.yaml`

(The feature branch itself — `feature/<spec-id>` — accumulates every `done` task. Merging it into the release branch is **the human's** step, once the whole spec is green; you never do that merge.)

Doc closeout is a Stage 7 concern — skip it for now.

### 6. Blocked → move card, then leave alone

Read every `tasks/*/in-progress/*.yaml` (across all feature workspaces). For any with `status: blocked`: **board:** ensure the task's card is in **Blocked** (on its feature board), then do **nothing else** *here*. The blocker-resolver skill (Stage 7) handles routing these to Opus or back to a human; don't un-block it — if you tried to "help", you'd race the resolver. (The card move is the one exception: it's idempotent and only touches the board, not the task.)

`blocked` is a **non-active** state, so the per-task worktree is reclaimed — but **not in this step**. The worker that hit the blocker has already exited (its pane is dead), so step 7's reconcile removes its `active_workers` entry **and** its worktree under the single rule "remove the worktree iff the task is non-active." That's deliberate: blocked leaves the active set (you chose this — a blocked task does not keep its local checkout). Leaving the actual teardown to reconcile keeps step 6 a pure board move and avoids racing a future resolver mid-step.

### 7. Reconcile state vs. reality

Drift happens. A tmux pane dies, a worktree gets manually deleted, a task file is moved by hand. Do a sanity check:

Note step 4 already ran this cycle, so a dead QA/fixer pane was handled there (4B/4C) with its proper semantics. Step 7 is the backstop for whatever's left. **The worktree rule is unchanged here: remove it iff the task is non-active.** Pane death ends an *agent* (drop it from `active_workers`); it does **not** by itself end the *worktree* — a dead agent on a still-active task leaves the worktree in place for the next agent (or a re-spawn) to reuse. For each entry in `active_workers`, locate its task file by **role** — `role: worker` → `tasks/*/in-progress/<id>.yaml`; `role: qa`/`fixer` → `tasks/*/review/<id>.yaml` (the `<id>` is globally unique, so glob across feature workspaces; check both state dirs if unsure):
- Does the task file exist anywhere under `tasks/`? If not (e.g. someone moved/deleted it by hand), it's a true orphan: `remove-worker` + `kill-pane` + `remove-worktree` to clean up.
- Does the tmux pane still exist (`tmux_manager list-panes`)? If the entry's `pane` id isn't listed, the agent died — `remove-worker`. Then read the task's `status:` and apply the single rule:
  - **non-active** (`qa-passed` / `blocked-escalated` / `blocked` / `done`) → also `remove-worktree --path <worktree>`. (This is how a `blocked` worker's worktree gets reclaimed, per step 6; a `role: qa` terminal entry should already be gone via 4C, and re-removing is a safe no-op.)
  - **still active** (`in-progress` / `pr-opened` / `pr-updated` / `qa-failed`) → **keep the worktree.** A crashed worker at `in-progress` leaves its checkout for a future re-spawn to reuse; a `pr-opened`/`qa-failed` task keeps it for the QA agent / fixer.
- **Board invariant:** is the task's card in the column matching its current `status:` (per the table — incl. `qa-failed`→QA Failed, `qa-passed`/`blocked-escalated`→Human Review)? If not, ensure it (idempotent). Self-heals board drift for in-flight work.

Don't go further than that. Do not try to revive a dead worker; if the task file is in a `tasks/<feature>/in-progress/` dir but the pane is gone, just remove from state (worktree stays — the task is still active) and let a future cycle re-spawn into the same worktree (after a human inspects).

### 8. Summary

Print one line per action you took. Example:

```
[orchestrator] cycle done in 2.1s
  approved → breakdown: 0
  spawned workers: 1 (TASK-051 on pane %7)
  pr-opened teardowns: 0
  done archives: 1 (TASK-049)
  board card moves: 2
  reconciliations: 0
```

If nothing happened, say so explicitly:

```
[orchestrator] cycle done in 0.4s — no actions
```

Then exit. **Do not loop.** Do not "wait and check again." If the user wants periodic cycles, they wired `/loop` or cron around you.

## Wrapper cheat-sheet

You call these via Bash. All exit non-zero on hard errors and 0 on success (including idempotent no-ops).

| Command | Purpose |
|---|---|
| `conda run -n harness python -m harness.state_manager init` | Create empty `orchestrator-state.yaml` (no-op if exists) |
| `conda run -n harness python -m harness.state_manager read` | Print state as JSON |
| `conda run -n harness python -m harness.state_manager runnable` | List task IDs whose deps are satisfied (newline-delimited) |
| `conda run -n harness python -m harness.state_manager add-worker --task-id … --worktree … --pane %N --started TS --model M [--role worker\|qa\|fixer]` | Add to active_workers (idempotent on task-id); `--role` defaults to `worker` |
| `conda run -n harness python -m harness.state_manager remove-worker --task-id …` | Remove from active_workers |
| `conda run -n harness python -m harness.tmux_manager provision-worker --task-id … --prompt-file … [--existing-branch] [--label worker\|qa\|fixer]` | Phase 1: worktree + idle reserved **pane** in the agents window, **no** claude yet; returns JSON with `pane` (`launched:false`). `--existing-branch` checks out the worker's `task/<id>` branch (QA/fixer); `--label` titles the pane/names the prompt/worktree |
| `conda run -n harness python -m harness.tmux_manager launch-worker --worktree … --session-id … --prompt-file … --pane %N [--model opus]` | Phase 2: start claude in the provisioned pane. `--model opus` for the QA-fail fixer |
| `conda run -n harness python -m harness.tmux_manager spawn-worker --task-id … --prompt-file …` | Provision + launch in one call (manual/testing; worker starts immediately) |
| `conda run -n harness python -m harness.tmux_manager kill-pane --pane %N` | Kill the agent's pane (idempotent) |
| `conda run -n harness python -m harness.tmux_manager remove-worktree --path …` | git worktree remove (idempotent, force-fallback) |
| `conda run -n harness python -m harness.tmux_manager ensure-branch --name feature/<spec-id> [--base …] --checkout` | Create the per-spec feature branch (idempotent; base defaults to the release branch) and check it out. Used at breakdown (step 2b) |
| `conda run -n harness python -m harness.tmux_manager merge-branch --into feature/<spec-id> --from task/<id> [--pr-number N]` | Integrate a task into the feature branch on QA pass (step 4C). Prints JSON `{merged,conflict,refused,reason}`. **Refuses to merge into the release branch.** With `--pr-number` + a remote, uses `gh pr merge`; else a local `git merge` |
| `conda run -n harness python -m harness.tmux_manager nudge --pane %N --text-file …` | Send mid-session message to a running agent |
| `conda run -n harness python -m harness.tmux_manager list-panes` | List live agent pane ids in the harness session |
| `bash ~/agent-harness/scripts/run-advisor.sh <spec>` | Invoke /advisor (Opus) on a spec — architectural gate before breakdown; prints `ADVISOR_VERDICT:` |
| `bash ~/agent-harness/scripts/run-breakdown.sh <spec>` | Invoke /task-breakdown on a spec |

## Rules of the road

- **You do not write code.** Workers write code; the breakdown skill writes task files; you only route work and update state. If you find yourself opening a project source file, you've gone past your job. **The one deliberate exception is step 4S** (`HARNESS_QA_VERIFY`), an opt-in test scaffold where you intentionally inject a single defect to verify QA — and even then only the minimal change, only when that env var is set.
- **You operate on metadata, not content.** Read task-file frontmatter to determine `status`, `depends_on`, `worktree`, `pane`. Do not read entire specs, ADRs, or `progress.md` files. The references philosophy of the harness is that each agent fetches the content it needs — yours is structurally minimal.
- **The state file is mediated.** Use `state_manager add-worker` / `remove-worker` for `active_workers` mutations. You may use `Edit` on the state file for fields the CLI doesn't expose (`cycle_count`, `last_action`, `last_action_time`). Never `Write` over the whole state file — that defeats the atomic-write guarantee.
- **The task file frontmatter is editable.** Use `Edit` to flip `status:` and to set `worktree`/`pane`/`started`/`pr_url`. Don't restructure the task file; the schema is in `~/agent-harness/schemas/task.schema.yaml`.
- **Don't touch a worker's `progress.md` or `decisions.md`.** Those belong to the worker. You watch them from the outside; you don't write to them.
- **If you don't know, stop.** If you can't tell what status a task is in, or a worktree path looks weird, or `state_manager read` returns something malformed — stop and report the issue to the user rather than guessing. The harness is designed so wrong actions cost real engineering time; correctness over throughput.

## Common situations

**First-ever cycle in a project.** No state file, empty task dirs. Initialize state (`state_manager init`) and exit with a "nothing to do" summary.

**Spec just approved, no tasks yet.** Step 2 runs the breakdown *synchronously*, so the task files land in `tasks/<feature>/backlog/` before step 3 runs. The **same** cycle then spawns workers for them (subject to free slots). Breakdown and the first spawns happen together, not across two cycles.

**All workers busy, more tasks runnable.** Skip step 3 (no slots free). Other steps run normally; when a PR opens and frees a slot, the next cycle spawns from the queue.

**Worker hit a blocker.** Status flips to `blocked` (worker writes this). You leave it alone (step 6). Stage 7's blocker-resolver picks it up.

**Pane gone but task still in tasks/<feature>/in-progress/.** Reconcile (step 7): drop from `active_workers`. The task file stays in `in-progress` — a human or a later cycle decides whether to re-spawn or escalate.

## Deferred to later stages

These are real responsibilities of the orchestrator but live in stages not yet built. Do not try to implement them here:

- **Review agent** (build order D) — a *code* reviewer that posts PR comments. Separate from QA (which is behavioral, step 4) and NOT part of the retry ladder.
- **Merge detection → `done`** — a separate agent detects your manual merge of a `qa-passed` PR (`gh pr view --json state,mergedAt`) and sets `status: done`; step 5 then archives it. The orchestrator does **not** detect merges itself — `done` is set for you.
- **Doc closeout** (Stage 7) — currently `done` tasks just move to `tasks/<feature>/done/` after step 5.
- **Blocker resolution** (Stage 7) — currently `blocked` tasks sit forever in step 6 (note: `blocked` from a worker BLOCKER is distinct from `blocked-escalated` from QA escalation, which awaits *you* in Human Review).
- **Telegram + Linear notifications** (Stage 5) — currently no notifications at all; you operate silently.

The QA retry loop (step 4) **is** built: `pr-opened`/`pr-updated` → async QA; `needs-changes` → Opus fixer → re-QA (one retry); `looks-good` → `qa-passed`; `escalate` or a dead QA run (×2) → `blocked-escalated`.
