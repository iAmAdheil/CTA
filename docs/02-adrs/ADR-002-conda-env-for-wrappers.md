---
status: accepted
date: 2026-05-28
supersedes: null
superseded_by: null
---

# ADR-002: Harness wrappers run in a dedicated conda env, invoked via `conda run`

## Context

The `/orchestrator` skill calls the Python wrappers as `python -m harness.state_manager …` /
`python -m harness.tmux_manager …` from the *project* repo's cwd. The `harness` package lives at
`~/agent-harness`, which is not on `sys.path` from a project dir, so `-m harness.*` fails with
`No module named harness`. This blocked the first end-to-end test (`harness-test-playbook.md`),
which only worked because every call was manually prefixed with `PYTHONPATH=~/agent-harness`.

Two facts shaped the fix:

- It is a **module-resolution** problem, not a dependency problem — the sole third-party dep
  (PyYAML) is already installed. Python simply can't *find* the package.
- In a Claude session, **each Bash tool call is a fresh process** that inherits the *session's*
  environment. So `export PYTHONPATH=…` in one Bash call does not carry to the next; an exported
  `PYTHONPATH` only helps if the *session itself* was launched with it set. Any fix that depends on
  launch method (e.g. only a launcher that exports `PYTHONPATH`) breaks when someone just types
  `/orchestrator` in an existing session.

The machine already runs Anaconda (`python3` is the anaconda base interpreter; conda 25.x present),
and heavier real dependencies are coming (Stage 5 Linear client, Stage 6 Playwright/Stagehand), so
a managed environment will pay off beyond just fixing imports.

## Decision

Run the wrappers inside a **dedicated conda env** (`harness`) with the package **editable-installed**,
and invoke them with **`conda run -n harness python -m harness.<module> …`**.

- `conda run -n <env>` executes a command inside the env *without* sourcing `conda.sh` or
  persisting an activation, so it is safe in every fresh Bash call — the fresh-shell-robust form of
  "activate the env first." It needs no `PYTHONPATH`, no absolute file paths, no launch-method
  assumptions.
- A minimal `pyproject.toml` (setuptools backend, declares `harness` + PyYAML) makes the package
  installable. `pip install -e` keeps it pointed at the live source, so wrapper edits take effect
  immediately with no reinstall.
- `setup.sh` creates the env and runs the editable install, **idempotently** (created once, no-op
  thereafter). It is machine-level setup that happens to live in the per-project bootstrap; running
  `setup.sh` in a second project just no-ops the env step. Env name overridable via
  `HARNESS_CONDA_ENV`. If `conda` is absent, `setup.sh` warns and skips (falling back to a manual
  `PYTHONPATH`).
- `scripts/run-orchestrator.sh` is the launcher `setup.sh` advertises: from the project root it
  `exec`s `claude /orchestrator` (with `--dangerously-skip-permissions` by default, since the
  orchestrator runs unattended and spawns workers). Cadence is the caller's job (`/loop`, cron,
  file-watcher).

## Consequences

- **Imports resolve regardless of how the session started** — launcher, `/loop`, cron, or a
  hand-typed `/orchestrator` all work, because resolution is per-call via `conda run`, not via
  inherited session env.
- **Real dependency isolation going forward.** Stage 5–7 deps go into the `harness` env, not the
  user's base/system Python.
- **`conda run` adds ~0.5–1s startup per call.** Acceptable at orchestrator cadence; not suitable
  for tight inner loops (there are none here).
- **A second runtime requirement: conda + the `harness` env.** Captured in the playbook
  prerequisites; `setup.sh` provisions it; missing-env errors point the user back to `setup.sh`.
- The wrappers themselves are unchanged — they remain plain `python -m harness.*` modules; only the
  invocation prefix and the env provisioning changed.

Related: ADR-001 (control-plane vs committed files), `harness-test-playbook.md`, build-roadmap
"Open (next)", memory `harness-worker-runtime-gaps`.
