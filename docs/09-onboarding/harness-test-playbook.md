# Harness End-to-End Test Playbook

A repeatable procedure for proving the harness control loop works on a throwaway project:
**approved spec → breakdown → workers → `pr-opened` → review → done → archive**.

Use this to smoke-test the harness after changing the wrappers, the orchestrator skill, or
the worker/breakdown skills. It is deliberately stubbed and trivial — the goal is to exercise
the *plumbing and control flow*, not to build anything real. First validated 2026-05-28.

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

The real `/task-breakdown` and `/worker` skills live (user-scope) at
`~/.claude/skills/{task-breakdown,worker}/SKILL.md`. As of 2026-05-28 they replaced the original
test stubs:

- **`/task-breakdown <spec>`** — reads the spec's acceptance criteria, its referenced ADRs, and
  `CLAUDE.md`, then shapes a *dependency-aware* set of schema-valid task YAMLs into `tasks/backlog/`
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

### 1. Bootstrap the dummy project

Worktrees are cut from the repo's current branch (auto-detected by
`tmux_manager.resolve_base_branch`, override with `HARNESS_BASE_BRANCH`), so `main` or `master`
both work — no special init needed. `main` is used here just for concreteness.

```bash
rm -rf "$PROJ" ~/wt-TASK-*            # clean slate
mkdir "$PROJ" && cd "$PROJ"
git init -b main -q
git config user.email dummy@test.local && git config user.name "Dummy Tester"
echo "# Dummy" > README.md && git add README.md && git commit -qm "initial commit"
bash ~/agent-harness/scripts/setup.sh
```

Fill `CLAUDE.md` with a trivial "implementing a task = drop a text file under `src/`" brief,
then commit the scaffolding so worktrees inherit it:

```bash
git add CLAUDE.md .gitignore docs/specs/_template.md && git commit -qm "scaffolding"
```

**Verify:** `tasks/{backlog,in-progress,review,done}/` and `docs/specs/` exist;
`orchestrator-state.yaml` present; on branch `main`.

### 2. Drop an approved spec

Create `docs/specs/feature-greeting.md` with frontmatter `status: approved` and two trivial
ACs. (Copy `docs/specs/_template.md` and edit.)

### 3. Breakdown (orchestrator step 2)

```bash
cd "$PROJ"
conda run -n harness python -m harness.state_manager read                       # baseline state
bash ~/agent-harness/scripts/run-breakdown.sh docs/specs/feature-greeting.md
```

**Verify:** two files in `tasks/backlog/`, schema-valid:

```bash
python3 -c "import yaml,jsonschema,glob; s=yaml.safe_load(open('$HOME/agent-harness/schemas/task.schema.yaml')); [jsonschema.validate(yaml.safe_load(open(f)),s) or print(f,'VALID') for f in glob.glob('tasks/backlog/*.yaml')]"
```

Then flip the spec frontmatter `approved → in-breakdown` (the one-way latch).

> Note: `run-breakdown.sh` is **synchronous** (`claude --print`), so the task files exist the
> moment it returns — breakdown and the first spawn happen in the *same* cycle, despite the
> orchestrator skill's "next cycle" wording.

### 4. Spawn workers (orchestrator step 3)

```bash
conda run -n harness python -m harness.state_manager runnable                   # → TASK-001, TASK-002
```

For each runnable task — **move the file first** (avoids a read-before-move race), then spawn
with an absolute task-file path in the prompt:

```bash
cd "$PROJ"
mv tasks/backlog/TASK-001.yaml tasks/in-progress/TASK-001.yaml
PROMPT=$(mktemp /tmp/spawn-TASK-001.XXXX.txt)
cat > "$PROMPT" <<EOF
You are a worker on the agent-harness. Follow the /worker skill.
TASK_FILE: $PROJ/tasks/in-progress/TASK-001.yaml
Your git worktree is your current directory (branch task/TASK-001). Read TASK_FILE
(absolute path) plus the spec/CLAUDE.md/code it points at (relative paths in your
worktree), implement the task for real, satisfy the project's Definition of Done,
commit on your branch, open a PR (real if a remote exists, else a local:// marker),
then edit TASK_FILE to set status: pr-opened. If you hit an unresolvable gap, set
status: blocked and stop.
EOF
conda run -n harness python -m harness.tmux_manager spawn-worker --task-id TASK-001 --prompt-file "$PROMPT"
```

Then record the worker (use the returned `worktree`/`window`):

```bash
# edit tasks/in-progress/TASK-001.yaml: status: in-progress, worktree, window, started, assigned_to
conda run -n harness python -m harness.state_manager add-worker --task-id TASK-001 \
  --worktree ~/wt-TASK-001 --window 1 --started 2026-01-01T00:00:00Z --model sonnet
```

Repeat for TASK-002. `max_workers` is 3, so both spawn in one cycle.

**Verify (hands-off — do NOT touch the windows):**

```bash
tmux list-windows -t harness                                # orchestrator + worker-* windows
for i in $(seq 1 40); do grep '^status:' tasks/in-progress/*.yaml; sleep 3
  grep -ql 'pr-opened\|blocked' tasks/in-progress/*.yaml && break; done
tmux capture-pane -p -t harness:1 -S -40 | tail            # see the worker's transcript
git -C ~/wt-TASK-001 log --oneline -1                       # the worker's commit exists
```

The real worker does an actual implementation + Definition-of-Done run, so expect ~1-3 min per
task (vs the stub's ~20-30s), still **without any human intervention**. If a worker hangs, capture
its pane — a "Do you trust the files in this folder?" prompt means the trust pre-seed
(`tmux_manager.pretrust_path`) didn't apply.

### 5. Teardown (orchestrator step 4)

For each `pr-opened` task:

```bash
conda run -n harness python -m harness.tmux_manager kill-window --window 1
conda run -n harness python -m harness.tmux_manager remove-worktree --path ~/wt-TASK-001
conda run -n harness python -m harness.state_manager remove-worker --task-id TASK-001
mv tasks/in-progress/TASK-001.yaml tasks/review/TASK-001.yaml
```

**Verify:** only window 0 (`orchestrator`) remains; `git worktree list` shows only the main
worktree; `active_workers` is `[]`; both tasks in `tasks/review/`.

### 6. Archive (orchestrator step 5)

QA/review (Stage 6) isn't built — simulate approval by flipping the `review/` task files to
`status: done`, then:

```bash
for f in tasks/review/*.yaml; do grep -q '^status: done' "$f" && mv "$f" "tasks/done/$(basename "$f")"; done
```

**Verify:** both tasks in `tasks/done/`; all other `tasks/` dirs empty; `active_workers` `[]`.

---

## Cleanup

```bash
tmux kill-session -t harness 2>/dev/null
rm -rf "$PROJ" ~/wt-TASK-* /tmp/harness-prompts /tmp/spawn-TASK-*
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
| Worker's worktree can't see `tasks/in-progress/<id>.yaml` | **Fixed** (ADR-001: gitignored control-plane, absolute-path access) | spawn prompt passes the absolute main-repo path |
| `python -m harness.*` not importable; no `run-orchestrator.sh` | **Fixed** (ADR-002: `harness` conda env + editable install, `conda run -n harness`; `run-orchestrator.sh` added) | — |
| Base branch hardcoded to `main` (broke `master` repos) | **Fixed** (`resolve_base_branch` auto-detects current branch; `HARNESS_BASE_BRANCH` override) | — |
