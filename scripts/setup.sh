#!/usr/bin/env bash
# setup.sh — bootstrap the harness directory structure inside a project repo.
#
# Run once from the root of a new project repo:
#   bash ~/agent-harness/scripts/setup.sh
#
# Idempotent: re-running won't clobber CLAUDE.md or existing task files.

set -euo pipefail

HARNESS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_ROOT="$(pwd)"

echo "Harness root:  $HARNESS_ROOT"
echo "Project root:  $PROJECT_ROOT"
echo

# ---------------------------------------------------------------------------
# 1. tasks/
# ---------------------------------------------------------------------------
# Task files are feature-scoped: tasks/<feature>/{backlog,in-progress,review,done}/.
# The per-feature workspace (its four state subdirs + board.md) is created on
# demand by task-breakdown when a spec is broken down, so we only seed the root.
mkdir -p tasks
echo "✓ tasks/  (feature workspaces created per-spec by task-breakdown)"

# ---------------------------------------------------------------------------
# 2. docs/
# ---------------------------------------------------------------------------
mkdir -p docs/specs docs/adrs docs/active-features
echo "✓ docs/{specs,adrs,active-features}/"

# ---------------------------------------------------------------------------
# 3. Remove any stale agent-prompts/ symlink from older setups.
# ---------------------------------------------------------------------------
# The base agent skills are installed into .claude/skills/ (section 3b below);
# the old project-side agent-prompts symlink into the harness repo is dead.
# Clean it up if a prior run created it.
if [ -L agent-prompts ]; then
  rm -f agent-prompts
  echo "✓ removed stale agent-prompts/ symlink"
fi

# ---------------------------------------------------------------------------
# 3b. .claude/skills/ — install the base harness agent skills into the project
# ---------------------------------------------------------------------------
# The harness's core agents (orchestrator, task-breakdown, worker, qa-agent,
# advisor) are version-controlled in this repo under skills/ and copied into the
# project's .claude/skills/ so Claude Code loads them as PROJECT-scoped skills.
# They are tracked (NOT gitignored) on purpose: workers and QA agents run inside
# git worktrees, which only contain COMMITTED files — so the skills must be
# committed to be loadable there. Re-copied on every run so skill updates in the
# harness repo propagate; edit skills in the harness repo's skills/, not here
# (local edits to .claude/skills/ are overwritten on the next setup run).
if [ -d "$HARNESS_ROOT/skills" ]; then
  mkdir -p .claude/skills
  for skill_dir in "$HARNESS_ROOT"/skills/*/; do
    name="$(basename "$skill_dir")"
    rm -rf ".claude/skills/$name"
    cp -R "$skill_dir" ".claude/skills/$name"
    echo "✓ .claude/skills/$name (from harness skills/)"
  done
  echo "  → commit .claude/skills/ so worker & QA worktrees can load these skills"
else
  echo "! harness skills/ missing — base agent skills NOT installed"
fi

# ---------------------------------------------------------------------------
# 4. docs/specs/_template.md (copy from harness templates if missing)
# ---------------------------------------------------------------------------
if [ -f docs/specs/_template.md ]; then
  echo "• docs/specs/_template.md already exists — leaving alone"
elif [ -f "$HARNESS_ROOT/templates/spec.md" ]; then
  cp "$HARNESS_ROOT/templates/spec.md" docs/specs/_template.md
  echo "✓ docs/specs/_template.md (copied from harness templates/spec.md)"
else
  echo "! harness templates/spec.md missing — skipped"
fi

# ---------------------------------------------------------------------------
# 5. CLAUDE.md (starter, only if missing)
# ---------------------------------------------------------------------------
if [ -f CLAUDE.md ]; then
  echo "• CLAUDE.md already exists — leaving alone"
else
  cat > CLAUDE.md <<'EOF'
# CLAUDE.md

Operating manual for agents working in this project. Loaded at the start of every agent session.

## Project Overview

<!-- One paragraph: what this project is, who uses it, what stage it's at. -->

## Stack

<!-- Languages, frameworks, key dependencies. -->

## Codebase Conventions

<!-- File layout, naming, import patterns, anything an agent should not have to infer. -->

## Testing

<!-- How to run lint, typecheck, tests. Which suites are flaky. -->

## Definition of Done

<!-- What "done" means before a worker opens a PR (lint clean, tests green, etc.). -->

## Things Not To Do

<!-- Sharp edges, footguns, deprecated paths. -->
EOF
  echo "✓ CLAUDE.md (starter)"
fi

# ---------------------------------------------------------------------------
# 6. orchestrator-state.yaml (empty initial state)
# ---------------------------------------------------------------------------
if [ -f orchestrator-state.yaml ]; then
  echo "• orchestrator-state.yaml already exists — leaving alone"
else
  cat > orchestrator-state.yaml <<'EOF'
active_workers: []
max_workers: 3
queue: []
last_action: null
last_action_time: null
cycle_count: 0
EOF
  echo "✓ orchestrator-state.yaml (empty)"
fi

# ---------------------------------------------------------------------------
# 6b. docs/_kanban/ persistent boards (orchestrator moves cards across columns)
# ---------------------------------------------------------------------------
# These are NOT regenerated each cycle — they persist, and the orchestrator
# relocates one card at a time as task/spec status changes. Created once with an
# empty column skeleton; never clobbered on re-run.
mkdir -p docs/_kanban
if [ -f docs/_kanban/specs-board.md ]; then
  echo "• docs/_kanban/specs-board.md already exists — leaving alone"
else
  cat > docs/_kanban/specs-board.md <<'EOF'
---
kanban-plugin: basic
---

## Under Review


## Approved


## Acknowledged


## Blocked

EOF
  echo "✓ docs/_kanban/specs-board.md (skeleton)"
fi
if [ -f docs/_kanban/tasks-board.md ]; then
  echo "• docs/_kanban/tasks-board.md already exists — leaving alone"
else
  cat > docs/_kanban/tasks-board.md <<'EOF'
---
kanban-plugin: basic
---

## Backlog


## In Progress


## In Review


## QA Failed


## Human Review


## Blocked


## Done

EOF
  echo "✓ docs/_kanban/tasks-board.md (skeleton)"
fi

# ---------------------------------------------------------------------------
# 7. .gitignore entries
# ---------------------------------------------------------------------------
ensure_gitignore_line() {
  local line="$1"
  if [ ! -f .gitignore ] || ! grep -qxF "$line" .gitignore; then
    echo "$line" >> .gitignore
    echo "✓ .gitignore += $line"
  else
    echo "• .gitignore already has '$line'"
  fi
}
# Control-plane state — gitignored, lives only in the main worktree, addressed
# by absolute path (see docs/02-adrs/ADR-001-control-plane-vs-committed-files.md).
ensure_gitignore_line "orchestrator-state.yaml"
ensure_gitignore_line "tasks/"
ensure_gitignore_line "docs/active-features/"
# Kanban boards are orchestrator-maintained control-plane (a live view of task/
# spec state), so they're gitignored like the rest — not committed project files.
ensure_gitignore_line "docs/_kanban/"
# Secrets + hygiene.
ensure_gitignore_line ".env"
ensure_gitignore_line ".env.*"
ensure_gitignore_line "__pycache__/"
ensure_gitignore_line "*.pyc"
ensure_gitignore_line ".DS_Store"

# ---------------------------------------------------------------------------
# 8. conda env for the harness Python wrappers
# ---------------------------------------------------------------------------
# The orchestrator/worker skills invoke the wrappers via `conda run -n <env>`,
# so the package must be importable inside a dedicated env. This is machine-level
# (not per-project) and idempotent: created once, a no-op on later runs.
# See docs/02-adrs/ADR-002-conda-env-for-wrappers.md.
HARNESS_ENV="${HARNESS_CONDA_ENV:-harness}"
if command -v conda >/dev/null 2>&1; then
  if conda env list | awk '{print $1}' | grep -qx "$HARNESS_ENV"; then
    echo "• conda env '$HARNESS_ENV' already exists"
  else
    echo "Creating conda env '$HARNESS_ENV'…"
    conda create -n "$HARNESS_ENV" python=3.13 pyyaml -y
  fi
  # Editable install (idempotent) so `python -m harness.*` resolves in the env.
  if conda run -n "$HARNESS_ENV" pip install -e "$HARNESS_ROOT" >/dev/null 2>&1; then
    echo "✓ harness editable-installed into conda env '$HARNESS_ENV'"
  else
    echo "! editable install into '$HARNESS_ENV' failed — check 'conda run -n $HARNESS_ENV pip install -e $HARNESS_ROOT'"
  fi
else
  echo "! conda not found — skipping env setup."
  echo "  Wrappers will need PYTHONPATH=$HARNESS_ROOT, or install the package another way."
fi

echo
echo "Done. Next:"
echo "  - Fill out CLAUDE.md"
echo "  - Commit the base agent skills:  git add .claude/skills && git commit -m 'harness: base agent skills'"
echo "    (they must be committed so worker/QA git worktrees can load them)"
echo "  - Drop a spec into docs/specs/ with 'status: approved' to kick off task breakdown"
echo "  - Start the orchestrator: bash $HARNESS_ROOT/scripts/run-orchestrator.sh"
