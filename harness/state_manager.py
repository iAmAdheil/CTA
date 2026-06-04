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
    pane: str,
    started: str,
    model: str,
    role: str = "worker",
    path: str | os.PathLike | None = None,
) -> dict[str, Any]:
    """Append a tracked agent to active_workers. Idempotent on task_id.

    `role` distinguishes the agent kinds that act on a task across its life:
    "worker" (fresh implementation), "qa" (async behavioral QA), and "fixer"
    (Opus fix-mode). The orchestrator consumes a QA verdict exactly once — at the
    moment it tears down the role:qa agent that produced it — so role is what
    tells a later cycle "there's an unconsumed verdict I still owe action on".
    Idempotent on task_id means only ONE tracked agent per task at a time, which
    holds because the loop is serial per task (worker -> QA -> fixer -> re-QA).
    """
    state = read_state(path)
    workers = state.setdefault("active_workers", [])
    workers[:] = [w for w in workers if w.get("task_id") != task_id]
    workers.append({
        "task_id": task_id,
        "worktree": worktree,
        "pane": pane,
        "started": started,
        "model": model,
        "role": role,
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

# Lower number = higher priority. Unknown/missing priority sorts last.
_PRIORITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _task_sort_key(task: dict[str, Any]) -> tuple[int, int, str]:
    """Sort key: priority (critical first), then numeric id, then raw id.

    The numeric-id tie-break keeps TASK-9 ahead of TASK-10 (string sort would
    reverse them) so equal-priority tasks run in stable, predictable order.
    """
    rank = _PRIORITY_RANK.get(task.get("priority"), len(_PRIORITY_RANK))
    raw_id = task.get("id") or ""
    try:
        num = int(raw_id.rsplit("-", 1)[-1])
    except (ValueError, IndexError):
        num = 1 << 30
    return (rank, num, raw_id)


def get_runnable_tasks(
    all_tasks: Iterable[dict[str, Any]],
    done_task_ids: Iterable[str],
) -> list[dict[str, Any]]:
    """Return runnable tasks ordered most-critical-first.

    A task is runnable iff:
      - status == "backlog"
      - every id in `depends_on` is in `done_task_ids`

    The result is sorted by `priority` (critical → high → medium → low, with
    unknown/missing priority last), tie-broken by numeric task id. The caller
    can take the first N and trust it's getting the highest-priority work.
    """
    done = set(done_task_ids)
    runnable: list[dict[str, Any]] = []
    for task in all_tasks:
        if task.get("status") != "backlog":
            continue
        deps = task.get("depends_on") or []
        if all(d in done for d in deps):
            runnable.append(task)
    runnable.sort(key=_task_sort_key)
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
# Spec discovery + serial-per-spec gate
# ---------------------------------------------------------------------------

def _read_spec_frontmatter(path: Path) -> dict[str, Any]:
    """Parse the leading `--- … ---` YAML frontmatter block of a spec file.

    Returns {} if the file has no frontmatter or it isn't a mapping.
    """
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    # Split on the frontmatter fences: text is "---\n<yaml>\n---\n<body>".
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    data = yaml.safe_load(parts[1])
    return data if isinstance(data, dict) else {}


# Statuses that mean "the harness still has automated work to do on this task".
# While ANY task is in one of these, the serial gate holds the next spec. The QA
# retry loop (issue #4) is why this is status-based, not directory-based:
# pr-opened/pr-updated/qa-failed tasks live in tasks/review/ but are NOT drained —
# they mean an async QA agent or an Opus fixer is (or will be) running. The next
# spec releases only once every task reaches a "now handled by the human" state:
# qa-passed, done, blocked, blocked-escalated.
ACTIVE_STATUSES = frozenset({
    "backlog", "in-progress", "pr-opened", "pr-updated", "qa-failed",
})


def get_next_spec(
    specs_dir: str | os.PathLike,
    tasks_root: str | os.PathLike,
) -> str | None:
    """Return the path of the next approved spec to break down, or None.

    Serial-per-spec gate: only one spec is worked at a time. A spec counts as
    "still being worked" while any task has a status in `ACTIVE_STATUSES`
    (regardless of which tasks/ subdir holds it — QA-loop tasks sit in review/
    but are still active). So:

      - If ANY task (in backlog/, in-progress/, or review/) is active, return
        None (hold — the current spec hasn't cleared the QA loop yet).
      - Otherwise pick the highest-priority spec with `status: approved`
        (critical → high → medium → low, tie-broken by filename) and return
        its path. None if there are no approved specs.
    """
    tasks_root = Path(tasks_root)
    all_tasks = (
        load_tasks_in(tasks_root / "backlog")
        + load_tasks_in(tasks_root / "in-progress")
        + load_tasks_in(tasks_root / "review")
    )
    if any(t.get("status") in ACTIVE_STATUSES for t in all_tasks):
        return None

    specs_dir = Path(specs_dir)
    if not specs_dir.exists():
        return None

    candidates: list[tuple[int, str, Path]] = []
    for p in sorted(specs_dir.glob("*.md")):
        if p.name.startswith("_"):
            continue
        fm = _read_spec_frontmatter(p)
        if fm.get("status") != "approved":
            continue
        rank = _PRIORITY_RANK.get(fm.get("priority"), len(_PRIORITY_RANK))
        candidates.append((rank, p.name, p))

    if not candidates:
        return None
    candidates.sort(key=lambda c: (c[0], c[1]))
    return str(candidates[0][2])


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
    p_add.add_argument("--pane", required=True, help="tmux pane id (e.g. '%7') returned by provision-worker")
    p_add.add_argument("--started", required=True, help="ISO-8601 timestamp")
    p_add.add_argument("--model", required=True, choices=["sonnet", "opus", "haiku"])
    p_add.add_argument("--role", default="worker", choices=["worker", "qa", "fixer"], help="Tracked-agent kind (default: worker)")

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

    p_next = sub.add_parser(
        "next-spec",
        help="Print the path of the next approved spec to break down, or nothing "
        "if a spec is still being worked (serial-per-spec gate)",
    )
    p_next.add_argument(
        "--specs-dir",
        default="docs/specs",
        help="Path to the specs directory (default: ./docs/specs)",
    )
    p_next.add_argument(
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
            pane=args.pane,
            started=args.started,
            model=args.model,
            role=args.role,
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

    if args.cmd == "next-spec":
        spec = get_next_spec(args.specs_dir, args.tasks_root)
        if spec:
            sys.stdout.write(f"{spec}\n")
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
