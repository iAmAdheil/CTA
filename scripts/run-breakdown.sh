#!/usr/bin/env bash
# run-breakdown.sh — invoke the /task-breakdown skill on a spec.
#
# Usage:
#   bash run-breakdown.sh <spec-path>
#
# What it does:
#   - Validates the spec path exists.
#   - Runs `claude --print "/task-breakdown <spec-path>"` from the current
#     directory (must be the project root).
#   - The skill itself reads the spec, existing ADRs, CLAUDE.md, and any
#     `references:` files the spec points at. It writes N task YAML files to
#     tasks/backlog/ via the Write tool.
#
# Environment overrides:
#   HARNESS_BREAKDOWN_MODEL       default: sonnet
#   HARNESS_BREAKDOWN_BUDGET_USD  default: 1.00 (set empty to disable cap)
#
# Notes:
#   - The /task-breakdown skill is not in this repo yet; it's deferred to the
#     dedicated agent-prompts session. Until then this script is scaffolding.
#   - cwd must be the project root so claude finds .claude/skills/, docs/, etc.

set -euo pipefail

SPEC_PATH="${1:-}"
if [ -z "$SPEC_PATH" ]; then
  echo "Usage: $0 <spec-path>" >&2
  exit 2
fi

if [ ! -f "$SPEC_PATH" ]; then
  echo "Spec not found: $SPEC_PATH" >&2
  exit 1
fi

MODEL="${HARNESS_BREAKDOWN_MODEL:-sonnet}"
BUDGET="${HARNESS_BREAKDOWN_BUDGET_USD:-1.00}"

cmd=(claude --print --model "$MODEL")
if [ -n "$BUDGET" ]; then
  cmd+=(--max-budget-usd "$BUDGET")
fi
cmd+=("/task-breakdown $SPEC_PATH")

exec "${cmd[@]}"
