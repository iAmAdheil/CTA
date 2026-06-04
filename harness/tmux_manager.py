"""Tmux + git-worktree wrappers for the agent-harness.

The LLM orchestrator calls these via `python -m harness.tmux ...`. They
encapsulate the fiddly bits — tmux session/window lifecycle, git worktree
creation/cleanup, quoting of long/multiline prompts when nudging running
workers — so the orchestrator's prompt stays focused on policy not plumbing.

## Conventions

- Everything lives in one tmux session, named "harness" by default (override
  with `HARNESS_TMUX_SESSION`). Window 0 by convention holds the orchestrator.
  **Every spawned agent (worker, QA, fixer) is a PANE in one shared "agents"
  window** (override the name with `HARNESS_AGENTS_WINDOW`), so all live agents
  are visible together in a single window. The window is created on the first
  agent and disappears when the last pane is killed — it self-heals.
- An agent's durable identity is its tmux **pane id** (e.g. `%7`), not a window
  index. Pane ids are globally unique within the server and are never reused, so
  they survive other panes opening/closing — unlike pane *indices*, which
  renumber. The orchestrator stores this id and uses it for launch/teardown.
- Worktrees are created as siblings of the project root by default:
  `../wt-<task-id>`. Override per-call. There is **one worktree per task**, not
  per agent: the worker creates it, and QA / the Opus fixer / re-QA all reuse
  the same path on the same `task/<id>` branch. It lives until the orchestrator
  removes it when the task leaves its active states (not at each role handoff).
- Worker branches are named `task/<task-id>`.
- Each worker session has a pre-allocated UUID so the orchestrator can
  `claude --resume <uuid>` later (e.g. for QA-fail fix mode in Stage 6).

## Why a helper shell script

Passing a multi-KB prompt as a positional arg to `claude` through tmux's
`split-window <command-string>` would require fragile shell quoting. Instead
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
DEFAULT_AGENTS_WINDOW = "agents"
AGENTS_WINDOW_ENV = "HARNESS_AGENTS_WINDOW"
DEFAULT_BASE_BRANCH = "main"
SPAWN_HELPER = Path(__file__).parent / "_spawn_worker.sh"
CLAUDE_CONFIG = Path.home() / ".claude.json"
PROMPT_DIR_ENV = "HARNESS_PROMPT_DIR"
# Long-text threshold above which we use paste-buffer instead of send-keys
NUDGE_LITERAL_MAX = 2000
# Idle command a provisioned-but-not-yet-launched agent pane runs. It holds
# the pane open (and its pane id stable) until `launch_worker` respawns it with
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


def _agents_window_name() -> str:
    return os.environ.get(AGENTS_WINDOW_ENV, DEFAULT_AGENTS_WINDOW)


def _agents_window_target(session: str) -> str | None:
    """Return `session:index` of the shared agents window, or None if absent."""
    want = _agents_window_name()
    res = _run(
        ["tmux", "list-windows", "-t", session, "-F", "#{window_index} #{window_name}"],
        capture=True,
        check=False,
    )
    if res.returncode != 0:
        return None
    for line in res.stdout.splitlines():
        idx, _, name = line.partition(" ")
        if name == want:
            return f"{session}:{idx}"
    return None


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

def _resolve_worktree_path(task_id: str, worktree: str | None, label: str = "worker") -> Path:
    if worktree:
        return Path(worktree).expanduser().resolve()
    # Sibling of cwd, named after the TASK — never the label. There is exactly
    # one worktree per task (`../wt-<task-id>`), created when the worker spawns
    # and reused by every later agent on the same task (QA, Opus fixer, re-QA).
    # It persists until the task leaves its active states; the orchestrator is
    # what removes it (see the orchestrator skill, step 4 / 7). `label` still
    # titles the pane and names the prompt file, but it does NOT fork the path —
    # a label-prefixed path would defeat the reuse this whole design relies on.
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
    new_branch: bool = True,
) -> Path:
    """Add a git worktree at `path`. Returns the path. Idempotent on `path`.

    - `new_branch=True` (default, fresh worker): `git worktree add -b <branch>
      <path> <base_branch>` — creates the branch. Falls back to checking out an
      existing branch if `-b` reports it already exists.
    - `new_branch=False` (QA / Opus fixer): `git worktree add <path> <branch>` —
      checks out the EXISTING `task/<id>` branch (the PR branch the worker left
      behind). In normal flow the per-task worktree already exists (the worker's,
      which now persists across the whole QA loop), so the idempotency check
      below short-circuits and this never runs. It stays correct for the edge
      case where the worktree was removed by hand and a later agent recreates it.
    """
    p = Path(path).expanduser().resolve()
    cwd = str(repo_root) if repo_root else None
    # Check if this path is already a registered worktree. This is the common
    # case for QA/fixer/re-QA: they reuse the worker's still-live `../wt-<id>`,
    # so creation is a no-op and they just open a fresh pane in it.
    out = _run(["git", "worktree", "list", "--porcelain"], capture=True, check=False)
    if out.returncode == 0 and f"worktree {p}" in out.stdout:
        return p
    p.parent.mkdir(parents=True, exist_ok=True)
    if not new_branch:
        # Check out an existing branch into a new worktree.
        cmd = ["git", "worktree", "add", str(p), branch]
        res = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)
        if res.returncode != 0:
            raise RuntimeError(
                f"git worktree add (existing branch {branch!r}) failed: {res.stderr.strip()}"
            )
        return p
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
# Branch management (feature integration branch)
# ---------------------------------------------------------------------------
#
# The harness gives each spec a `feature/<spec-id>` integration branch. Every
# task is cut from it and PRs back into it; when a task passes QA the
# orchestrator merges its branch into the feature branch (`merge_branch`) and
# marks the task done. The human's only merge is feature/<spec-id> -> the
# release branch — which the agent is hard-guarded against doing here.


def _git(args: list[str], repo_root: str | os.PathLike | None = None) -> subprocess.CompletedProcess:
    cwd = str(repo_root) if repo_root else None
    return subprocess.run(["git", *args], cwd=cwd, text=True, capture_output=True)


def _branch_exists(name: str, repo_root: str | os.PathLike | None = None) -> bool:
    return _git(["rev-parse", "--verify", "--quiet", f"refs/heads/{name}"], repo_root).returncode == 0


def release_branch(repo_root: str | os.PathLike | None = None) -> str:
    """The repo's release/default branch — the one the agent must NOT merge into.

    Detection: `origin/HEAD` symbolic-ref (what the remote calls default) >
    a local `main` > a local `master` > DEFAULT_BASE_BRANCH. This is distinct
    from `resolve_base_branch`, which returns the *current* branch (a feature
    branch while a spec is in flight) — here we specifically want the release
    branch so `merge_branch` can refuse to touch it.
    """
    res = _git(["symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"], repo_root)
    if res.returncode == 0 and res.stdout.strip():
        # e.g. "refs/remotes/origin/main" -> "main"
        return res.stdout.strip().rsplit("/", 1)[-1]
    for cand in ("main", "master"):
        if _branch_exists(cand, repo_root):
            return cand
    return DEFAULT_BASE_BRANCH


def ensure_branch(
    name: str,
    base: str | None = None,
    *,
    checkout: bool = False,
    repo_root: str | os.PathLike | None = None,
) -> dict[str, Any]:
    """Ensure branch `name` exists, created from `base`. Idempotent.

    Used to create `feature/<spec-id>` at breakdown. `base` defaults to the
    repo's **release branch** (`release_branch()` — main/master), which is the
    right base for a feature branch regardless of what the orchestrator currently
    has checked out (e.g. a previous, not-yet-merged feature branch). With
    `checkout=True` the repo's working tree is switched to it (the orchestrator
    runs on the feature branch for the spec's duration, so the spec commit lands
    there and workers base onto it). If the branch already exists this only
    (optionally) checks it out — it never moves the branch.

    Returns {"branch", "created", "checked_out", "base"}.
    """
    base = base or release_branch(repo_root)
    existed = _branch_exists(name, repo_root)
    created = False
    if not existed:
        res = _git(["branch", name, base], repo_root)
        if res.returncode != 0:
            raise RuntimeError(f"git branch {name} {base} failed: {res.stderr.strip()}")
        created = True
    checked_out = False
    if checkout:
        res = _git(["checkout", name], repo_root)
        if res.returncode != 0:
            raise RuntimeError(f"git checkout {name} failed: {res.stderr.strip()}")
        checked_out = True
    return {"branch": name, "created": created, "checked_out": checked_out, "base": base}


def merge_branch(
    *,
    into: str,
    frm: str,
    pr_number: int | None = None,
    repo_root: str | os.PathLike | None = None,
    push: bool = False,
) -> dict[str, Any]:
    """Merge branch `frm` (a task/<id> branch) into `into` (feature/<spec-id>).

    This is the orchestrator's QA-pass integration step. It is **hard-guarded
    against merging into the release branch** (main/master/origin's default):
    that merge is the human's alone. On a merge conflict it aborts cleanly and
    reports it (the orchestrator then escalates the task to the human) — it
    never leaves the working tree in a half-merged state.

    Mechanism:
      - If `pr_number` is given AND a remote + `gh` are available, merge via
        `gh pr merge <n> --merge` (so the GitHub PR is marked merged), then
        fast-forward the local `into` branch to match.
      - Otherwise (local-marker mode / no remote), check out `into` and
        `git merge --no-ff <frm>` locally.

    Returns {"merged": bool, "conflict": bool, "refused": bool, "via": str,
    "reason": str}.
    """
    result = {"merged": False, "conflict": False, "refused": False, "via": "", "reason": ""}

    rel = release_branch(repo_root)
    if into in {"main", "master"} or into == rel:
        result["refused"] = True
        result["reason"] = (
            f"refusing to merge into release branch {into!r} — the agent only "
            f"integrates into a feature branch; merging to {rel!r} is the human's gate"
        )
        return result

    have_remote = bool(_git(["remote"], repo_root).stdout.strip())
    have_gh = shutil.which("gh") is not None

    if pr_number is not None and have_remote and have_gh:
        res = subprocess.run(
            ["gh", "pr", "merge", str(pr_number), "--merge"],
            cwd=str(repo_root) if repo_root else None, text=True, capture_output=True,
        )
        if res.returncode != 0:
            result["reason"] = f"gh pr merge #{pr_number} failed: {res.stderr.strip()}"
            # A blocked/conflicting PR reports here; treat as conflict for routing.
            result["conflict"] = True
            return result
        # Sync the local feature branch so worktrees cut from it locally see the merge.
        _git(["fetch", "origin", into], repo_root)
        _git(["checkout", into], repo_root)
        ff = _git(["merge", "--ff-only", f"origin/{into}"], repo_root)
        result["merged"] = True
        result["via"] = "gh"
        if ff.returncode != 0:
            result["reason"] = f"merged on GitHub; local ff-sync warning: {ff.stderr.strip()}"
        return result

    # Local merge path.
    co = _git(["checkout", into], repo_root)
    if co.returncode != 0:
        result["reason"] = f"git checkout {into} failed: {co.stderr.strip()}"
        return result
    msg = f"Merge {frm} into {into}"
    res = _git(["merge", "--no-ff", "-m", msg, frm], repo_root)
    if res.returncode != 0:
        # Conflict (or other failure): abort so the tree is left clean.
        _git(["merge", "--abort"], repo_root)
        result["conflict"] = True
        result["reason"] = f"git merge {frm} -> {into} failed: {res.stderr.strip() or res.stdout.strip()}"
        return result
    result["merged"] = True
    result["via"] = "local"
    if push and have_remote:
        _git(["push", "origin", into], repo_root)
    return result


# ---------------------------------------------------------------------------
# Pane management
# ---------------------------------------------------------------------------

def _list_pane_ids(session: str) -> list[str]:
    """All pane ids (e.g. `%7`) across every window of the session."""
    res = _run(
        ["tmux", "list-panes", "-s", "-t", session, "-F", "#{pane_id}"],
        capture=True,
        check=False,
    )
    if res.returncode != 0:
        return []
    return [x for x in res.stdout.split() if x.strip().startswith("%")]


def _retile_agents_window(session: str) -> None:
    """Rebalance the shared agents window to a tiled grid (best effort)."""
    win = _agents_window_target(session)
    if win is not None:
        _run(["tmux", "select-layout", "-t", win, "tiled"], check=False)


def kill_pane(pane_id: str) -> None:
    """`tmux kill-pane -t <pane_id>`. No-op if the pane is gone.

    `pane_id` is a tmux pane id such as `%7` (globally unique, what
    `provision_worker` returns). Killing the last pane in the shared agents
    window also closes that window — which is fine; it's recreated on the next
    spawn. Surviving panes are re-tiled so the layout stays balanced.
    """
    _run(["tmux", "kill-pane", "-t", pane_id], check=False)
    _retile_agents_window(_session_name())


# ---------------------------------------------------------------------------
# Worker spawn
# ---------------------------------------------------------------------------

def _open_agent_pane(session: str, pane_title: str) -> str:
    """Open an idle placeholder pane in the shared agents window; return its id.

    First agent: create the agents window (its first pane). Later agents:
    `split-window` a new pane into that window, then re-tile so all panes stay
    balanced and visible. Either way the pane runs `PLACEHOLDER_CMD` until
    `launch_worker` respawns it. Pane titles are shown in the pane border (set
    once on the window) so each agent is labelled in the shared view.
    """
    win = _agents_window_target(session)
    if win is None:
        cmd = [
            "tmux", "new-window",
            "-t", session, "-n", _agents_window_name(),
            "-P", "-F", "#{pane_id}",
            PLACEHOLDER_CMD,
        ]
        pane_id = _run(cmd, capture=True).stdout.strip()
        win = _agents_window_target(session) or f"{session}:{_agents_window_name()}"
        # Show per-pane titles in the border so each agent is labelled.
        _run(["tmux", "set-option", "-w", "-t", win, "pane-border-status", "top"], check=False)
    else:
        cmd = [
            "tmux", "split-window",
            "-t", win, "-P", "-F", "#{pane_id}",
            PLACEHOLDER_CMD,
        ]
        pane_id = _run(cmd, capture=True).stdout.strip()
        _retile_agents_window(session)

    _run(["tmux", "select-pane", "-t", pane_id, "-T", pane_title], check=False)
    return pane_id


def provision_worker(
    *,
    task_id: str,
    prompt: str,
    worktree: str | None = None,
    base_branch: str | None = None,
    repo_root: str | os.PathLike | None = None,
    session: str | None = None,
    pane_title: str | None = None,
    session_id: str | None = None,
    branch: str | None = None,
    new_branch: bool = True,
    label: str = "worker",
) -> dict[str, Any]:
    """Phase 1 of spawning: set everything up *except* starting the worker.

    Creates the worktree/branch, pre-trusts it, writes the prompt file, and
    reserves an **idle placeholder** pane (running `PLACEHOLDER_CMD`, not claude)
    in the shared agents window. The worker is NOT running yet.

    This exists so the orchestrator can do all of its bookkeeping — move the task
    file to `in-progress/`, set `status: in-progress` + worktree/pane/started/
    assigned_to, record the worker in `orchestrator-state.yaml` — *before* the
    worker process exists. A worker that is launched into a fully-prepared world
    can never observe a half-set-up task file nor race the orchestrator's writes
    to it. Call `launch_worker` with the returned fields to actually start it.

    Returns the same dict as `spawn_worker`, plus `"launched": False`.
    """
    sess = ensure_session(session)
    sid = session_id or str(uuid.uuid4())
    wt_path = _resolve_worktree_path(task_id, worktree, label)
    branch = branch or f"task/{task_id}"
    base = resolve_base_branch(base_branch, repo_root)

    create_worktree(branch, wt_path, base_branch=base, repo_root=repo_root, new_branch=new_branch)

    # Pre-trust the worktree so the worker doesn't hang on the folder-trust
    # dialog (no human to accept it in an unattended run).
    pretrust_path(wt_path)

    # Persist the prompt so the spawn helper can read it without quoting. The
    # label keeps a QA/fixer prompt from clobbering the worker's on the same task.
    prompt_file = _prompt_dir() / f"{task_id}.{label}.prompt.txt"
    prompt_file.write_text(prompt, encoding="utf-8")

    title = pane_title or f"{label}-{task_id}"
    pane_id = _open_agent_pane(sess, title)

    return {
        "task_id": task_id,
        "session_id": sid,
        "session": sess,
        "pane": pane_id,
        "window_target": _agents_window_target(sess),
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
    pane: str,
    session: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Phase 2 of spawning: start the worker in an already-provisioned pane.

    `tmux respawn-pane -k` kills the placeholder hold command and runs the
    spawn helper (which `exec`s `claude [--model M] --session-id <uuid>
    "$(cat <prompt>)"`) in the same pane. From here the worker is live.

    `model` is passed to `claude --model` — used to run the Opus fixer as opus;
    omit (None) to use claude's default. `pane` is the pane id (e.g. `%7`)
    returned by `provision_worker`; pane ids are global, so no session prefix
    is needed.
    """
    sess = session or _session_name()
    helper_cmd = f"{SPAWN_HELPER} {worktree!s} {session_id} {prompt_file!s}"
    if model:
        helper_cmd += f" {model}"
    cmd = [
        "tmux", "respawn-pane", "-k",
        "-t", str(pane),
        helper_cmd,
    ]
    _run(cmd)
    return {"session": sess, "pane": str(pane), "launched": True}


def spawn_worker(
    *,
    task_id: str,
    prompt: str,
    worktree: str | None = None,
    base_branch: str | None = None,
    repo_root: str | os.PathLike | None = None,
    session: str | None = None,
    pane_title: str | None = None,
    session_id: str | None = None,
    branch: str | None = None,
    new_branch: bool = True,
    label: str = "worker",
    model: str | None = None,
) -> dict[str, Any]:
    """Provision + launch a worker in one call (the worker starts immediately).

    Convenience wrapper around `provision_worker` + `launch_worker`. Prefer the
    two-phase form from the orchestrator so bookkeeping happens before launch
    (see `provision_worker`); use this for manual/testing one-shots where there
    is no concurrent writer to the task file.

    Returns:
        {
          "task_id": str, "session_id": str (UUID), "session": str,
          "pane": str (e.g. "%7"), "window_target": str, "worktree": str (abs),
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
        pane_title=pane_title,
        session_id=session_id,
        branch=branch,
        new_branch=new_branch,
        label=label,
    )
    launch_worker(
        worktree=info["worktree"],
        session_id=info["session_id"],
        prompt_file=info["prompt_file"],
        pane=info["pane"],
        session=info["session"],
        model=model,
    )
    info["launched"] = True
    return info


# ---------------------------------------------------------------------------
# Mid-session nudges
# ---------------------------------------------------------------------------

def send_nudge(target_id: str, text: str, *, submit: bool = True) -> None:
    """Deliver `text` to the given agent's pane.

    `target_id` is normally a pane id (e.g. `%7`), which tmux accepts as a
    target directly. A `session:window` form is also accepted, and a bare
    window index is interpreted under the default session.

    Long or multiline text goes through `tmux load-buffer` + `paste-buffer`
    (bracketed paste, handles any chars). Short single-line text goes via
    `send-keys -l` (literal). Always followed by Enter unless submit=False.
    """
    if target_id.startswith("%") or ":" in target_id:
        target = target_id
    else:
        target = f"{_session_name()}:{target_id}"

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
        p.add_argument("--session-id", default=None, help="Pre-allocated claude --session-id UUID (default: generate one)")
        p.add_argument("--branch", default=None, help="Branch name (default: task/<task-id>)")
        p.add_argument("--existing-branch", action="store_true", help="Check out an existing branch instead of creating one (QA / Opus fixer on the worker's PR branch)")
        p.add_argument("--label", default="worker", help="Agent kind: titles the pane + names the prompt file + default worktree (worker|qa|fixer)")
        p.add_argument("--model", default=None, help="claude --model for the spawned agent (e.g. opus for the fixer); spawn-worker only")

    p_spawn = sub.add_parser("spawn-worker", help="Provision + launch a worker in one call (worker starts immediately)")
    _add_provision_args(p_spawn)

    p_prov = sub.add_parser("provision-worker", help="Phase 1: create worktree + reserve an idle pane in the agents window, but do NOT start the worker")
    _add_provision_args(p_prov)

    p_launch = sub.add_parser("launch-worker", help="Phase 2: start the worker in an already-provisioned pane")
    p_launch.add_argument("--worktree", required=True, help="Worktree path returned by provision-worker")
    p_launch.add_argument("--session-id", required=True, help="session-id returned by provision-worker")
    p_launch.add_argument("--prompt-file", required=True, help="prompt_file returned by provision-worker")
    p_launch.add_argument("--pane", required=True, help="Pane id (e.g. '%7') returned by provision-worker")
    p_launch.add_argument("--session", default=None)
    p_launch.add_argument("--model", default=None, help="claude --model for the spawned agent (e.g. opus for the QA-fail fixer)")

    p_nudge = sub.add_parser("nudge", help="Send text to a running agent's pane")
    p_nudge.add_argument("--pane", required=True, help="Pane id (e.g. '%7') of the target agent")
    g_text = p_nudge.add_mutually_exclusive_group(required=True)
    g_text.add_argument("--text", help="Text to send")
    g_text.add_argument("--text-file", help="File containing text to send")
    p_nudge.add_argument("--no-submit", action="store_true", help="Don't press Enter after typing")

    p_kill = sub.add_parser("kill-pane", help="Kill an agent's tmux pane")
    p_kill.add_argument("--pane", required=True, help="Pane id (e.g. '%7')")

    p_rmwt = sub.add_parser("remove-worktree", help="git worktree remove")
    p_rmwt.add_argument("--path", required=True)
    p_rmwt.add_argument("--force", action="store_true")

    p_ensb = sub.add_parser("ensure-branch", help="Create a branch from a base if missing (idempotent); e.g. the per-spec feature branch")
    p_ensb.add_argument("--name", required=True, help="Branch to ensure (e.g. feature/<spec-id>)")
    p_ensb.add_argument("--base", default=None, help="Branch to create it from (default: the repo's release branch — main/master)")
    p_ensb.add_argument("--checkout", action="store_true", help="Also check it out in the working tree")
    p_ensb.add_argument("--repo-root", default=None, help="Repo to operate in (default: cwd)")

    p_mrg = sub.add_parser("merge-branch", help="Merge a task branch INTO the feature branch (QA-pass integration). Refuses to merge into the release branch.")
    p_mrg.add_argument("--into", required=True, help="Target branch (the feature/<spec-id> integration branch)")
    p_mrg.add_argument("--from", dest="frm", required=True, help="Source branch (task/<id>)")
    p_mrg.add_argument("--pr-number", type=int, default=None, help="PR number — when set and a remote+gh exist, merge via `gh pr merge` so the PR is marked merged")
    p_mrg.add_argument("--push", action="store_true", help="Push the feature branch after a local merge (when a remote exists)")
    p_mrg.add_argument("--repo-root", default=None, help="Repo to operate in (default: cwd)")

    sub.add_parser("ensure-session", help="Create the harness session if missing")
    sub.add_parser("list-panes", help="List live agent pane ids in the harness session")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.cmd in ("spawn-worker", "provision-worker"):
        prompt = args.prompt if args.prompt is not None else Path(args.prompt_file).read_text(encoding="utf-8")
        fn = spawn_worker if args.cmd == "spawn-worker" else provision_worker
        kwargs = dict(
            task_id=args.task_id,
            prompt=prompt,
            worktree=args.worktree,
            base_branch=args.base_branch,
            repo_root=args.repo_root,
            session=args.session,
            session_id=args.session_id,
            branch=args.branch,
            new_branch=not args.existing_branch,
            label=args.label,
        )
        if args.cmd == "spawn-worker":
            kwargs["model"] = args.model
        info = fn(**kwargs)
        json.dump(info, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    if args.cmd == "launch-worker":
        info = launch_worker(
            worktree=args.worktree,
            session_id=args.session_id,
            prompt_file=args.prompt_file,
            pane=args.pane,
            session=args.session,
            model=args.model,
        )
        json.dump(info, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    if args.cmd == "nudge":
        text = args.text if args.text is not None else Path(args.text_file).read_text(encoding="utf-8")
        send_nudge(args.pane, text, submit=not args.no_submit)
        return 0

    if args.cmd == "kill-pane":
        kill_pane(args.pane)
        return 0

    if args.cmd == "remove-worktree":
        remove_worktree(args.path, force=args.force)
        return 0

    if args.cmd == "ensure-branch":
        info = ensure_branch(
            args.name, args.base, checkout=args.checkout, repo_root=args.repo_root
        )
        json.dump(info, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    if args.cmd == "merge-branch":
        info = merge_branch(
            into=args.into, frm=args.frm, pr_number=args.pr_number,
            repo_root=args.repo_root, push=args.push,
        )
        json.dump(info, sys.stdout, indent=2)
        sys.stdout.write("\n")
        # Non-zero exit on a non-merge so the orchestrator can branch on $? too,
        # but the JSON (refused/conflict/merged) is the authoritative signal.
        return 0 if info.get("merged") else 1

    if args.cmd == "ensure-session":
        sys.stdout.write(ensure_session() + "\n")
        return 0

    if args.cmd == "list-panes":
        for pid in _list_pane_ids(_session_name()):
            sys.stdout.write(f"{pid}\n")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
