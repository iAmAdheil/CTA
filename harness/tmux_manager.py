"""Tmux + git-worktree wrappers for the agent-harness.

The LLM orchestrator calls these via `python -m harness.tmux ...`. They
encapsulate the fiddly bits — tmux session/window lifecycle, git worktree
creation/cleanup, quoting of long/multiline prompts when nudging running
workers — so the orchestrator's prompt stays focused on policy not plumbing.

## Conventions

- All worker windows live in one tmux session, named "harness" by default
  (override with `HARNESS_TMUX_SESSION`). Session 0 by convention holds the
  orchestrator; worker windows are 1+; the monitor (Stage 8) sits at 7.
- Worktrees are created as siblings of the project root by default:
  `../wt-<task-id>`. Override per-call.
- Worker branches are named `task/<task-id>`.
- Each worker session has a pre-allocated UUID so the orchestrator can
  `claude --resume <uuid>` later (e.g. for QA-fail fix mode in Stage 6).

## Why a helper shell script

Passing a multi-KB prompt as a positional arg to `claude` through tmux's
`new-window <command-string>` would require fragile shell quoting. Instead
we write the prompt to a temp file and invoke `harness/_spawn_worker.sh`,
which reads the file with `"$(cat ...)"` — bash does not re-expand the
command-substitution result inside double-quotes, so any chars in the
prompt are passed verbatim.

## Why paste-buffer for nudges

For mid-session nudges to a running worker (Stage 7 blocker decisions),
multi-line text and special chars are common. `tmux send-keys` mishandles
newlines and shell-special chars; `load-buffer` + `paste-buffer` preserves
both and triggers bracketed-paste mode which the claude TTY handles
correctly.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any


DEFAULT_SESSION = "harness"
DEFAULT_BASE_BRANCH = "main"
SPAWN_HELPER = Path(__file__).parent / "_spawn_worker.sh"
CLAUDE_CONFIG = Path.home() / ".claude.json"
PROMPT_DIR_ENV = "HARNESS_PROMPT_DIR"
# Long-text threshold above which we use paste-buffer instead of send-keys
NUDGE_LITERAL_MAX = 2000
# Idle command a provisioned-but-not-yet-launched worker window runs. It holds
# the window open (and its index stable) until `launch_worker` respawns it with
# the real claude command. INT_MAX seconds (~68y); BSD/GNU sleep both accept it.
PLACEHOLDER_CMD = "sleep 2147483647"


# ---------------------------------------------------------------------------
# Low-level subprocess wrappers
# ---------------------------------------------------------------------------

def _run(cmd: list[str], *, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        check=check,
        text=True,
        capture_output=capture,
    )


def _session_name() -> str:
    return os.environ.get("HARNESS_TMUX_SESSION", DEFAULT_SESSION)


def _prompt_dir() -> Path:
    """Directory for prompt files handed to the spawn helper.

    Persisted (not auto-deleted) so the LLM orch can inspect them after a
    spawn. The orchestrator can clean them up after the window is gone.
    Defaults to /tmp/harness-prompts/.
    """
    d = Path(os.environ.get(PROMPT_DIR_ENV, "/tmp/harness-prompts"))
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Session management
# ---------------------------------------------------------------------------

def ensure_session(session: str | None = None) -> str:
    """Create the harness tmux session if it doesn't exist. Returns the name."""
    name = session or _session_name()
    # capture=True so tmux's "can't find session: <name>" doesn't leak to stderr
    # and pollute callers that read our JSON from a merged stdout+stderr stream.
    if _run(["tmux", "has-session", "-t", name], check=False, capture=True).returncode != 0:
        # window 0 is a placeholder for the orchestrator itself
        _run(["tmux", "new-session", "-d", "-s", name, "-n", "orchestrator"])
    return name


def kill_session(session: str | None = None) -> None:
    name = session or _session_name()
    _run(["tmux", "kill-session", "-t", name], check=False)


# ---------------------------------------------------------------------------
# Worktree management
# ---------------------------------------------------------------------------

def _resolve_worktree_path(task_id: str, worktree: str | None) -> Path:
    if worktree:
        return Path(worktree).expanduser().resolve()
    # Sibling of cwd, named after the task
    return (Path.cwd().parent / f"wt-{task_id}").resolve()


def resolve_base_branch(
    base: str | None = None,
    repo_root: str | os.PathLike | None = None,
) -> str:
    """Resolve the branch a worker's worktree should be cut from.

    Precedence: explicit `base` arg > `HARNESS_BASE_BRANCH` env > the repo's
    currently checked-out branch (the orchestrator runs on the integration
    branch, so that's the right base) > `DEFAULT_BASE_BRANCH`.

    Hardcoding "main" broke repos whose default branch is "master" (or anything
    else), since the orchestrator never passed `--base-branch`. Detecting the
    current branch makes worktree creation work regardless of branch name.
    """
    if base:
        return base
    env = os.environ.get("HARNESS_BASE_BRANCH")
    if env:
        return env
    cwd = str(repo_root) if repo_root else None
    res = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=cwd, text=True, capture_output=True,
    )
    name = res.stdout.strip()
    if res.returncode == 0 and name and name != "HEAD":  # "HEAD" == detached
        return name
    return DEFAULT_BASE_BRANCH


def create_worktree(
    branch: str,
    path: str | os.PathLike,
    *,
    base_branch: str = DEFAULT_BASE_BRANCH,
    repo_root: str | os.PathLike | None = None,
) -> Path:
    """`git worktree add -b <branch> <path> <base_branch>`. Returns the path.

    Idempotent on `path`: if a worktree already exists there, it's returned
    as-is without error.
    """
    p = Path(path).expanduser().resolve()
    cwd = str(repo_root) if repo_root else None
    # Check if this path is already a registered worktree
    out = _run(["git", "worktree", "list", "--porcelain"], capture=True, check=False)
    if out.returncode == 0 and f"worktree {p}" in out.stdout:
        return p
    p.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["git", "worktree", "add", "-b", branch, str(p), base_branch]
    res = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)
    if res.returncode != 0:
        # If the branch already exists, retry without -b
        if "already exists" in (res.stderr or ""):
            cmd2 = ["git", "worktree", "add", str(p), branch]
            subprocess.run(cmd2, cwd=cwd, text=True, check=True)
        else:
            raise RuntimeError(
                f"git worktree add failed: {res.stderr.strip()}"
            )
    return p


def pretrust_path(path: str | os.PathLike) -> bool:
    """Mark `path` as trusted in ~/.claude.json.

    A freshly-created worktree is a brand-new directory, so on first launch
    `claude` shows the "Do you trust the files in this folder?" dialog.
    `--dangerously-skip-permissions` does NOT dismiss it. An unattended worker
    has no human to accept it, so it would hang forever. We pre-seed the trust
    flag the CLI checks (`projects[<abs-path>].hasTrustDialogAccepted`).

    Returns True if a write happened, False if already trusted or skipped.
    Best-effort: never raises on a missing/locked/malformed config (the worker
    just falls back to showing the dialog). Atomic via mkstemp + os.replace.
    """
    p = str(Path(path).expanduser().resolve())
    try:
        if CLAUDE_CONFIG.exists():
            with CLAUDE_CONFIG.open(encoding="utf-8") as f:
                cfg = json.load(f)
        else:
            cfg = {}
    except (json.JSONDecodeError, OSError):
        return False
    if not isinstance(cfg, dict):
        return False
    projects = cfg.setdefault("projects", {})
    entry = projects.setdefault(p, {})
    if entry.get("hasTrustDialogAccepted") is True:
        return False
    entry["hasTrustDialogAccepted"] = True
    entry.setdefault("projectOnboardingSeenCount", 1)
    try:
        fd, tmp = tempfile.mkstemp(
            prefix=".claude.json.", suffix=".tmp", dir=str(CLAUDE_CONFIG.parent)
        )
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        os.replace(tmp, CLAUDE_CONFIG)
    except OSError:
        try:
            os.unlink(tmp)
        except (OSError, NameError):
            pass
        return False
    return True


def remove_worktree(path: str | os.PathLike, *, force: bool = False) -> None:
    """`git worktree remove [--force] <path>`. No-op if path doesn't exist."""
    p = Path(path).expanduser()
    if not p.exists():
        return
    cmd = ["git", "worktree", "remove"]
    if force:
        cmd.append("--force")
    cmd.append(str(p.resolve()))
    res = subprocess.run(cmd, text=True, capture_output=True)
    if res.returncode != 0 and not force:
        # Retry with --force on dirty worktrees
        remove_worktree(p, force=True)


# ---------------------------------------------------------------------------
# Window management
# ---------------------------------------------------------------------------

def _list_window_indices(session: str) -> list[int]:
    res = _run(
        ["tmux", "list-windows", "-t", session, "-F", "#{window_index}"],
        capture=True,
        check=False,
    )
    if res.returncode != 0:
        return []
    return [int(x) for x in res.stdout.split() if x.strip().isdigit()]


def kill_window(window_id: str) -> None:
    """`tmux kill-window -t <window_id>`. No-op if the window is gone.

    `window_id` can be `harness:1` or just `1` (which is interpreted under
    the default session). Accepts both forms.
    """
    target = window_id if ":" in window_id else f"{_session_name()}:{window_id}"
    _run(["tmux", "kill-window", "-t", target], check=False)


# ---------------------------------------------------------------------------
# Worker spawn
# ---------------------------------------------------------------------------

def provision_worker(
    *,
    task_id: str,
    prompt: str,
    worktree: str | None = None,
    base_branch: str | None = None,
    repo_root: str | os.PathLike | None = None,
    session: str | None = None,
    window: int | None = None,
    window_name: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Phase 1 of spawning: set everything up *except* starting the worker.

    Creates the worktree/branch, pre-trusts it, writes the prompt file, and
    reserves an **idle placeholder** tmux window (running `PLACEHOLDER_CMD`, not
    claude). The worker is NOT running yet.

    This exists so the orchestrator can do all of its bookkeeping — move the task
    file to `in-progress/`, set `status: in-progress` + worktree/window/started/
    assigned_to, record the worker in `orchestrator-state.yaml` — *before* the
    worker process exists. A worker that is launched into a fully-prepared world
    can never observe a half-set-up task file nor race the orchestrator's writes
    to it. Call `launch_worker` with the returned fields to actually start it.

    Returns the same dict as `spawn_worker`, plus `"launched": False`.
    """
    sess = ensure_session(session)
    sid = session_id or str(uuid.uuid4())
    wt_path = _resolve_worktree_path(task_id, worktree)
    branch = f"task/{task_id}"
    base = resolve_base_branch(base_branch, repo_root)

    create_worktree(branch, wt_path, base_branch=base, repo_root=repo_root)

    # Pre-trust the worktree so the worker doesn't hang on the folder-trust
    # dialog (no human to accept it in an unattended run).
    pretrust_path(wt_path)

    # Persist the prompt so the spawn helper can read it without quoting.
    prompt_file = _prompt_dir() / f"{task_id}.prompt.txt"
    prompt_file.write_text(prompt, encoding="utf-8")

    name = window_name or f"worker-{task_id}"
    target = f"{sess}:{window}" if window is not None else sess

    # Reserve the window with an idle hold command. `launch_worker` replaces it.
    cmd = [
        "tmux", "new-window",
        "-t", target,
        "-n", name,
        "-P", "-F", "#{window_index}",
        PLACEHOLDER_CMD,
    ]
    res = _run(cmd, capture=True)
    assigned = int(res.stdout.strip())

    return {
        "task_id": task_id,
        "session_id": sid,
        "session": sess,
        "window": assigned,
        "window_target": f"{sess}:{assigned}",
        "worktree": str(wt_path),
        "branch": branch,
        "prompt_file": str(prompt_file),
        "launched": False,
    }


def launch_worker(
    *,
    worktree: str | os.PathLike,
    session_id: str,
    prompt_file: str | os.PathLike,
    window: int | str,
    session: str | None = None,
) -> dict[str, Any]:
    """Phase 2 of spawning: start the worker in an already-provisioned window.

    `tmux respawn-window -k` kills the placeholder hold command and runs the
    spawn helper (which `exec`s `claude --session-id <uuid> "$(cat <prompt>)"`)
    in the same window index. From here the worker is live.

    `window` may be an int index or a full `session:index` target.
    """
    sess = session or _session_name()
    target = window if (isinstance(window, str) and ":" in window) else f"{sess}:{window}"
    cmd = [
        "tmux", "respawn-window", "-k",
        "-t", str(target),
        f"{SPAWN_HELPER} {worktree!s} {session_id} {prompt_file!s}",
    ]
    _run(cmd)
    return {"session": sess, "window_target": str(target), "launched": True}


def spawn_worker(
    *,
    task_id: str,
    prompt: str,
    worktree: str | None = None,
    base_branch: str | None = None,
    repo_root: str | os.PathLike | None = None,
    session: str | None = None,
    window: int | None = None,
    window_name: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    """Provision + launch a worker in one call (the worker starts immediately).

    Convenience wrapper around `provision_worker` + `launch_worker`. Prefer the
    two-phase form from the orchestrator so bookkeeping happens before launch
    (see `provision_worker`); use this for manual/testing one-shots where there
    is no concurrent writer to the task file.

    Returns:
        {
          "task_id": str, "session_id": str (UUID), "session": str,
          "window": int, "window_target": str, "worktree": str (abs),
          "branch": str, "prompt_file": str, "launched": True,
        }
    """
    info = provision_worker(
        task_id=task_id,
        prompt=prompt,
        worktree=worktree,
        base_branch=base_branch,
        repo_root=repo_root,
        session=session,
        window=window,
        window_name=window_name,
        session_id=session_id,
    )
    launch_worker(
        worktree=info["worktree"],
        session_id=info["session_id"],
        prompt_file=info["prompt_file"],
        window=info["window"],
        session=info["session"],
    )
    info["launched"] = True
    return info


# ---------------------------------------------------------------------------
# Mid-session nudges
# ---------------------------------------------------------------------------

def send_nudge(window_id: str, text: str, *, submit: bool = True) -> None:
    """Deliver `text` to the given window's active pane.

    Long or multiline text goes through `tmux load-buffer` + `paste-buffer`
    (bracketed paste, handles any chars). Short single-line text goes via
    `send-keys -l` (literal). Always followed by Enter unless submit=False.
    """
    target = window_id if ":" in window_id else f"{_session_name()}:{window_id}"

    if "\n" in text or len(text) > NUDGE_LITERAL_MAX:
        # paste-buffer path
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", delete=False, suffix=".nudge"
        ) as f:
            f.write(text)
            tmp_path = f.name
        try:
            buf = f"harness-nudge-{os.getpid()}"
            _run(["tmux", "load-buffer", "-b", buf, tmp_path])
            _run(["tmux", "paste-buffer", "-b", buf, "-t", target, "-d"])
        finally:
            try:
                os.unlink(tmp_path)
            except FileNotFoundError:
                pass
    else:
        _run(["tmux", "send-keys", "-t", target, "-l", text])

    if submit:
        _run(["tmux", "send-keys", "-t", target, "Enter"])


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m harness.tmux_manager",
        description="tmux + git-worktree wrappers for the agent-harness",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    # Shared provisioning args, used by both spawn-worker and provision-worker.
    def _add_provision_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--task-id", required=True)
        g = p.add_mutually_exclusive_group(required=True)
        g.add_argument("--prompt", help="Initial prompt as a string")
        g.add_argument("--prompt-file", help="Path to a file containing the initial prompt")
        p.add_argument("--worktree", default=None, help="Worktree path (default: ../wt-<task-id>)")
        p.add_argument("--base-branch", default=None, help="Branch to cut the worktree from (default: repo's current branch, or $HARNESS_BASE_BRANCH)")
        p.add_argument("--repo-root", default=None, help="Repo to spawn the worktree from (default: cwd)")
        p.add_argument("--session", default=None)
        p.add_argument("--window", type=int, default=None, help="Target window index (default: tmux auto-assigns)")
        p.add_argument("--session-id", default=None, help="Pre-allocated claude --session-id UUID (default: generate one)")

    p_spawn = sub.add_parser("spawn-worker", help="Provision + launch a worker in one call (worker starts immediately)")
    _add_provision_args(p_spawn)

    p_prov = sub.add_parser("provision-worker", help="Phase 1: create worktree + reserve an idle window, but do NOT start the worker")
    _add_provision_args(p_prov)

    p_launch = sub.add_parser("launch-worker", help="Phase 2: start the worker in an already-provisioned window")
    p_launch.add_argument("--worktree", required=True, help="Worktree path returned by provision-worker")
    p_launch.add_argument("--session-id", required=True, help="session-id returned by provision-worker")
    p_launch.add_argument("--prompt-file", required=True, help="prompt_file returned by provision-worker")
    p_launch.add_argument("--window", required=True, help="Window index (or session:index) returned by provision-worker")
    p_launch.add_argument("--session", default=None)

    p_nudge = sub.add_parser("nudge", help="Send text to a running worker window")
    p_nudge.add_argument("--window", required=True, help="window-id (e.g. '1' or 'harness:1')")
    g_text = p_nudge.add_mutually_exclusive_group(required=True)
    g_text.add_argument("--text", help="Text to send")
    g_text.add_argument("--text-file", help="File containing text to send")
    p_nudge.add_argument("--no-submit", action="store_true", help="Don't press Enter after typing")

    p_kill = sub.add_parser("kill-window", help="Kill a tmux window")
    p_kill.add_argument("--window", required=True)

    p_rmwt = sub.add_parser("remove-worktree", help="git worktree remove")
    p_rmwt.add_argument("--path", required=True)
    p_rmwt.add_argument("--force", action="store_true")

    sub.add_parser("ensure-session", help="Create the harness session if missing")
    sub.add_parser("list-windows", help="List window indices in the harness session")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.cmd in ("spawn-worker", "provision-worker"):
        prompt = args.prompt if args.prompt is not None else Path(args.prompt_file).read_text(encoding="utf-8")
        fn = spawn_worker if args.cmd == "spawn-worker" else provision_worker
        info = fn(
            task_id=args.task_id,
            prompt=prompt,
            worktree=args.worktree,
            base_branch=args.base_branch,
            repo_root=args.repo_root,
            session=args.session,
            window=args.window,
            session_id=args.session_id,
        )
        json.dump(info, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    if args.cmd == "launch-worker":
        info = launch_worker(
            worktree=args.worktree,
            session_id=args.session_id,
            prompt_file=args.prompt_file,
            window=args.window,
            session=args.session,
        )
        json.dump(info, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    if args.cmd == "nudge":
        text = args.text if args.text is not None else Path(args.text_file).read_text(encoding="utf-8")
        send_nudge(args.window, text, submit=not args.no_submit)
        return 0

    if args.cmd == "kill-window":
        kill_window(args.window)
        return 0

    if args.cmd == "remove-worktree":
        remove_worktree(args.path, force=args.force)
        return 0

    if args.cmd == "ensure-session":
        sys.stdout.write(ensure_session() + "\n")
        return 0

    if args.cmd == "list-windows":
        for idx in _list_window_indices(_session_name()):
            sys.stdout.write(f"{idx}\n")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
