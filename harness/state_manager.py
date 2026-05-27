"""Orchestrator state management for the agent-harness.

The orchestrator is an LLM agent that calls these functions either via direct
Python import (for tests) or via the `python -m harness.state ...` CLI (the
main consumer pattern).

Two responsibilities:

  1. Atomic read/write of `orchestrator-state.yaml` (the live state file).
  2. Pure helpers for state mutation (add/remove worker) and dependency-graph
     traversal (which tasks are runnable right now).

Atomicity: writes go to a sibling `.tmp` file then `os.replace()` onto the
real path. `os.replace()` is atomic on POSIX, so the file is never observed
half-written even if the orchestrator crashes mid-write.

The state file path defaults to `./orchestrator-state.yaml` (relative to the
process cwd, which is always the project root per build-guide convention).
Override via the `HARNESS_STATE_FILE` env var or the `--state-file` CLI flag.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable

import yaml


DEFAULT_STATE_FILE = "orchestrator-state.yaml"

EMPTY_STATE: dict[str, Any] = {
    "active_workers": [],
    "max_workers": 3,
    "queue": [],
    "last_action": None,
    "last_action_time": None,
    "cycle_count": 0,
}


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def state_path(override: str | None = None) -> Path:
    """Resolve the state file path. Precedence: arg > env > default."""
    if override:
        return Path(override)
    env = os.environ.get("HARNESS_STATE_FILE")
    if env:
        return Path(env)
    return Path(DEFAULT_STATE_FILE)


# ---------------------------------------------------------------------------
# Core I/O
# ---------------------------------------------------------------------------

def read_state(path: str | os.PathLike | None = None) -> dict[str, Any]:
    """Read state from disk. Returns EMPTY_STATE if the file doesn't exist."""
    p = state_path(str(path) if path else None)
    if not p.exists():
        return dict(EMPTY_STATE)
    with p.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        return dict(EMPTY_STATE)
    if not isinstance(data, dict):
        raise ValueError(f"State file {p} did not contain a YAML mapping")
    return data


def write_state(state: dict[str, Any], path: str | os.PathLike | None = None) -> None:
    """Atomically write state. tmp file in the same dir, then os.replace()."""
    p = state_path(str(path) if path else None)
    p.parent.mkdir(parents=True, exist_ok=True)
    # NamedTemporaryFile in the same dir guarantees os.replace() is atomic
    # (rename across filesystems isn't).
    fd, tmp_path = tempfile.mkstemp(
        prefix=p.name + ".",
        suffix=".tmp",
        dir=str(p.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(state, f, sort_keys=False, default_flow_style=False)
        os.replace(tmp_path, p)
    except Exception:
        # Clean up the tmp file if anything went wrong
        try:
            os.unlink(tmp_path)
        except FileNotFoundError:
            pass
        raise


# ---------------------------------------------------------------------------
# Mutation helpers
# ---------------------------------------------------------------------------

def add_worker(
    *,
    task_id: str,
    worktree: str,
    window: int,
    started: str,
    model: str,
    path: str | os.PathLike | None = None,
) -> dict[str, Any]:
    """Append a worker to active_workers. Idempotent on task_id."""
    state = read_state(path)
    workers = state.setdefault("active_workers", [])
    workers[:] = [w for w in workers if w.get("task_id") != task_id]
    workers.append({
        "task_id": task_id,
        "worktree": worktree,
        "window": window,
        "started": started,
        "model": model,
    })
    write_state(state, path)
    return state


def remove_worker(
    task_id: str,
    path: str | os.PathLike | None = None,
) -> dict[str, Any]:
    """Drop a worker from active_workers by task_id. No-op if absent."""
    state = read_state(path)
    workers = state.setdefault("active_workers", [])
    workers[:] = [w for w in workers if w.get("task_id") != task_id]
    write_state(state, path)
    return state


# ---------------------------------------------------------------------------
# Dependency graph
# ---------------------------------------------------------------------------

def get_runnable_tasks(
    all_tasks: Iterable[dict[str, Any]],
    done_task_ids: Iterable[str],
) -> list[dict[str, Any]]:
    """Return tasks whose status is `backlog` and whose deps are all done.

    A task is runnable iff:
      - status == "backlog"
      - every id in `depends_on` is in `done_task_ids`

    Returns tasks in their original iteration order; the caller decides
    priority/queueing.
    """
    done = set(done_task_ids)
    runnable: list[dict[str, Any]] = []
    for task in all_tasks:
        if task.get("status") != "backlog":
            continue
        deps = task.get("depends_on") or []
        if all(d in done for d in deps):
            runnable.append(task)
    return runnable


# ---------------------------------------------------------------------------
# Task file discovery (helper used by the `runnable` CLI subcommand)
# ---------------------------------------------------------------------------

def load_tasks_in(directory: str | os.PathLike) -> list[dict[str, Any]]:
    """Load every *.yaml file in a directory as a task dict."""
    d = Path(directory)
    if not d.exists():
        return []
    tasks: list[dict[str, Any]] = []
    for p in sorted(d.glob("*.yaml")):
        with p.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if isinstance(data, dict):
            tasks.append(data)
    return tasks


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m harness.state_manager",
        description="orchestrator-state.yaml read/write + runnable-task query",
    )
    parser.add_argument(
        "--state-file",
        default=None,
        help="Path to orchestrator-state.yaml (default: ./orchestrator-state.yaml or $HARNESS_STATE_FILE)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("read", help="Print state as JSON on stdout")

    p_add = sub.add_parser("add-worker", help="Add a worker to active_workers")
    p_add.add_argument("--task-id", required=True)
    p_add.add_argument("--worktree", required=True)
    p_add.add_argument("--window", required=True, type=int)
    p_add.add_argument("--started", required=True, help="ISO-8601 timestamp")
    p_add.add_argument("--model", required=True, choices=["sonnet", "opus", "haiku"])

    p_rm = sub.add_parser("remove-worker", help="Remove a worker by task_id")
    p_rm.add_argument("--task-id", required=True)

    p_run = sub.add_parser(
        "runnable",
        help="Print task IDs whose deps are satisfied (reads tasks/backlog/ and tasks/done/)",
    )
    p_run.add_argument(
        "--tasks-root",
        default="tasks",
        help="Path to tasks/ directory (default: ./tasks)",
    )

    p_init = sub.add_parser(
        "init",
        help="Write an empty state file if one doesn't exist (no-op otherwise)",
    )
    p_init.add_argument("--max-workers", type=int, default=3)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    path = args.state_file

    if args.cmd == "read":
        state = read_state(path)
        json.dump(state, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
        return 0

    if args.cmd == "add-worker":
        add_worker(
            task_id=args.task_id,
            worktree=args.worktree,
            window=args.window,
            started=args.started,
            model=args.model,
            path=path,
        )
        return 0

    if args.cmd == "remove-worker":
        remove_worker(args.task_id, path=path)
        return 0

    if args.cmd == "runnable":
        tasks_root = Path(args.tasks_root)
        backlog = load_tasks_in(tasks_root / "backlog")
        done = load_tasks_in(tasks_root / "done")
        done_ids = [t.get("id") for t in done if t.get("id")]
        runnable = get_runnable_tasks(backlog, done_ids)
        for task in runnable:
            sys.stdout.write(f"{task.get('id')}\n")
        return 0

    if args.cmd == "init":
        p = state_path(path)
        if p.exists():
            sys.stderr.write(f"State file already exists at {p}, leaving alone.\n")
            return 0
        state = dict(EMPTY_STATE)
        state["max_workers"] = args.max_workers
        write_state(state, path)
        sys.stderr.write(f"Wrote empty state to {p}\n")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
