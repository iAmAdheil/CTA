# Harness Build Progress

Snapshot of what's actually been built and verified in `/Users/abhishekgupta/agent-harness/`. Each ✅ corresponds to a file shipped or a check that was run.

---

## Stage 1 — Repo & Foundation

- [x] git repo initialized on `main`
- [x] `.gitignore` covering `.env`, `__pycache__/`, `*.pyc`, `.DS_Store`, `orchestrator-state.yaml`
- [x] Top-level directory layout: `harness/`, `templates/`, `schemas/`, `scripts/`, `agent-prompts/`
- [x] `templates/task.yaml` — all task fields (`status`, `priority`, `depends_on`, `blocks`, `can_parallelize_with`, `model`, `linear_id`, `worktree`, `window`, `pr_url`, `pr_number`, `qa_failure_count`, `qa_out_of_scope_bugs`, `suggested_files_to_read`, `references`)
- [x] `templates/progress.md` — `## Log`, `## Blockers`, `## Decisions`, `## Pre-existing Failures` sections
- [x] `templates/qa-report.md` — AC table, automated-test pass, manual-nav pass, out-of-scope findings, verdict (`PASS` / `CONDITIONAL PASS` / `FAIL`)
- [x] `schemas/task.schema.yaml` — JSON Schema for task files, enumerates the eight valid `status` values
- [x] `schemas/orchestrator-state.schema.yaml` — JSON Schema for `orchestrator-state.yaml`
- [x] `scripts/setup.sh` — idempotent project-repo bootstrap. Creates `tasks/{backlog,in-progress,review,done}/`, `docs/{specs,adrs,active-features}/`, `agent-prompts/` symlink to the harness repo, starter `CLAUDE.md` (with placeholder section headers), empty `orchestrator-state.yaml`, and adds `.gitignore` entries
- [x] Verified `setup.sh` on a clean tempdir — first run produces the full structure; second run is a no-op
- [x] Schemas validated as parseable YAML; task template validates against the task schema

## Stage 2 — Core Wrappers

Wrappers-only scope. The end-to-end loop demo is deferred until `orchestrator.md` is written in the agent-prompts session.

- [x] `requirements.txt` (PyYAML — confirmed already on system)
- [x] `harness/__init__.py` (package marker)
- [x] `harness/state_manager.py`
  - [x] `read_state` / `write_state` — atomic via `tempfile.mkstemp` + `os.replace()`
  - [x] `add_worker` — idempotent on `task_id`
  - [x] `remove_worker` — no-op if absent
  - [x] `get_runnable_tasks` — filters `backlog` tasks whose `depends_on` is a subset of the done set
  - [x] `load_tasks_in(directory)` — reads every `*.yaml` in a tasks/ subdir
  - [x] CLI: `python -m harness.state_manager {init, read, add-worker, remove-worker, runnable}`
- [x] `harness/tmux_manager.py`
  - [x] `ensure_session` / `kill_session` — manage the `harness` tmux session
  - [x] `create_worktree` — `git worktree add -b`, idempotent on path
  - [x] `remove_worktree` — `git worktree remove`, with `--force` fallback for dirty trees
  - [x] `kill_window` — idempotent
  - [x] `spawn_worker` — creates worktree + opens tmux window + launches `claude --session-id <uuid>` with the prompt passed via a temp file (avoids multi-KB shell-quoting issues)
  - [x] `send_nudge` — `tmux load-buffer` + `paste-buffer` for long/multiline text (handles `$`, `"`, `` ` ``, newlines verbatim); `tmux send-keys -l` for short single-line text; always followed by `Enter` unless `--no-submit`
  - [x] CLI: `python -m harness.tmux_manager {spawn-worker, nudge, kill-window, remove-worktree, ensure-session, list-windows}`
- [x] `harness/_spawn_worker.sh` — internal shell helper executed inside a fresh tmux window; `exec`s `claude --session-id "$2" "$(cat "$3")"` in the worktree directory
- [x] Verified `state_manager` end-to-end via CLI: `init` → `add-worker` × 2 → `read` (JSON) → `remove-worker` → `runnable` (correctly returned `TASK-A` + `TASK-C`, excluded `TASK-B` whose dep was still in backlog)
- [x] Verified `spawn_worker` with a stub `claude` binary: prompt containing `"quotes"`, `$dollars`, `` `backticks` ``, and a newline reached the worker process verbatim
- [x] Verified `send_nudge` short-text path (`send-keys -l`) and long-multiline path (`paste-buffer`) by capturing the typed text in a file via `cat > …`
- [x] Verified `kill_window` and `remove_worktree` clean up correctly and are safe to call twice

## Stage 3 — Task Breakdown Scaffolding

Scaffolding-only scope. The `/task-breakdown` skill (the actual breakdown agent) is deferred to the agent-prompts session. The end-to-end demo (approved spec → task files in `tasks/backlog/`) waits for that skill to exist.

- [x] `templates/spec.md` — spec template. Frontmatter (`status`, `priority`, `linear_epic_id`), and sections for Overview, Acceptance Criteria, Out of Scope, References, Implementation Notes
- [x] `scripts/run-breakdown.sh` — thin wrapper that runs `claude --print --model sonnet --max-budget-usd 1.00 "/task-breakdown <spec-path>"`. Validates spec path; budget cap + model overridable via env vars
- [x] `scripts/setup.sh` updated — now copies `templates/spec.md` → `docs/specs/_template.md` on first run; idempotent on re-run
- [x] Verified `setup.sh` on a clean tempdir: template copied first run, no-op on second
- [x] Verified `run-breakdown.sh` argument validation: missing arg exits 2, missing-file exits 1 (live `claude` call not exercised since the skill doesn't exist yet)

---

## Session memories saved

These shape future sessions' behavior on this project; not code, but durable decisions.

- [x] `harness-references-philosophy.md` — handoff prompts pass file paths / skill names; the receiving agent fetches content itself
- [x] `orchestrator-is-llm-agent.md` — orchestrator is an LLM session with an instruction prompt, not a `while True` Python loop; build-guide Stage 2 is out of date on this
