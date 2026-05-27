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
for d in backlog in-progress review done; do
  mkdir -p "tasks/$d"
done
echo "✓ tasks/{backlog,in-progress,review,done}/"

# ---------------------------------------------------------------------------
# 2. docs/
# ---------------------------------------------------------------------------
mkdir -p docs/specs docs/adrs docs/active-features
echo "✓ docs/{specs,adrs,active-features}/"

# ---------------------------------------------------------------------------
# 3. agent-prompts/ symlink → harness repo
# ---------------------------------------------------------------------------
if [ -L agent-prompts ]; then
  echo "• agent-prompts/ symlink already exists"
elif [ -e agent-prompts ]; then
  echo "! agent-prompts/ exists and is not a symlink — leaving alone"
else
  ln -s "$HARNESS_ROOT/agent-prompts" agent-prompts
  echo "✓ agent-prompts/ → $HARNESS_ROOT/agent-prompts"
fi

# ---------------------------------------------------------------------------
# 4. CLAUDE.md (starter, only if missing)
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
# 5. orchestrator-state.yaml (empty initial state)
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
# 6. .gitignore entries
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
ensure_gitignore_line "orchestrator-state.yaml"
ensure_gitignore_line ".env"
ensure_gitignore_line ".env.*"

echo
echo "Done. Next:"
echo "  - Fill out CLAUDE.md"
echo "  - Drop a spec into docs/specs/ with 'status: approved' to kick off task breakdown"
echo "  - Start the orchestrator: bash $HARNESS_ROOT/scripts/run-orchestrator.sh"
