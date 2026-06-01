# Harness End-to-End Test Playbook

A repeatable procedure for proving the harness control loop works on a throwaway project:
**approved spec → breakdown → workers → `pr-opened` → QA → (Opus fixer → re-QA) → `qa-passed` → archive**.

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
export HARNESS_QA_AUTOPASS=1   # interim: QA writes looks-good without a real test env (see §6)
```
(No `PYTHONPATH` — the wrappers resolve through the `harness` conda env via `conda run`.)

> **`HARNESS_QA_AUTOPASS=1`** must be in the **tmux server environment** so spawned QA windows
> inherit it (same propagation as `HARNESS_CLAUDE_DANGEROUS`). Export it **before** the harness
> tmux session is created; if the session already exists, set it with
> `tmux setenv -t harness HARNESS_QA_AUTOPASS 1` (new windows inherit it). Until a behavioral test
> environment (e.g. Playwright browser MCP) is wired, this lets the loop run without QA's verdict
> being load-bearing. **Unset it to get real behavioral QA.** To exercise the *retry/escalate* ladder
> instead of always passing, use `HARNESS_QA_FORCE` (see §6) — it overrides auto-pass.

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

Fill `CLAUDE.md` with a trivial "implementing a task = drop a text file under `src/`" brief,
then commit the scaffolding so worktrees inherit it:

```bash
git add CLAUDE.md .gitignore docs/specs/_template.md && git commit -qm "scaffolding"
```

**Verify:** `tasks/{backlog,in-progress,review,done}/` and `docs/specs/` exist;
`orchestrator-state.yaml` present; on branch `main`.

### 2. Drop an approved spec — and COMMIT it

Create `docs/specs/feature-greeting.md` with frontmatter `status: approved` and two trivial
ACs. (Copy `docs/specs/_template.md` and edit.)

> **⚠️ Commit the spec before breakdown.** Workers read the spec by *relative path from their
> worktree*, which is a **committed git snapshot** — an uncommitted spec is invisible to them and
> every worker blocks with "referenced spec absent". (The advisor and breakdown see it anyway
> because they run in the main worktree — so the failure only shows up at the worker.) In the real
> loop the orchestrator commits the spec at step 2b.0; in this manual playbook, commit it yourself:
> `git add docs/specs/feature-greeting.md && git commit -m "spec: feature-greeting approved"`.
> Surfaced by the issue-#4 e2e run.

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
with an absolute task-file path in the prompt. This manual flow uses the one-shot `spawn-worker`
(worker starts immediately), which is fine when *you* are the only one writing the task file.

> The real `/orchestrator` instead uses the two-phase **`provision-worker`** → bookkeep →
> **`launch-worker`** split so it can set `status: in-progress` + worktree/window/state *before* the
> worker exists — eliminating the status-write race a fast worker would otherwise win. To rehearse
> that flow by hand: `provision-worker …` (returns `window`/`session_id`/`worktree`/`prompt_file`,
> reserves an idle `sleep` window), do the `mv` + status/metadata edit + `add-worker`, then
> `launch-worker --worktree … --session-id … --prompt-file … --window …`.

```bash
cd "$PROJ"
mv tasks/backlog/TASK-001.yaml tasks/in-progress/TASK-001.yaml
PROMPT=$(mktemp /tmp/spawn-TASK-001.XXXXXX)
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

### 6. QA cycle (orchestrator step 4) — issue #4

QA is now built. After teardown a `pr-opened` task sits in `tasks/review/`. The orchestrator (step
4D) spawns an **async QA agent** on the worker's branch; here we drive it by hand.

```bash
TID=TASK-001
# the worker filled `branch:` and wrote a qa-instructions-<id>.md recipe at pr-opened — confirm:
grep -E '^(branch|qa_instructions|status):' tasks/review/$TID.yaml

# spawn QA on the EXISTING branch (own worktree, role: qa)
PF=$(mktemp /tmp/spawn-$TID.XXXXXX)
cat > "$PF" <<EOF
You are the QA agent on the agent-harness. Follow the /qa-agent skill.
TASK_FILE: $PWD/tasks/review/$TID.yaml
Your worktree (cwd) is on branch task/$TID. Read TASK_FILE for the expected_behavior
rubric and qa_instructions recipe, RUN the feature, and write your verdict
(looks-good / needs-changes / escalate) into the qa-report status. Do not edit the task status.
EOF
J=$(conda run -n harness python -m harness.tmux_manager provision-worker --task-id $TID --existing-branch --label qa --prompt-file "$PF")
WT=$(echo "$J" | python3 -c 'import json,sys;print(json.load(sys.stdin)["worktree"])')
SID=$(echo "$J" | python3 -c 'import json,sys;print(json.load(sys.stdin)["session_id"])')
W=$(echo "$J" | python3 -c 'import json,sys;print(json.load(sys.stdin)["window"])')
conda run -n harness python -m harness.state_manager add-worker --task-id $TID --worktree "$WT" --window $W --started "$(date -u +%FT%TZ)" --model sonnet --role qa
conda run -n harness python -m harness.tmux_manager launch-worker --worktree "$WT" --session-id "$SID" --prompt-file "$PF" --window $W
```

**Watch** the QA window run the functions, then read the verdict:

```bash
grep -m1 '^\*\*status:\*\*' "$(grep '^qa_report:' tasks/review/$TID.yaml | awk '{print $2}')"
```

**Route by verdict** (orchestrator step 4C). **Before spawning ANY QA agent (first or re-QA), reset
the report `**status:**` to `WIP`** (or delete the report) so a read can't consume the previous
round's stale verdict. Then, after a verdict, tear down the QA agent (kill-window → remove-worktree →
remove-worker):

- `looks-good` → `status: qa-passed`; card → Human Review.
- `escalate` → `status: blocked-escalated` + set `failure_reason`; card → Human Review.
- `needs-changes` → `status: qa-failed`, bump `qa_failure_count` to 1; card → QA Failed; then spawn
  the **Opus fixer** (same three calls, `--label fixer`, `--role fixer`, `--model opus` on
  launch-worker, prompt = "/worker … FIX MODE"). It pushes to the same branch and sets
  `pr-updated`; re-spawn QA. **2nd-pass re-QA is terminal:** anything but `looks-good` → `escalate`.

**Exercising the retry/escalate ladder (test hook).** Real QA isn't wired yet, so use
`HARNESS_QA_FORCE` (it overrides auto-pass) to drive the routing deterministically — set it in the
tmux server env like the autopass var (`tmux setenv -g HARNESS_QA_FORCE needs-changes`):

- `HARNESS_QA_FORCE=needs-changes` → QA forces `needs-changes` on **pass 1** (the report tells the
  fixer it's a TEST HOOK → make a trivial change), then `looks-good` on **re-QA** (`qa_failure_count≥1`).
  This drives the whole happy ladder: `needs-changes → Opus fixer (trivial commit, same branch) →
  pr-updated → re-QA → looks-good → qa-passed`. **Validated live on TASK-004, 2026-06-01.**
- `HARNESS_QA_FORCE=escalate` → QA forces `escalate` immediately → `blocked-escalated` + `failure_reason`.
- Unset (`tmux setenv -gu HARNESS_QA_FORCE`) to fall back to auto-pass. (For a *real* defect instead
  of the hook, hand-edit the implementation on the branch to violate a rubric item before spawning QA.)

**Dead-QA infra retry:** kill the QA window before it writes a verdict (report stuck at `WIP`); the
orchestrator increments `qa_run_attempts` and respawns QA; at 2 it sets `blocked-escalated` +
`failure_reason`.

### 7. Archive (orchestrator step 5)

`done` is set by a (future) merge/archival agent that detects your manual merge; for the toy
(local-marker, no remote) simulate it by flipping a `qa-passed` task to `done`:

```bash
for f in tasks/review/*.yaml; do grep -q '^status: done' "$f" && mv "$f" "tasks/done/$(basename "$f")"; done
```

**Verify:** archived tasks in `tasks/done/`; `active_workers` `[]`; the tasks board shows cards in
**Human Review**/**Done**; the next approved spec only releases once every task is `qa-passed`/
`done`/`blocked`/`blocked-escalated` (run `state_manager next-spec` to confirm the gate).

---

## Cleanup

```bash
tmux kill-session -t harness 2>/dev/null
rm -rf "$PROJ" ~/wt-TASK-* ~/wt-qa-* ~/wt-fix-* /tmp/harness-prompts /tmp/spawn-*
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
