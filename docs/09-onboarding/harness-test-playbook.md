# Harness End-to-End Test Playbook

A repeatable procedure for proving the harness control loop works on a throwaway project:
**approved spec → breakdown → workers → `pr-opened` → QA → (Opus fixer → re-QA) → `qa-passed` → archive**.

Use this to smoke-test the harness after changing the wrappers, the orchestrator skill, or
the worker/breakdown skills. The throwaway project is a **small but real Express + React todo
app** — small enough to build in a few worker passes, real enough that **behavioral QA has
something to actually drive** (`bruno` against the Express API, `playwright-cli` against the
React UI). The goal is still the *plumbing and control flow* first; the todo app is the vehicle.
First validated 2026-05-28 (then on a trivial greeting stub); migrated to the todo app to
exercise real behavioral QA and the `HARNESS_QA_VERIFY` hook (§6).

Pairs with `build-progress.md` (what's built) and `build-roadmap.md` (what's left). Known gaps
this test exposes are tracked in the roadmap and in the memory `harness-worker-runtime-gaps`.

---

## Prerequisites

- `tmux`, `git`, and the `claude` CLI on PATH.
- The harness repo at `~/agent-harness`.
- The `harness` conda env, created by `setup.sh` (`conda create -n harness …` + editable install).
  Wrappers are invoked as `conda run -n harness python -m harness.*` — no `PYTHONPATH` needed. If
  `conda run -n harness …` errors that the env is missing, run `setup.sh` (or set
  `HARNESS_CONDA_ENV` to your env name). See ADR-002.
- Global setting `skipDangerousModePermissionPrompt: true` in `~/.claude/settings.json` — the
  wrappers launch workers/breakdown with `--dangerously-skip-permissions`, and this lets that
  run without a confirmation prompt.
- Spawning workers launches autonomous `claude` sessions. Under Claude Code auto mode the
  safety classifier blocks each spawn; approve it, toggle auto mode off, or allowlist
  `python -m harness.tmux_manager spawn-worker` in settings.

## The skills under test

The base harness agent skills are version-controlled in the harness repo at
`~/agent-harness/skills/<name>/SKILL.md` and installed by `setup.sh` into each project's
`.claude/skills/` (project-scoped, and **committed** so worker/QA git worktrees can load them).
The real `/task-breakdown` and `/worker` skills replaced the original test stubs (2026-05-28):

- **`/task-breakdown <spec>`** — reads the spec's acceptance criteria, its referenced ADRs, and
  `CLAUDE.md`, then creates the feature workspace `tasks/<feature>/{backlog,in-progress,review,done}/`
  + `board.md` (here `<feature>` = the spec id `todo-app`) and shapes a *dependency-aware* set of
  schema-valid task YAMLs into `tasks/<feature>/backlog/`
  (correct `depends_on`/`blocks`/`can_parallelize_with`). Task count is driven by the ACs, not fixed.
- **`/worker`** — runs in its worktree, reads its task by **absolute** path (control-plane, ADR-001)
  and the spec/`CLAUDE.md`/code by relative path, implements the task for real, runs the project's
  Definition of Done, commits on its branch, opens a PR (real `gh pr create` if a remote +
  authenticated `gh` exist, else a `local://pr/<id>` marker), and flips its task file to
  `status: pr-opened`.

> The earlier stub behavior (fixed two tasks; a worker that just dropped `src/<id>.txt`) is gone.
> On a **throwaway test project with no GitHub remote**, the worker uses the `local://` marker —
> the status flip, not a real PR, is what drives the loop. Give the test project a trivial
> `CLAUDE.md` brief and at least one tiny, well-specified spec so the real skills have something
> concrete to shape and implement.

---

## Procedure

Pick a throwaway project dir (`PROJ`) outside the harness repo. Worktrees are created as
siblings (`../wt-<task-id>`), so keep `PROJ` somewhere with room (e.g. `~/harness-dummy`).

### 0. Set the session env

```bash
PROJ=~/harness-dummy
```
(No `PYTHONPATH` — the wrappers resolve through the `harness` conda env via `conda run`.)

> **Real behavioral QA is now the default.** The QA agent drives features through real tooling —
> `playwright-cli` for web/frontend, `bruno` (`bru`) for backend APIs — so no auto-pass env var is
> needed (the interim `HARNESS_QA_AUTOPASS` gate has been removed). Two **test hooks** (§6) let you
> exercise the QA loop on demand; both propagate through the **tmux server environment** so spawned
> panes inherit them (same mechanism as `HARNESS_CLAUDE_DANGEROUS`) — export them **before** the
> harness tmux session is created, or `tmux setenv -t harness <VAR> <value>` on the live session
> (new panes inherit it):
>
> - **`HARNESS_QA_FORCE`** — read by the **QA agent**; forces a *verdict* deterministically so the
>   retry/escalate **routing** runs without QA's judgment being load-bearing. Tests the plumbing.
> - **`HARNESS_QA_VERIFY`** — read by the **orchestrator**; injects a real, minor behavioral bug into
>   the worker's branch after `pr-opened`, so **real** QA has to genuinely *catch* it and drive the
>   fix loop. Tests that QA + the Opus fixer actually work. **Mutually exclusive with
>   `HARNESS_QA_FORCE`** — if both are set, `HARNESS_QA_FORCE` wins (QA never runs for real, so there
>   is nothing to verify) and `HARNESS_QA_VERIFY` is a no-op.

### 1. Bootstrap the dummy project

Worktrees are cut from the repo's current branch (auto-detected by
`tmux_manager.resolve_base_branch`, override with `HARNESS_BASE_BRANCH`), so `main` or `master`
both work — no special init needed. `main` is used here just for concreteness.

> ⚠️ **Run the bootstrap as one `&&`-chained block, and never let `cd` fail silently.** This
> sequence writes `README.md`, commits, and runs `setup.sh` — all of which mutate **whatever the
> current directory is**. If `cd "$PROJ"` doesn't run, every following command lands in your
> *previous* cwd, typically the `~/agent-harness` repo itself: `setup.sh` scaffolds `tasks/`,
> `CLAUDE.md`, `docs/specs/`, and rewrites `.gitignore` there, and the `echo > README.md` + commit
> clobbers and commits over the harness repo. This actually happened on 2026-05-28 and had to be
> manually reverted. Two specific traps:
> - **Bare globs under zsh.** `rm -rf "$PROJ" ~/wt-TASK-*` aborts the *entire line* with "no matches
>   found" when no `wt-TASK-*` exists (zsh `NOMATCH`), so the `cd` you appended after it never runs.
>   Use a glob-safe `find` instead.
> - **Unchained commands.** Newline-separated commands run regardless of whether the previous one
>   failed. Chain the dir-change with `&&` so a failed `mkdir`/`cd` aborts the rest, and print `pwd`
>   to confirm before `setup.sh`.

```bash
find ~ -maxdepth 1 -name 'wt-TASK-*' -exec rm -rf {} +    # glob-safe worktree cleanup
rm -rf "$PROJ" && mkdir "$PROJ" && cd "$PROJ" && pwd && \
  git init -b main -q && \
  git config user.email dummy@test.local && git config user.name "Dummy Tester" && \
  echo "# Dummy" > README.md && git add README.md && git commit -qm "initial commit" && \
  bash ~/agent-harness/scripts/setup.sh && \
  echo "BOOTSTRAP OK in $(pwd)"
```

If that block stops early or `pwd` is not `$PROJ`, **fix the directory before doing anything else** —
do not re-run `setup.sh` from the wrong place.

Fill `CLAUDE.md` with the todo-app operating manual so every worker, the breakdown skill, and QA
share one definition of the project, its layout, and its Definition of Done:

```bash
cat > CLAUDE.md <<'EOF'
# Todo App — operating manual (harness test project)

A minimal full-stack todo app: an **Express** JSON API + a **React** (Vite) single-page UI.
Small on purpose — it exists so the harness has a real, runnable feature for behavioral QA.

## Layout
- `server/` — Express API (Node, ESM). Entry `server/index.js`, port **3001**.
- `client/` — React + Vite SPA. Dev server proxies `/api` → `http://localhost:3001`.
- Todos are kept **in memory** in the server process (no DB) — a `[]` of
  `{ id: string, title: string, completed: boolean }`. Restart = empty list. That is fine.

## API contract (the server owns this)
- `GET    /api/todos`            → `200` `[Todo, …]`
- `POST   /api/todos`            body `{ title }` → `201` the created `Todo` (`completed:false`);
                                  empty/whitespace `title` → `400`.
- `PATCH  /api/todos/:id`        body `{ completed }` (and/or `{ title }`) → `200` updated `Todo`;
                                  unknown id → `404`.
- `DELETE /api/todos/:id`        → `204`; unknown id → `404`.

## UI contract (the client owns this)
- Lists all todos; an input + **Add** button creates one; each row has a checkbox that toggles
  `completed` (with a line-through style when done) and a **Delete** button that removes it.
- Every mutation calls the API and reflects the server's response.

## Definition of Done (every task must pass before opening a PR)
- `npm install` works at the repo root (npm workspaces: `server`, `client`).
- `npm run lint` is clean (ESLint).
- `npm test` passes (server: supertest over the API; add tests for what you build).
- `npm run build` succeeds for the client (Vite build).
- App runs: `npm run dev` starts the API on :3001 and the Vite dev server on :5173.

## How to run (QA uses this)
- API only:    `npm --workspace server run start`   (or `dev`)
- Client only: `npm --workspace client run dev`
- Both:        `npm run dev` (root) — concurrently starts server + client.

## Working agreement
- Implement only your task's slice; keep the contracts above stable so other tasks' PRs compose.
- Match existing style; no new heavyweight deps without a reason.
EOF
git add CLAUDE.md .gitignore docs/specs/_template.md .claude/skills && git commit -qm "scaffolding: todo-app brief + base agent skills"
```

> The DoD above is what the **worker** runs before `pr-opened` and what **QA** drives afterward
> (`bruno` against the API, `playwright-cli` against the UI). It is also the surface
> `HARNESS_QA_VERIFY` (§6) targets — the injected bug violates one of these contracts so real QA
> catches it.

**Verify:** `tasks/` and `docs/specs/` exist; `orchestrator-state.yaml` present; on branch `main`.
(The per-feature `tasks/<feature>/{backlog,in-progress,review,done}/` workspace doesn't exist yet —
task-breakdown creates it in step 3.)

### 2. Drop an approved spec — and COMMIT it

Create `docs/specs/todo-app.md` with frontmatter `status: approved` and ACs that span the
Express API and the React UI, so breakdown shapes a few composable, independently-PR-able tasks
(API foundation → API CRUD → UI). Copy `docs/specs/_template.md` and edit, or paste:

```bash
cat > docs/specs/todo-app.md <<'EOF'
---
status: approved
id: todo-app
author: harness-test
created: 2026-06-04
priority: high
linear_epic: null
adrs_referenced: []
---

# Spec: Todo app (Express + React)

## Goal
A minimal full-stack todo app — an Express JSON API plus a React (Vite) SPA — that lets a user
add, list, complete, and delete todos. Small but real, so behavioral QA can drive it end to end.

## Acceptance Criteria
- [ ] **Project scaffold + run.** `npm install` at the root sets up npm workspaces (`server`,
      `client`); `npm run dev` starts the Express API on :3001 and the Vite dev server on :5173;
      `npm run lint`, `npm test`, and `npm run build` all succeed.
- [ ] **List todos.** `GET /api/todos` returns `200` with a JSON array of
      `{ id, title, completed }`; empty list returns `[]`.
- [ ] **Create a todo.** `POST /api/todos` with `{ title }` returns `201` with the created todo
      (`completed: false`, a generated `id`), and it then appears in `GET /api/todos`. A
      missing/blank `title` returns `400` and creates nothing.
- [ ] **Toggle complete.** `PATCH /api/todos/:id` with `{ completed: true|false }` returns `200`
      with the updated todo; an unknown id returns `404`.
- [ ] **Delete a todo.** `DELETE /api/todos/:id` returns `204` and the todo no longer appears in
      `GET /api/todos`; an unknown id returns `404`.
- [ ] **React UI.** The SPA lists todos, has an input + Add button that creates one, a per-row
      checkbox that toggles completion (completed rows render struck-through), and a Delete button
      that removes the row. Every action calls the API and reflects the server response.

## Out of Scope
- Persistence/DB (todos live in memory), auth, multi-user, pagination, editing title in the UI.

## Edge Cases
- Blank/whitespace-only title is rejected (`400`); the UI does not create an empty todo.
- Operations on an unknown id return `404`, not a crash.

## Open Questions
- None — kept deliberately small for the harness e2e.
EOF
git add docs/specs/todo-app.md && git commit -m "spec: todo-app approved"
```

> **⚠️ Commit the spec before breakdown.** Workers read the spec by *relative path from their
> worktree*, which is a **committed git snapshot** — an uncommitted spec is invisible to them and
> every worker blocks with "referenced spec absent". (The advisor and breakdown see it anyway
> because they run in the main worktree — so the failure only shows up at the worker.) In the real
> loop the orchestrator commits the spec at step 2b.0; in this manual playbook, commit it yourself
> (the `git commit` is already chained into the block above). Surfaced by the issue-#4 e2e run.

### 3. Breakdown (orchestrator step 2)

```bash
cd "$PROJ"
conda run -n harness python -m harness.state_manager read                       # baseline state
bash ~/agent-harness/scripts/run-breakdown.sh docs/specs/todo-app.md
```

**Verify:** a handful of files in `tasks/todo-app/backlog/` (count is AC-driven — expect ~3–4: a scaffold
task, the API CRUD task(s), and the React UI task, wired with `depends_on` so the UI waits on the
API), all schema-valid:

```bash
python3 -c "import yaml,jsonschema,glob; s=yaml.safe_load(open('$HOME/agent-harness/schemas/task.schema.yaml')); [jsonschema.validate(yaml.safe_load(open(f)),s) or print(f,'VALID') for f in glob.glob('tasks/todo-app/backlog/*.yaml')]"
```

Then flip the spec frontmatter `approved → in-breakdown` (the one-way latch).

> Note: `run-breakdown.sh` is **synchronous** (`claude --print`), so the task files exist the
> moment it returns — breakdown and the first spawn happen in the *same* cycle, despite the
> orchestrator skill's "next cycle" wording.

### 4. Spawn workers (orchestrator step 3)

```bash
conda run -n harness python -m harness.state_manager runnable                   # only dep-free tasks
```

> With the todo spec's `depends_on` wiring, `runnable` will return **only the scaffold task** at
> first (the API/UI tasks list it as a dependency, so they stay held until it reaches `done`).
> Don't be surprised to see one runnable task here, not all of them — that gating is the point.
> The IDs below (`TASK-001`, …) are placeholders; use whatever `runnable`/breakdown actually emit.

For each runnable task — **move the file first** (avoids a read-before-move race), then spawn
with an absolute task-file path in the prompt. This manual flow uses the one-shot `spawn-worker`
(worker starts immediately), which is fine when *you* are the only one writing the task file.

> The real `/orchestrator` instead uses the two-phase **`provision-worker`** → bookkeep →
> **`launch-worker`** split so it can set `status: in-progress` + worktree/pane/state *before* the
> worker exists — eliminating the status-write race a fast worker would otherwise win. To rehearse
> that flow by hand: `provision-worker …` (returns `pane`/`session_id`/`worktree`/`prompt_file`,
> reserves an idle `sleep` pane in the shared agents window), do the `mv` + status/metadata edit +
> `add-worker`, then `launch-worker --worktree … --session-id … --prompt-file … --pane …`.

```bash
cd "$PROJ"
mv tasks/todo-app/backlog/TASK-001.yaml tasks/todo-app/in-progress/TASK-001.yaml
PROMPT=$(mktemp /tmp/spawn-TASK-001.XXXXXX)
cat > "$PROMPT" <<EOF
You are a worker on the agent-harness. Follow the /worker skill.
TASK_FILE: $PROJ/tasks/todo-app/in-progress/TASK-001.yaml
Your git worktree is your current directory (branch task/TASK-001). Read TASK_FILE
(absolute path) plus the spec/CLAUDE.md/code it points at (relative paths in your
worktree), implement the task for real, satisfy the project's Definition of Done,
commit on your branch, open a PR (real if a remote exists, else a local:// marker),
then edit TASK_FILE to set status: pr-opened. If you hit an unresolvable gap, set
status: blocked and stop.
EOF
conda run -n harness python -m harness.tmux_manager spawn-worker --task-id TASK-001 --prompt-file "$PROMPT"
```

Then record the worker (use the returned `worktree`/`pane`):

```bash
# edit tasks/todo-app/in-progress/TASK-001.yaml: status: in-progress, worktree, pane, started, assigned_to
conda run -n harness python -m harness.state_manager add-worker --task-id TASK-001 \
  --worktree ~/wt-TASK-001 --pane %7 --started 2026-01-01T00:00:00Z --model sonnet
```

Repeat for each task `runnable` returns. `max_workers` is 3, so up to three spawn in one cycle —
each as a pane in the shared `agents` window. Tasks gated behind `depends_on` only become runnable
once their dependency is `done` (step 5), so the todo build naturally fans out over a few cycles:
scaffold → (API, …) → UI.

**Verify (hands-off — do NOT touch the panes):**

```bash
tmux list-panes -s -t harness                               # orchestrator pane + agent panes
for i in $(seq 1 40); do grep '^status:' tasks/todo-app/in-progress/*.yaml; sleep 3
  grep -ql 'pr-opened\|blocked' tasks/todo-app/in-progress/*.yaml && break; done
tmux capture-pane -p -t %7 -S -40 | tail                   # see the worker's transcript (its pane id)
git -C ~/wt-TASK-001 log --oneline -1                       # the worker's commit exists
```

The real worker does an actual implementation + Definition-of-Done run, so expect ~1-3 min per
task (vs the stub's ~20-30s), still **without any human intervention**. If a worker hangs, capture
its pane — a "Do you trust the files in this folder?" prompt means the trust pre-seed
(`tmux_manager.pretrust_path`) didn't apply.

### 5. Teardown (orchestrator step 4)

For each `pr-opened` task — **kill the pane only; the worktree persists for QA to reuse** (it's
one-per-task, removed later when the task goes terminal — not at this handoff):

```bash
conda run -n harness python -m harness.tmux_manager kill-pane --pane %7
conda run -n harness python -m harness.state_manager remove-worker --task-id TASK-001
mv tasks/todo-app/in-progress/TASK-001.yaml tasks/todo-app/review/TASK-001.yaml
# NOTE: do NOT remove-worktree here — the QA agent (next section) reuses ~/wt-TASK-001.
```

**Verify:** once the last agent pane is killed the `agents` window closes, leaving only window 0
(`orchestrator`); `active_workers` is `[]`; the torn-down task(s) in `tasks/todo-app/review/`. **`git
worktree list` still shows `wt-TASK-001`** — that's expected now; it's reclaimed only when the task
reaches `qa-passed` / `blocked-escalated` / `blocked` / `done`.

### 6. QA cycle (orchestrator step 4) — issue #4

QA is now built. After teardown a `pr-opened` task sits in `tasks/todo-app/review/`. The orchestrator (step
4D) spawns an **async QA agent** on the worker's branch; here we drive it by hand.

```bash
TID=TASK-001
# the worker filled `branch:` and wrote a qa-instructions-<id>.md recipe at pr-opened — confirm:
grep -E '^(branch|qa_instructions|status):' tasks/todo-app/review/$TID.yaml

# spawn QA on the EXISTING branch — reuses the worker's persistent worktree (role: qa).
# provision-worker resolves ../wt-$TID from the task id, so $WT below is the SAME
# checkout the worker used; only a fresh pane is opened in it.
PF=$(mktemp /tmp/spawn-$TID.XXXXXX)
cat > "$PF" <<EOF
You are the QA agent on the agent-harness. Follow the /qa-agent skill.
TASK_FILE: $PWD/tasks/todo-app/review/$TID.yaml
Your worktree (cwd) is on branch task/$TID. Read TASK_FILE for the expected_behavior
rubric and qa_instructions recipe, RUN the feature, and write your verdict
(looks-good / needs-changes / escalate) into the qa-report status. Do not edit the task status.
EOF
J=$(conda run -n harness python -m harness.tmux_manager provision-worker --task-id $TID --existing-branch --label qa --prompt-file "$PF")
WT=$(echo "$J" | python3 -c 'import json,sys;print(json.load(sys.stdin)["worktree"])')
SID=$(echo "$J" | python3 -c 'import json,sys;print(json.load(sys.stdin)["session_id"])')
PANE=$(echo "$J" | python3 -c 'import json,sys;print(json.load(sys.stdin)["pane"])')
conda run -n harness python -m harness.state_manager add-worker --task-id $TID --worktree "$WT" --pane "$PANE" --started "$(date -u +%FT%TZ)" --model sonnet --role qa
conda run -n harness python -m harness.tmux_manager launch-worker --worktree "$WT" --session-id "$SID" --prompt-file "$PF" --pane "$PANE"
```

**Watch** the QA agent's pane run the functions, then read the verdict:

```bash
grep -m1 '^\*\*status:\*\*' "$(grep '^qa_report:' tasks/todo-app/review/$TID.yaml | awk '{print $2}')"
```

**Route by verdict** (orchestrator step 4C). **Before spawning ANY QA agent (first or re-QA), reset
the report `**status:**` to `WIP`** (or delete the report) so a read can't consume the previous
round's stale verdict. Then, after a verdict, tear down the QA agent — **kill-pane → remove-worker**.
**Remove the worktree only on a terminal verdict** (`looks-good`/`escalate`); on `needs-changes`
keep it so the Opus fixer (and re-QA) reuse the same checkout:

- `looks-good` → `status: qa-passed`; card → Human Review. **Terminal → also `remove-worktree --path $WT`.**
- `escalate` → `status: blocked-escalated` + set `failure_reason`; card → Human Review. **Terminal → also `remove-worktree --path $WT`.**
- `needs-changes` → `status: qa-failed`, bump `qa_failure_count` to 1; card → QA Failed; **keep the worktree**; then spawn
  the **Opus fixer** (same three calls, `--label fixer`, `--role fixer`, `--model opus` on
  launch-worker, prompt = "/worker … FIX MODE"). It pushes to the same branch and sets
  `pr-updated`; re-spawn QA. **2nd-pass re-QA is terminal:** anything but `looks-good` → `escalate`.

**Two test hooks for the QA loop.** Real behavioral QA (via `playwright-cli` / `bruno`) is the
default. Two env vars exercise the loop on demand — they sit at **different layers** and answer
**different questions**, and they are **mutually exclusive** (set at most one; if both are set,
`HARNESS_QA_FORCE` wins and `HARNESS_QA_VERIFY` is ignored, because forced QA never runs for real):

| Hook | Read by | What it does | What it proves |
|---|---|---|---|
| `HARNESS_QA_FORCE` | the **QA agent** | forces a *verdict* without running the feature | the retry/escalate **routing** (plumbing) |
| `HARNESS_QA_VERIFY` | the **orchestrator** | injects a *real bug* into the branch after `pr-opened`, QA runs for real | that QA **catches** defects and the fix loop heals them |

**`HARNESS_QA_FORCE` (force the verdict — tests routing).** Set it in the tmux server env
(`tmux setenv -g HARNESS_QA_FORCE needs-changes`):

- `HARNESS_QA_FORCE=needs-changes` → QA forces `needs-changes` on **pass 1** (the report tells the
  fixer it's a TEST HOOK → make a trivial change), then `looks-good` on **re-QA** (`qa_failure_count≥1`).
  This drives the whole happy ladder: `needs-changes → Opus fixer (trivial commit, same branch) →
  pr-updated → re-QA → looks-good → qa-passed`. **Validated live on TASK-004, 2026-06-01.**
- `HARNESS_QA_FORCE=escalate` → QA forces `escalate` immediately → `blocked-escalated` + `failure_reason`.
- Unset (`tmux setenv -gu HARNESS_QA_FORCE`) to fall back to real behavioral QA.

**`HARNESS_QA_VERIFY` (inject a real bug — verifies QA actually works).** Where `HARNESS_QA_FORCE`
*bypasses* QA's judgment, `HARNESS_QA_VERIFY` *exercises* it: the orchestrator deliberately breaks
the worker's code so genuine behavioral QA has a real defect to catch. Set it (any non-empty value)
in the tmux server env *before* the QA spawn for a `pr-opened` task:

```bash
tmux setenv -g HARNESS_QA_VERIFY 1     # (and make sure HARNESS_QA_FORCE is UNSET)
```

When set, the orchestrator — at the point it would spawn the **first** QA for a `pr-opened` task,
and **only** if the task hasn't already been sabotaged — injects **one small, behavior-visible**
defect that violates a rubric item (e.g. the `POST /api/todos` handler drops the `completed` field
so created todos come back without it, or the UI checkbox never PATCHes the server). It commits that
on the `task/<id>` branch, records `qa_verify_injected: true` on the task file (so it's injected
exactly once and survives a crash), then spawns QA **normally** — QA is never told a bug was planted.
Real QA observes the broken behavior → `needs-changes` → Opus fixer repairs it → `pr-updated` →
re-QA → `looks-good` → `qa-passed`. The full ladder runs on a **genuine** verdict, end to end.
See the orchestrator skill, step 4 (test-scaffold sub-step **4S**), for the exact mechanics.
**Validated live on TASK-001, 2026-06-04** (todo-app): 4S injected a one-line PATCH defect
(`todo.completed` forced to `false`) on `task/TASK-001`; real QA caught it behaviorally
(`needs-changes`: "PATCH `{completed:true}` → 200 but body shows `completed:false`", 9/10 tests) with
no forced wording; the Opus fixer repaired it (`pr-updated`); re-QA returned `looks-good` (10/10) →
`qa-passed`. Injected exactly once (`qa_verify_injected: true`, no double-inject on re-QA).

> **Note (worktree-lifecycle change):** the validation above predates the one-worktree-per-task
> change. 4S no longer cuts an ephemeral `wt-verify-<id>`; it injects the defect **directly into the
> task's persistent `wt-<id>`** (the worker's, which now survives `pr-opened`) and commits there, with
> nothing to tear down — that same worktree goes on to host QA and the fixer.

To drive this section by hand (the orchestrator does it automatically when the var is set): on the
PR branch, hand-edit the implementation to violate one rubric item before spawning QA, commit it,
then run QA as above and watch it return `needs-changes`.

**Dead-QA infra retry:** kill the QA pane before it writes a verdict (report stuck at `WIP`); the
orchestrator increments `qa_run_attempts` and respawns QA; at 2 it sets `blocked-escalated` +
`failure_reason`.

### 7. Archive (orchestrator step 5)

`done` is set by a (future) merge/archival agent that detects your manual merge; for the toy
(local-marker, no remote) simulate it by flipping a `qa-passed` task to `done`:

```bash
for f in tasks/todo-app/review/*.yaml; do grep -q '^status: done' "$f" && mv "$f" "tasks/todo-app/done/$(basename "$f")"; done
```

**Verify:** archived tasks in `tasks/todo-app/done/`; `active_workers` `[]`; the tasks board shows cards in
**Human Review**/**Done**; the next approved spec only releases once every task is `qa-passed`/
`done`/`blocked`/`blocked-escalated` (run `state_manager next-spec` to confirm the gate).

---

## Cleanup

```bash
tmux kill-session -t harness 2>/dev/null
rm -rf "$PROJ" ~/wt-TASK-* ~/wt-qa-* ~/wt-fix-* ~/wt-verify-* /tmp/harness-prompts /tmp/spawn-*
```

Trust entries for the deleted worktrees linger in `~/.claude.json` under `projects` — harmless,
but removable if you want a pristine config.

---

## Known gaps this test exposes

See `build-roadmap.md` and the `harness-worker-runtime-gaps` memory for status. In brief:

| Gap | Status | Workaround in this playbook |
|---|---|---|
| Folder-trust dialog hangs unattended workers | **Fixed** (`tmux_manager.pretrust_path`) | — |
| Tool-permission prompts hang unattended sessions | **Fixed** (`--dangerously-skip-permissions` in wrappers) | — |
| Worker's worktree can't see `tasks/<feature>/in-progress/<id>.yaml` | **Fixed** (ADR-001: gitignored control-plane, absolute-path access) | spawn prompt passes the absolute main-repo path |
| `python -m harness.*` not importable; no `run-orchestrator.sh` | **Fixed** (ADR-002: `harness` conda env + editable install, `conda run -n harness`; `run-orchestrator.sh` added) | — |
| Base branch hardcoded to `main` (broke `master` repos) | **Fixed** (`resolve_base_branch` auto-detects current branch; `HARNESS_BASE_BRANCH` override) | — |
