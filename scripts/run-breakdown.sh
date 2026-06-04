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
#     `references:` files the spec points at. It creates the feature workspace
#     tasks/<feature>/{backlog,in-progress,review,done}/ + board.md and writes N
#     task YAML files to tasks/<feature>/backlog/ via the Write tool.
#
# Environment overrides:
#   HARNESS_BREAKDOWN_MODEL       default: sonnet
#   HARNESS_BREAKDOWN_BUDGET_USD  default: 1.00 (set empty to disable cap)
#
# Notes:
#   - The /task-breakdown skill lives at user scope (~/.claude/skills/task-breakdown/).
#   - cwd must be the project root so claude finds docs/, tasks/, CLAUDE.md, etc.

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
# The breakdown runs unattended (no human to approve tool use). Skip permission
# prompts by default; set HARNESS_CLAUDE_DANGEROUS=0 to opt out.
if [ "${HARNESS_CLAUDE_DANGEROUS:-1}" = "1" ]; then
  cmd+=(--dangerously-skip-permissions)
fi
cmd+=("/task-breakdown $SPEC_PATH")

exec "${cmd[@]}"
