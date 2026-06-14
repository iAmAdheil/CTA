"""Mechanical event producer for the harness history store.

Launched by `harness-up.sh` as a pane in the harness tmux session, one per run.
It runs for the life of the run and records — WITHOUT any LLM in the loop — what
the harness does, by three mechanical means:

  1. **Agent transcripts** — every harness agent (the pinned orchestrator + each
     spawned worker / QA / fixer) writes a Claude Code JSONL transcript. We learn
     their session UUIDs from the harness's own control plane (`assigned_to` in
     task files + the pinned orchestrator session id), locate each transcript by
     globbing ~/.claude/projects/*/<uuid>.jsonl (workers run in worktrees, so
     their transcript is under a *different* project-slug dir), and tail it for
     tool calls + tool errors. Because we only ever tail UUIDs the harness itself
     recorded, an ad-hoc Claude session you open by hand is never captured.

  2. **Control-plane diff** — task files moving between state dirs / changing
     `status`, and QA verdicts appearing in qa-report files.

  3. **Git poll** — new commits, branches, merges, worktrees.

Depth = "events + tool calls": we store each tool call's name + a short summary
(+ a `ref` back to the transcript line for manual drill-down) and tool *errors*,
but not full tool inputs/outputs or agent reasoning.

Run it standalone for one tick (testing):
    python -m harness.history_watcher --project . --run-id <id> --once
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import yaml

from . import history


# ---------------------------------------------------------------------------
# Small formatting helpers (mirrors scripts/_fmt_stream.py so summaries match)
# ---------------------------------------------------------------------------

def _short(s: Any, n: int = 160) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _tool_summary(name: str, inp: Any) -> str:
    """One-line, human summary of a tool call from its input dict."""
    if not isinstance(inp, dict):
        return _short(inp, 120)
    for k in ("command", "file_path", "path", "description", "pattern",
              "query", "url", "prompt", "old_string", "content"):
        if inp.get(k):
            val = _short(inp[k], 120)
            # Lead common tools with the target so the timeline scans well.
            if name in ("Edit", "Write", "Read", "NotebookEdit") and k == "file_path":
                return val
            if name == "Bash" and k == "command":
                return val
            return f"{k}={val}"
    return _short(json.dumps(inp), 120)


def _file_sig(p: Path) -> str | None:
    try:
        st = p.stat()
        return f"{st.st_mtime_ns}:{st.st_size}"
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Transcript location + tailing
# ---------------------------------------------------------------------------

def _claude_projects_dir() -> Path:
    return Path.home() / ".claude" / "projects"


def find_transcript(session_uuid: str) -> Path | None:
    """Locate an agent's transcript by UUID across every project-slug dir.

    Globbing by UUID is slug-independent — it finds the file whether the agent
    ran in the main repo (orchestrator) or in a worktree (worker/QA/fixer), which
    land under different ~/.claude/projects/<slug>/ dirs.
    """
    matches = list(_claude_projects_dir().glob(f"*/{session_uuid}.jsonl"))
    return matches[0] if matches else None


def read_new_lines(path: Path, offset: int) -> tuple[list[str], int]:
    """Read complete (newline-terminated) lines from `offset`. Returns (lines, new_offset).

    A trailing partial line (the agent mid-write) is left unconsumed so the next
    tick re-reads it once complete — we never parse a half-written JSON line.
    """
    try:
        with path.open("rb") as f:
            f.seek(offset)
            data = f.read()
    except OSError:
        return [], offset
    if not data:
        return [], offset
    last_nl = data.rfind(b"\n")
    if last_nl == -1:
        return [], offset  # no complete line yet
    consumed = data[: last_nl + 1]
    text = consumed.decode("utf-8", errors="replace")
    lines = [ln for ln in text.split("\n") if ln.strip()]
    return lines, offset + len(consumed)


# ---------------------------------------------------------------------------
# The watcher
# ---------------------------------------------------------------------------

QA_VERDICTS = ("looks-good", "needs-changes", "escalate", "WIP")


class Watcher:
    def __init__(self, *, project: Path, run_id: str, slug: str,
                 orch_sid: str | None, git_every: int = 3):
        self.project = project
        self.run_id = run_id
        self.slug = slug
        self.orch_sid = orch_sid
        self.git_every = git_every
        self.rd = history.run_dir(slug, run_id)
        self.seq = history._next_seq(self.rd)
        self.tick_n = 0

        # session uuid -> {role, task, feature, model, path, offset, found,
        #                  started, stopped, tools{tool_use_id: name}}
        self.agents: dict[str, dict[str, Any]] = {}
        # task id -> {state, status, feature, assigned_to, verdict}
        self.tasks: dict[str, dict[str, Any]] = {}
        self.state_sig: str | None = None
        self.board_sigs: dict[str, str] = {}
        self.seen_commits: set[str] = set()
        self.seen_branches: set[str] = set()
        self.seen_worktrees: set[str] = set()
        self.emitted_verdicts: set[tuple[str, str]] = set()  # (task_id, verdict) — dedup
        self._git_primed = False

        # The pinned orchestrator is a first-class agent from t=0.
        if orch_sid:
            self.agents[orch_sid] = self._new_agent("orchestrator", None, None, "opus")

    # -- emit -----------------------------------------------------------------

    def emit(self, ev: dict[str, Any]) -> None:
        ev.setdefault("run", self.run_id)
        self.seq = history.append_event(self.rd, ev, seq=self.seq) + 1

    def _new_agent(self, role, task, feature, model):
        return {"role": role, "task": task, "feature": feature, "model": model,
                "path": None, "offset": 0, "found": False, "started": False,
                "stopped": False, "tools": {}}

    # -- agents / transcripts -------------------------------------------------

    def _register_agents_from_tasks(self) -> None:
        """Pick up worker/QA/fixer session UUIDs from task `assigned_to`."""
        roles_by_task = self._active_roles()
        for t in self.tasks.values():
            sid = t.get("assigned_to")
            if not sid or sid in self.agents:
                continue
            role, model = roles_by_task.get(t["id"], ("worker", t.get("model", "sonnet")))
            self.agents[sid] = self._new_agent(role, t["id"], t.get("feature"), model)

    def _active_roles(self) -> dict[str, tuple[str, str]]:
        """task_id -> (role, model) from orchestrator-state.yaml active_workers."""
        out: dict[str, tuple[str, str]] = {}
        sf = self.project / "orchestrator-state.yaml"
        try:
            data = yaml.safe_load(sf.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            return out
        for w in data.get("active_workers", []) or []:
            if isinstance(w, dict) and w.get("task_id"):
                out[w["task_id"]] = (w.get("role", "worker"), w.get("model", "sonnet"))
        return out

    def _tail_agents(self) -> None:
        for sid, a in self.agents.items():
            if a["stopped"]:
                continue
            if a["path"] is None:
                found = find_transcript(sid)
                if found is None:
                    continue
                a["path"] = found
                a["found"] = True
            if not a["started"]:
                a["started"] = True
                self.emit({"kind": "agent_start", "session": sid, "role": a["role"],
                           "task": a["task"], "feature": a["feature"], "model": a["model"]})
            lines, a["offset"] = read_new_lines(a["path"], a["offset"])
            for i, raw in enumerate(lines):
                try:
                    line = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                self._parse_transcript_line(sid, a, line)

    def _parse_transcript_line(self, sid: str, a: dict[str, Any], ev: dict[str, Any]) -> None:
        t = ev.get("type")
        ts = ev.get("timestamp")
        base = {"session": sid, "role": a["role"], "task": a["task"],
                "feature": a["feature"]}
        if t == "assistant":
            content = (ev.get("message") or {}).get("content") or []
            for c in content:
                if not isinstance(c, dict) or c.get("type") != "tool_use":
                    continue
                name = c.get("name", "?")
                a["tools"][c.get("id")] = name
                self.emit({**base, "kind": "tool", "ts": ts, "tool": name,
                           "summary": _tool_summary(name, c.get("input")),
                           "ref": {"transcript": str(a["path"]), "uuid": ev.get("uuid")}})
        elif t == "user":
            content = (ev.get("message") or {}).get("content") or []
            for c in content:
                if not isinstance(c, dict) or c.get("type") != "tool_result":
                    continue
                if not c.get("is_error"):
                    continue
                body = c.get("content", "")
                if isinstance(body, list):
                    body = " ".join(b.get("text", "") for b in body if isinstance(b, dict))
                tool = a["tools"].get(c.get("tool_use_id"), "")
                self.emit({**base, "kind": "error", "ts": ts, "tool": tool,
                           "summary": _short(body, 200)})

    def _stop_finished_agents(self) -> None:
        """Emit agent_stop when a task's agent is gone from active_workers and the
        task is in a terminal-for-the-agent status (the role handed off / ended)."""
        active_tasks = set(self._active_roles().keys())
        for sid, a in self.agents.items():
            if a["stopped"] or not a["started"] or a["role"] == "orchestrator":
                continue
            task = a["task"]
            if task and task not in active_tasks:
                a["stopped"] = True
                self.emit({"kind": "agent_stop", "session": sid, "role": a["role"],
                           "task": task, "feature": a["feature"]})

    # -- control plane --------------------------------------------------------

    def _scan_tasks(self) -> None:
        """Diff task files: emit task_move on state/status change, qa_verdict on
        a new verdict in the linked qa-report."""
        root = self.project / "tasks"
        current: dict[str, dict[str, Any]] = {}
        if root.exists():
            for tf in root.glob("*/*/*.yaml"):
                try:
                    data = yaml.safe_load(tf.read_text(encoding="utf-8")) or {}
                except (OSError, yaml.YAMLError):
                    continue
                if not isinstance(data, dict) or not data.get("id"):
                    continue
                tid = data["id"]
                current[tid] = {
                    "id": tid,
                    "state": tf.parent.name,          # backlog/in-progress/review/done
                    "status": data.get("status"),
                    "feature": data.get("feature") or tf.parent.parent.name,
                    "assigned_to": data.get("assigned_to"),
                    "model": data.get("model"),
                    "qa_report": data.get("qa_report"),
                }
        for tid, t in current.items():
            prev = self.tasks.get(tid)
            if prev is None:
                # First sighting. Only announce mid-flight work (a task being
                # actively worked when the run started) — don't replay a backlog
                # or already-done baseline as a "move" on every fresh launch.
                if t["state"] in ("in-progress", "review"):
                    self.emit({"kind": "task_move", "task": tid, "feature": t["feature"],
                               "from": None, "to": t["state"], "status": t["status"]})
            elif prev["state"] != t["state"] or prev["status"] != t["status"]:
                self.emit({"kind": "task_move", "task": tid, "feature": t["feature"],
                           "from": prev["state"], "to": t["state"], "status": t["status"]})
            self._check_qa_verdict(t)
        self.tasks = current
        self._register_agents_from_tasks()

    def _check_qa_verdict(self, t: dict[str, Any]) -> None:
        rep = t.get("qa_report")
        if not rep:
            return
        p = (self.project / rep) if not os.path.isabs(rep) else Path(rep)
        if not p.exists():
            return
        verdict = None
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                low = line.lower()
                if "status:" in low:
                    for v in QA_VERDICTS:
                        if v.lower() in low:
                            verdict = v
                            break
                if verdict:
                    break
        except OSError:
            return
        key = (t["id"], verdict)
        if verdict and verdict != "WIP" and key not in self.emitted_verdicts:
            self.emitted_verdicts.add(key)
            self.emit({"kind": "qa_verdict", "task": t["id"], "feature": t["feature"],
                       "verdict": verdict, "report": str(p)})

    def _snapshot_state(self) -> None:
        sf = self.project / "orchestrator-state.yaml"
        sig = _file_sig(sf)
        if sig is None or sig == self.state_sig:
            return
        self.state_sig = sig
        try:
            data = yaml.safe_load(sf.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            return
        # copy the raw file into snapshots/ and emit a light cycle/state event
        snap = self.rd / "snapshots" / f"state-{self.seq:05d}.yaml"
        try:
            snap.write_text(sf.read_text(encoding="utf-8"), encoding="utf-8")
        except OSError:
            pass
        workers = [
            {"task": w.get("task_id"), "role": w.get("role"), "pane": w.get("pane")}
            for w in data.get("active_workers", []) or [] if isinstance(w, dict)
        ]
        self.emit({"kind": "state", "cycle_count": data.get("cycle_count"),
                   "last_action": data.get("last_action"),
                   "active": workers, "snapshot": snap.name})

    def _snapshot_boards(self) -> None:
        boards = list((self.project / "docs" / "_kanban").glob("*.md"))
        boards += list((self.project / "tasks").glob("*/board.md"))
        for b in boards:
            sig = _file_sig(b)
            key = str(b)
            if sig and self.board_sigs.get(key) != sig:
                self.board_sigs[key] = sig
                # boards are noisy; we snapshot the file but don't spam the timeline
                dest = self.rd / "snapshots" / f"board-{b.parent.name}-{b.name}"
                try:
                    dest.write_text(b.read_text(encoding="utf-8"), encoding="utf-8")
                except OSError:
                    pass

    # -- git ------------------------------------------------------------------

    def _git(self, *args: str) -> str:
        try:
            r = subprocess.run(["git", "-C", str(self.project), *args],
                               text=True, capture_output=True)
            return r.stdout if r.returncode == 0 else ""
        except OSError:
            return ""

    def _poll_git(self) -> None:
        # commits across all branches
        for line in self._git("log", "--all", "--pretty=format:%H%x09%D%x09%s",
                              "-n", "120").splitlines():
            sha, _, rest = line.partition("\t")
            refs, _, subject = rest.partition("\t")
            if sha and sha not in self.seen_commits:
                self.seen_commits.add(sha)
                if self._git_primed:
                    self.emit({"kind": "git", "action": "commit", "sha": sha[:10],
                               "branch": _short(refs, 60), "summary": _short(subject, 120)})
        # branches
        for b in self._git("for-each-ref", "--format=%(refname:short)",
                           "refs/heads").split():
            if b and b not in self.seen_branches:
                self.seen_branches.add(b)
                if self._git_primed:
                    self.emit({"kind": "git", "action": "branch", "branch": b})
        # worktrees
        cur_wts = set()
        for line in self._git("worktree", "list", "--porcelain").splitlines():
            if line.startswith("worktree "):
                cur_wts.add(line.split(" ", 1)[1].strip())
        for w in cur_wts - self.seen_worktrees:
            if self._git_primed:
                self.emit({"kind": "git", "action": "worktree_add", "summary": w})
        for w in self.seen_worktrees - cur_wts:
            if self._git_primed:
                self.emit({"kind": "git", "action": "worktree_remove", "summary": w})
        self.seen_worktrees = cur_wts
        self._git_primed = True  # first poll just seeds the baseline (no backfill spam)

    # -- tick / loop ----------------------------------------------------------

    def tick(self) -> None:
        self.tick_n += 1
        self._scan_tasks()
        self._snapshot_state()
        self._snapshot_boards()
        self._tail_agents()
        self._stop_finished_agents()
        if self.tick_n == 1 or self.tick_n % self.git_every == 0:
            self._poll_git()

    def finalize(self, reason: str = "teardown") -> None:
        for sid, a in self.agents.items():
            if a["started"] and not a["stopped"] and a["role"] != "orchestrator":
                self.emit({"kind": "agent_stop", "session": sid, "role": a["role"],
                           "task": a["task"], "feature": a["feature"]})
        history.end_run(slug=self.slug, run_id=self.run_id, reason=reason)
        history.build_index()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m harness.history_watcher")
    ap.add_argument("--project", default=os.getcwd())
    ap.add_argument("--run-id", default=os.environ.get("HARNESS_RUN_ID"))
    ap.add_argument("--orch-sid", default=os.environ.get("HARNESS_ORCH_SID"))
    ap.add_argument("--poll", type=float, default=2.0, help="Seconds between ticks")
    ap.add_argument("--index-every", type=int, default=10, help="Rebuild index every N ticks")
    ap.add_argument("--once", action="store_true", help="Run a single tick and exit (testing)")
    args = ap.parse_args(argv)

    if not args.run_id:
        sys.stderr.write("history_watcher: --run-id (or $HARNESS_RUN_ID) is required\n")
        return 2

    project = Path(os.path.abspath(os.path.expanduser(args.project)))
    slug = history.project_slug(project)
    w = Watcher(project=project, run_id=args.run_id, slug=slug, orch_sid=args.orch_sid)

    if args.once:
        w.tick()
        history.build_index()
        return 0

    stop = {"flag": False}

    def _handle(signum, frame):
        stop["flag"] = True

    for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        try:
            signal.signal(s, _handle)
        except (ValueError, OSError):
            pass

    sys.stderr.write(
        f"history-watcher: recording run {args.run_id} "
        f"→ {history.run_dir(slug, args.run_id)}\n"
    )
    sys.stderr.flush()
    try:
        while not stop["flag"]:
            try:
                w.tick()
            except Exception as e:  # never let one bad tick kill the recorder
                sys.stderr.write(f"history-watcher: tick error: {e}\n")
            if w.tick_n % args.index_every == 0:
                try:
                    history.build_index()
                except Exception:
                    pass
            # responsive sleep so signals are handled promptly
            slept = 0.0
            while slept < args.poll and not stop["flag"]:
                time.sleep(0.1)
                slept += 0.1
    finally:
        w.finalize("teardown")
    return 0


if __name__ == "__main__":
    sys.exit(main())
