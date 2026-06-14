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
#   HARNESS_ORCH_LOOP         loop interval for the orchestrator (default: 60s).
#                             The orchestrator runs on `/loop <interval> /orchestrator`
#                             so the pipeline self-advances out of the box. Override
#                             with e.g. HARNESS_ORCH_LOOP=30s; set to "off"/"none"
#                             for a single cycle that then idles at the TUI.
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

# ---------------------------------------------------------------------------
# History capture — mint a run id so every cycle/agent/event in THIS launch is
# recorded under ~/.agent-harness-history/<project>/runs/<run-id>/ (browse it
# later with scripts/harness-history.sh). A run == one harness-up → teardown.
# ---------------------------------------------------------------------------
HARNESS_ROOT="$(cd "$HERE/.." && pwd)"
# Python runner for the harness package. Prefer a direct python3 that can import
# pyyaml: it execs python directly, so the long-running watcher gets the SIGHUP
# tmux sends on teardown and can stamp a clean run_end. Fall back to the conda
# env the other wrappers use.
if PYTHONPATH="$HARNESS_ROOT" python3 -c 'import yaml' >/dev/null 2>&1; then
  PYRUN="env PYTHONPATH=$HARNESS_ROOT python3"
elif command -v conda >/dev/null 2>&1; then
  PYRUN="conda run --no-capture-output -n ${HARNESS_CONDA_ENV:-harness} python"
else
  PYRUN=""
fi

RUN_ID=""; ORCH_SID=""; HISTORY_OK=0
if [ -n "$PYRUN" ]; then
  RUN_ID="$($PYRUN -c 'import datetime,uuid;print(datetime.datetime.now().strftime("%Y%m%d-%H%M%S")+"-"+uuid.uuid4().hex[:6])' 2>/dev/null)"
  ORCH_SID="$($PYRUN -c 'import uuid;print(uuid.uuid4())' 2>/dev/null)"
  if [ -n "$RUN_ID" ] && [ -n "$ORCH_SID" ] \
     && $PYRUN -m harness.history mint-run --project "$PROJECT" --run-id "$RUN_ID" \
          --orch-sid "$ORCH_SID" --interval "${HARNESS_ORCH_LOOP:-60s}" >/dev/null 2>&1; then
    HISTORY_OK=1
  else
    echo "! history capture unavailable (mint-run failed) — launching without it." >&2
  fi
else
  echo "! no python3+pyyaml or conda found — history capture disabled." >&2
fi

# Build the headed orchestrator command for pane 0.
ORCH_CMD="claude"
[ "${HARNESS_CLAUDE_DANGEROUS:-1}" = "1" ] && ORCH_CMD="$ORCH_CMD --dangerously-skip-permissions"
# Pin the orchestrator's session id so the history watcher can find its
# transcript and record the cycle's tool calls (task moves, spawns, merges).
[ -n "$ORCH_SID" ] && ORCH_CMD="$ORCH_CMD --session-id $ORCH_SID"
# Default to a self-driving loop so the pipeline advances out of the box — a
# single cycle would look hung once the first worker finishes (see todo.md).
HARNESS_ORCH_LOOP="${HARNESS_ORCH_LOOP:-60s}"
case "$HARNESS_ORCH_LOOP" in
  off|none|"") ORCH_CMD="$ORCH_CMD /orchestrator" ;;                              # one cycle, then idle at the TUI
  *)           ORCH_CMD="$ORCH_CMD '/loop ${HARNESS_ORCH_LOOP} /orchestrator'" ;; # continuous, every $HARNESS_ORCH_LOOP
esac

# window 0 "orchestrator", pane 0 = a shell in the project dir (we send-keys the
# orchestrator into it so the shell survives if claude exits → easy re-run).
tmux new-session -d -s "$SESSION" -n orchestrator -c "$PROJECT"
# pane 1 (right): the live status/logs dashboard, auto-refreshing.
tmux split-window -h -t "$SESSION:orchestrator" -c "$PROJECT" "bash '$HERE/orch-watch.sh' -f"
# pane 2 (right, below the dashboard): the mechanical history recorder for this
# run. It's a pane in the session, so `tmux kill-session` SIGHUPs it and it
# stamps a clean run_end. Captured pane id, since the index renumbers on layout.
WPANE=""
if [ "$HISTORY_OK" = "1" ]; then
  WATCH_ENV="HARNESS_RUN_ID=$RUN_ID HARNESS_ORCH_SID=$ORCH_SID"
  [ -n "${HARNESS_HISTORY_DIR:-}" ] && WATCH_ENV="$WATCH_ENV HARNESS_HISTORY_DIR=$HARNESS_HISTORY_DIR"
  WPANE="$(tmux split-window -v -P -F '#{pane_id}' -t "$SESSION:orchestrator.1" -c "$PROJECT" \
    "$WATCH_ENV $PYRUN -m harness.history_watcher --project '$PROJECT'")"
fi
tmux select-layout -t "$SESSION:orchestrator" main-vertical
# label the panes in their borders
tmux set-option -w -t "$SESSION:orchestrator" pane-border-status top 2>/dev/null || true
tmux select-pane -t "$SESSION:orchestrator.0" -T "orchestrator (claude)" 2>/dev/null || true
tmux select-pane -t "$SESSION:orchestrator.1" -T "logs (orch-watch -f)" 2>/dev/null || true
[ -n "$WPANE" ] && tmux select-pane -t "$WPANE" -T "history (recording run $RUN_ID)" 2>/dev/null || true
# launch the headed orchestrator in pane 0
tmux send-keys -t "$SESSION:orchestrator.0" "$ORCH_CMD" C-m
tmux select-pane -t "$SESSION:orchestrator.0"

echo "harness session '$SESSION' is up:"
echo "  window 0 'orchestrator' : [pane 0] $ORCH_CMD   |   [pane 1] orch-watch -f"
[ "$HISTORY_OK" = "1" ] && echo "                            [pane 2] history recorder (run $RUN_ID)"
echo "  window 1 'agents'       : created when the first agent spawns"
echo
echo "  attach:  tmux attach -t $SESSION        (detach: Ctrl-b then d)"
echo "  agents:  Ctrl-b then 1   (switch to the agents window once it appears)"
echo "  stop:    tmux kill-session -t $SESSION"
if [ "$HISTORY_OK" = "1" ]; then
  echo
  echo "  recording → ~/.agent-harness-history/  (this run: $RUN_ID)"
  echo "  browse:    bash $HERE/harness-history.sh"
fi
