#!/bin/sh
# Internal helper used by tmux_manager.spawn_worker.
# Runs as the command inside the agent's pane in the shared agents window
# (started via `respawn-pane -k` over the idle placeholder).
#
# Args:
#   $1 = worktree path (cwd for the worker)
#   $2 = claude --session-id value (UUID)
#   $3 = path to a file containing the initial prompt
#   $4 = (optional) claude --model (e.g. opus for the QA-fail fixer); empty = default
#
# `exec claude ...` replaces this shell, so when claude exits the pane
# closes (no leftover sh prompt). The orchestrator can still kill-pane
# explicitly while claude is running.

set -e

worktree="$1"
session_id="$2"
prompt_file="$3"
model="${4:-}"

cd "$worktree"
# Workers run unattended in a detached tmux pane — there is no human to
# approve tool use, so permission prompts would hang the worker forever.
# Default to skipping them; set HARNESS_CLAUDE_DANGEROUS=0 in the tmux server
# environment to opt out.
perms="--dangerously-skip-permissions"
[ "${HARNESS_CLAUDE_DANGEROUS:-1}" = "0" ] && perms=""
model_flag=""
[ -n "$model" ] && model_flag="--model $model"
# Quoting note: $(cat ...) inside double-quotes is NOT re-expanded by bash,
# so prompt contents (incl. $, backticks, quotes) are passed verbatim.
exec claude $perms $model_flag --session-id "$session_id" "$(cat "$prompt_file")"
