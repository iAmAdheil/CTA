#!/usr/bin/env bash
# harness-up.sh — bring the harness up in a tmux session you attach to and watch.
#
# This is the HEADED launch path. The orchestrator runs interactively inside a
# real tmux pane (so its TUI works — unlike a bare `claude -p`, which only
# prints the final message, or launching from a non-tty subprocess, which has
# no pty for the TUI). It spawns its agents as panes in a SEPARATE window of the
# same session.
#
# Layout (session: $HARNESS_TMUX_SESSION, default "harness"):
#
#   window 0  "orchestrator"
#     ┌────────────────────────┬──────────────────────┐
#     │ pane 0 (left)          │ pane 1 (right)        │
#     │ claude /orchestrator   │ orch-watch.sh -f      │
#     │  — headed TUI          │  — live status / logs │
#     └────────────────────────┴──────────────────────┘
#   window 1  "agents"   ← created on the first spawn; one pane per worker/QA/fixer
#
# The orchestrator can ASSUME this session exists and just spawn into the
# "agents" window (harness.tmux_manager does that lazily; `ensure-session` is a
# harmless no-op here since this script already created it).
#
# Usage (from the project root):
#   bash ~/agent-harness/scripts/harness-up.sh
#   tmux attach -t harness          # watch / interact; detach with Ctrl-b then d
#
# Env:
#   HARNESS_TMUX_SESSION      session name (default: harness)
#   HARNESS_CLAUDE_DANGEROUS  default 1 — skip permission prompts (unattended spawns)
#   HARNESS_ORCH_LOOP         if set (e.g. "30s"), run the orchestrator on a /loop
#                             at that interval instead of a single cycle
set -uo pipefail

if [ ! -d tasks ] || [ ! -f orchestrator-state.yaml ]; then
  echo "Not in a harness project root (missing tasks/ or orchestrator-state.yaml)." >&2
  echo "Run from the project root, after: bash ~/agent-harness/scripts/setup.sh" >&2
  exit 1
fi
if ! command -v tmux >/dev/null 2>&1; then
  echo "tmux not found — install it (e.g. brew install tmux)." >&2
  exit 1
fi

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SESSION="${HARNESS_TMUX_SESSION:-harness}"
PROJECT="$(pwd)"

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "tmux session '$SESSION' already exists — not clobbering it."
  echo "  attach:  tmux attach -t $SESSION"
  echo "  rebuild: tmux kill-session -t $SESSION  &&  re-run this script"
  exit 0
fi

# Build the headed orchestrator command for pane 0.
ORCH_CMD="claude"
[ "${HARNESS_CLAUDE_DANGEROUS:-1}" = "1" ] && ORCH_CMD="$ORCH_CMD --dangerously-skip-permissions"
if [ -n "${HARNESS_ORCH_LOOP:-}" ]; then
  ORCH_CMD="$ORCH_CMD '/loop ${HARNESS_ORCH_LOOP} /orchestrator'"   # continuous
else
  ORCH_CMD="$ORCH_CMD /orchestrator"                                # one cycle, then idle at the TUI
fi

# window 0 "orchestrator", pane 0 = a shell in the project dir (we send-keys the
# orchestrator into it so the shell survives if claude exits → easy re-run).
tmux new-session -d -s "$SESSION" -n orchestrator -c "$PROJECT"
# pane 1 (right): the live status/logs dashboard, auto-refreshing.
tmux split-window -h -t "$SESSION:orchestrator" -c "$PROJECT" "bash '$HERE/orch-watch.sh' -f"
tmux select-layout -t "$SESSION:orchestrator" main-vertical
# label the panes in their borders
tmux set-option -w -t "$SESSION:orchestrator" pane-border-status top 2>/dev/null || true
tmux select-pane -t "$SESSION:orchestrator.0" -T "orchestrator (claude)" 2>/dev/null || true
tmux select-pane -t "$SESSION:orchestrator.1" -T "logs (orch-watch -f)" 2>/dev/null || true
# launch the headed orchestrator in pane 0
tmux send-keys -t "$SESSION:orchestrator.0" "$ORCH_CMD" C-m
tmux select-pane -t "$SESSION:orchestrator.0"

echo "harness session '$SESSION' is up:"
echo "  window 0 'orchestrator' : [pane 0] $ORCH_CMD   |   [pane 1] orch-watch -f"
echo "  window 1 'agents'       : created when the first agent spawns"
echo
echo "  attach:  tmux attach -t $SESSION        (detach: Ctrl-b then d)"
echo "  agents:  Ctrl-b then 1   (switch to the agents window once it appears)"
echo "  stop:    tmux kill-session -t $SESSION"
