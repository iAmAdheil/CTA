#!/usr/bin/env bash
# run-advisor.sh — invoke the /advisor skill (Opus) on a spec to check whether
# it needs an architectural decision before task breakdown.
#
# Usage:
#   bash run-advisor.sh <spec-path>
#
# What it does:
#   - Validates the spec path exists.
#   - Runs `claude --print "/advisor <spec-path>"` (Opus) from the current
#     directory (must be the project root).
#   - The skill reads the spec, existing docs/adrs/, and CLAUDE.md, then either:
#       * PROCEED (no arch decision needed), or
#       * PROCEED after writing a docs/adrs/ADR-NNN-*.md (status: proposed) for
#         a low-risk decision it made autonomously, or
#       * BLOCK — writes `status: blocked` + a reason into the spec and stops.
#   - Its final stdout line is the machine contract the caller branches on:
#       ADVISOR_VERDICT: PROCEED
#       ADVISOR_VERDICT: BLOCK — <one-line reason>
#
# Environment overrides:
#   HARNESS_ADVISOR_MODEL       default: opus
#   HARNESS_ADVISOR_BUDGET_USD  default: 1.50 (set empty to disable cap)
#   HARNESS_CLAUDE_DANGEROUS    default: 1 (skip permission prompts; 0 to opt out)
#
# Notes:
#   - The /advisor skill lives at user scope (~/.claude/skills/advisor/).
#   - cwd must be the project root so claude finds docs/, CLAUDE.md, etc.

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

MODEL="${HARNESS_ADVISOR_MODEL:-opus}"
BUDGET="${HARNESS_ADVISOR_BUDGET_USD:-1.50}"

cmd=(claude --print --model "$MODEL")
if [ -n "$BUDGET" ]; then
  cmd+=(--max-budget-usd "$BUDGET")
fi
# The advisor runs unattended (no human to approve tool use). Skip permission
# prompts by default; set HARNESS_CLAUDE_DANGEROUS=0 to opt out.
if [ "${HARNESS_CLAUDE_DANGEROUS:-1}" = "1" ]; then
  cmd+=(--dangerously-skip-permissions)
fi
cmd+=("/advisor $SPEC_PATH")

exec "${cmd[@]}"
