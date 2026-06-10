#!/usr/bin/env bash
# harness-drive.sh — TEMPORARY loop driver: fire orchestrator cycles until every
# task in the project reaches a terminal state. This is the stopgap for the
# CRITICAL issue in todo.md (single-shot orchestrator + agents that don't
# self-exit): each cycle reaps the finished agent and spawns the next, so firing
# cycles in a paced loop carries the pipeline to completion — what `/loop` does.
#
# Pacing: after each cycle, if an agent is actively running, wait until it emits
# a result (a task `status:` or qa-report status line changes) or a timeout;
# if nothing is running but work remains, fire again immediately.
#
# Run from the project root:
#   bash ~/agent-harness/scripts/harness-drive.sh
#
# Env:
#   HARNESS_DRIVE_MAX_CYCLES  hard cap on cycles      (default 40)
#   HARNESS_DRIVE_WAIT        max secs to wait per agent turn (default 900)
#   HARNESS_DRIVE_POLL        poll interval secs      (default 15)
set -uo pipefail

if [ ! -f orchestrator-state.yaml ]; then
  echo "Not in a harness project root." >&2; exit 1
fi
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAX_CYCLES="${HARNESS_DRIVE_MAX_CYCLES:-40}"
WAIT_SECS="${HARNESS_DRIVE_WAIT:-900}"
POLL="${HARNESS_DRIVE_POLL:-15}"
LOGDIR=".harness-logs"; mkdir -p "$LOGDIR"
DLOG="$LOGDIR/drive.log"

say(){ echo "[$(date '+%H:%M:%S')] $*" | tee -a "$DLOG"; }

# active worker count — read straight from the state file (no conda needed).
# NB: `grep -c` prints "0" AND exits 1 on no match, so capture stdout and never
# chain `|| echo 0` (that would emit "0\n0" and break the integer test below).
active_count(){ local c; c=$(grep -cE '^[[:space:]]*-[[:space:]]*task_id:' orchestrator-state.yaml 2>/dev/null); echo "${c:-0}"; }
# tasks not yet archived to done/ (i.e. still moving through the pipeline)
pending_count(){ find tasks -type f \( -path '*/backlog/*.yaml' -o -path '*/in-progress/*.yaml' -o -path '*/review/*.yaml' \) 2>/dev/null | wc -l | tr -d ' '; }
# signature of agent OUTPUTS: every task status line + every qa-report status line
work_sig(){
  { find tasks -type f -name '*.yaml' -exec grep -h '^status:' {} \; 2>/dev/null
    grep -rh -E '^\*\*status:\*\*' docs/active-features 2>/dev/null
  } | sort | cksum | awk '{print $1}'
}

say "=== harness-drive start (max ${MAX_CYCLES} cycles, wait ${WAIT_SECS}s, poll ${POLL}s) ==="
cycle=0
while [ "$cycle" -lt "$MAX_CYCLES" ]; do
  cycle=$((cycle+1))
  say "--- cycle $cycle: firing orchestrator ---"
  bash "$HERE/orch-run.sh" >/dev/null 2>&1 || say "  (orch-run exited non-zero — continuing)"

  a="$(active_count)"; p="$(pending_count)"
  say "  after cycle $cycle: active_workers=$a  pending_tasks=$p"

  if [ "$a" -eq 0 ] && [ "$p" -eq 0 ]; then
    say "=== ALL TASKS TERMINAL — pipeline complete after $cycle cycles ==="
    exit 0
  fi

  if [ "$a" -gt 0 ]; then
    base="$(work_sig)"; waited=0
    say "  agent(s) running — waiting for a result (sig=$base)…"
    while [ "$waited" -lt "$WAIT_SECS" ]; do
      sleep "$POLL"; waited=$((waited+POLL))
      if [ "$(work_sig)" != "$base" ]; then
        say "  agent produced a result after ${waited}s — advancing"
        break
      fi
    done
    [ "$waited" -ge "$WAIT_SECS" ] && say "  timed out after ${waited}s waiting for an agent — firing anyway"
  else
    say "  no active agents but $p task(s) pending — firing next cycle promptly"
    sleep 5
  fi
done

say "=== hit MAX_CYCLES=$MAX_CYCLES without full completion — stopping (inspect with orch-watch.sh) ==="
exit 1
