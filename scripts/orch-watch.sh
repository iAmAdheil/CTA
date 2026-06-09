#!/usr/bin/env bash
# orch-watch.sh — dashboard for harness activity in the current project.
#
# Run from the project root:
#   bash ~/agent-harness/scripts/orch-watch.sh          # one snapshot
#   bash ~/agent-harness/scripts/orch-watch.sh -f        # follow (refresh every 3s)
#   bash ~/agent-harness/scripts/orch-watch.sh -f 5      # follow, refresh every 5s
#
# Surfaces, in one view:
#   - whether an orchestrator cycle is running
#   - the live tmux 'agents' session (worker/QA panes) + the attach command
#   - git branch + recent commits
#   - task files grouped by state, and the kanban boards
#   - control-plane state (orchestrator-state.yaml)
#   - tail of the latest streaming activity log (from orch-run.sh)
set -uo pipefail

SESSION="${HARNESS_TMUX_SESSION:-harness}"
LOGDIR=".harness-logs"
TAIL_N="${HARNESS_WATCH_TAIL:-18}"

snapshot() {
  command -v clear >/dev/null 2>&1 && clear || true
  echo "═══════ HARNESS WATCH · $(date '+%H:%M:%S') · ${PWD##*/} ═══════"
  echo
  echo "▌ orchestrator cycle"
  if pgrep -fl "p /orchestrator" 2>/dev/null | grep -v pgrep >/dev/null; then
    pgrep -fl "p /orchestrator" | grep -v pgrep | sed 's/^/  ● /'
  else
    echo "  ○ not running (one cycle = one invocation; re-run orch-run.sh to advance)"
  fi
  echo
  echo "▌ tmux '$SESSION' — live agents (workers / QA / fixers)"
  if tmux has-session -t "$SESSION" 2>/dev/null; then
    tmux list-panes -s -t "$SESSION" \
      -F "  #{window_name}  pane=#{pane_id}  [#{?pane_dead,DEAD,live}]  #{pane_current_command}  #{pane_current_path}" 2>/dev/null
    echo "  → attach:  tmux attach -t $SESSION    (detach: Ctrl-b then d)"
  else
    echo "  (no session yet — created when the first agent pane spawns)"
  fi
  echo
  echo "▌ git"
  echo "  branch: $(git branch --show-current 2>/dev/null || echo '?')"
  git log --oneline -5 2>/dev/null | sed 's/^/  /'
  echo
  echo "▌ tasks by state"
  if find tasks -type f -name '*.md' 2>/dev/null | grep -q .; then
    find tasks -type f -name '*.md' 2>/dev/null \
      | sed -E 's#^tasks/[^/]+/##' | awk -F/ 'NF>1{print $1}' \
      | sort | uniq -c | sed 's/^/    /'
    echo "  files:"
    find tasks -type f -name '*.md' 2>/dev/null | sed 's#^#    #'
  else
    echo "  (no task files yet — breakdown hasn't written tasks)"
  fi
  [ -f docs/_kanban/tasks-board.md ] && echo "  board: docs/_kanban/tasks-board.md  ·  specs: docs/_kanban/specs-board.md"
  echo
  echo "▌ control-plane (orchestrator-state.yaml)"
  sed 's/^/  /' orchestrator-state.yaml 2>/dev/null || echo "  (missing)"
  echo
  local latest="$LOGDIR/orch-activity-latest.log"
  echo "▌ activity log — $latest (last $TAIL_N lines)"
  if [ -e "$latest" ]; then
    tail -n "$TAIL_N" "$latest" 2>/dev/null | sed 's/^/  /'
  else
    echo "  (none yet — launch cycles via orch-run.sh to get streaming activity)"
  fi
}

if [ ! -f orchestrator-state.yaml ]; then
  echo "Not in a harness project root (no orchestrator-state.yaml here)." >&2
  exit 1
fi

case "${1:-}" in
  -f|--follow)
    interval="${2:-3}"
    while true; do snapshot; echo; echo "(following — Ctrl-c to stop, refresh ${interval}s)"; sleep "$interval"; done
    ;;
  *)
    snapshot
    ;;
esac
