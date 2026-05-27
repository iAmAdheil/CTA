#!/bin/sh
# Internal helper used by tmux_manager.spawn_worker.
# Runs as the shell command inside a freshly-created tmux window.
#
# Args:
#   $1 = worktree path (cwd for the worker)
#   $2 = claude --session-id value (UUID)
#   $3 = path to a file containing the initial prompt
#
# `exec claude ...` replaces this shell, so when claude exits the window
# closes (no leftover sh prompt). The orchestrator can still kill-window
# explicitly while claude is running.

set -e

worktree="$1"
session_id="$2"
prompt_file="$3"

cd "$worktree"
# Quoting note: $(cat ...) inside double-quotes is NOT re-expanded by bash,
# so prompt contents (incl. $, backticks, quotes) are passed verbatim.
exec claude --session-id "$session_id" "$(cat "$prompt_file")"
