#!/usr/bin/env bash
# run-orchestrator.sh — run one orchestrator cycle in a project repo.
#
# Usage (from the project root):
#   bash ~/agent-harness/scripts/run-orchestrator.sh
#
# The /orchestrator skill calls the harness wrappers via `conda run -n <env>`
# (see ADR-002), so no PYTHONPATH is needed. cwd MUST be the project root so
# claude finds tasks/, docs/, orchestrator-state.yaml, and .claude/.
#
# Cadence (every N seconds, on file-change, etc.) is the caller's job — wrap
# this in /loop, cron, or a file-watcher. This script runs the loop exactly once.
#
# Env overrides:
#   HARNESS_CLAUDE_DANGEROUS  default 1 — the orchestrator runs unattended and
#                             spawns workers, so skip permission prompts. Set 0
#                             to run with prompts (interactive use).

set -euo pipefail

if [ ! -d tasks ] || [ ! -f orchestrator-state.yaml ]; then
  echo "Not in a harness project root (missing tasks/ or orchestrator-state.yaml)." >&2
  echo "Run from the project root, after: bash ~/agent-harness/scripts/setup.sh" >&2
  exit 1
fi

cmd=(claude)
if [ "${HARNESS_CLAUDE_DANGEROUS:-1}" = "1" ]; then
  cmd+=(--dangerously-skip-permissions)
fi
cmd+=("/orchestrator")

exec "${cmd[@]}"
