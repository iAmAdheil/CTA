#!/usr/bin/env bash
# orch-run.sh — run ONE orchestrator cycle with live, streaming activity logging.
#
# Run from the project root:
#   bash ~/agent-harness/scripts/orch-run.sh            # foreground, streams to terminal
#   nohup bash ~/agent-harness/scripts/orch-run.sh &    # background; watch with orch-watch.sh -f
#
# Why this exists: run-orchestrator.sh launches the interactive TUI (needs a
# real terminal), and a bare `claude -p /orchestrator` only prints the FINAL
# message — so you can't watch the cycle's activity. This uses
# `--output-format stream-json --verbose` to capture every step as it happens:
#
#   .harness-logs/orch-activity-<ts>.log    human-readable, timestamped (tail this)
#   .harness-logs/orch-stream-<ts>.jsonl    raw event stream (replay/debug)
#   .harness-logs/orch-activity-latest.log  symlink → newest activity log
#   .harness-logs/orch-stream-latest.jsonl  symlink → newest raw stream
#
# Env: HARNESS_CLAUDE_DANGEROUS (default 1) — skip permission prompts.
set -uo pipefail

if [ ! -d tasks ] || [ ! -f orchestrator-state.yaml ]; then
  echo "Not in a harness project root (missing tasks/ or orchestrator-state.yaml)." >&2
  echo "Run from the project root." >&2
  exit 1
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOGDIR=".harness-logs"
mkdir -p "$LOGDIR"
TS="$(date +%Y%m%d-%H%M%S)"
RAW="$LOGDIR/orch-stream-$TS.jsonl"
ACT="$LOGDIR/orch-activity-$TS.log"
ln -sfn "$(basename "$ACT")" "$LOGDIR/orch-activity-latest.log"
ln -sfn "$(basename "$RAW")" "$LOGDIR/orch-stream-latest.jsonl"

cmd=(claude -p "/orchestrator" --output-format stream-json --verbose)
if [ "${HARNESS_CLAUDE_DANGEROUS:-1}" = "1" ]; then
  cmd+=(--dangerously-skip-permissions)
fi

echo "[$(date '+%H:%M:%S')] ▶ orchestrator cycle starting — activity → $ACT" | tee -a "$ACT"
# stream → raw jsonl (tee) → human formatter → activity log + terminal
"${cmd[@]}" 2>>"$ACT" | tee "$RAW" | python3 "$HERE/_fmt_stream.py" | tee -a "$ACT"
echo "[$(date '+%H:%M:%S')] ■ orchestrator cycle exited" | tee -a "$ACT"
