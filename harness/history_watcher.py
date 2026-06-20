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

from . import history, pricing


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

# An agent with an in-progress task but no new tool activity for this long (wall
# clock) is flagged as a stall. Generous so a long Bash/test run isn't a false
# positive.
STALL_SECONDS = 300

# Tool result tail length kept on a `tool` event (Bash stdout/stderr etc.).
RESULT_TAIL = 200

# Agent final-message length kept as `final_note` — the agent's own verdict on
# what it did. Generous: this is the per-task "what happened" summary the Tasks
# view shows in full (the timeline truncates it with an ellipsis on its own).
FINAL_NOTE = 1000


def _iso_ms(ts: str | None) -> float | None:
    """ISO-8601-Z timestamp → epoch milliseconds, or None."""
    if not ts:
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp() * 1000.0
    except (ValueError, TypeError):
        return None


# Classify a tool result into a `problem` code. Matches *actual* git failure
# output, not JSON that merely mentions "conflict"/"rejected" as a false value —
# e.g. the merge helper prints {"merged": true, "conflict": false} on success.
def _problem_code(tool: str, ok: bool, body: str) -> str | None:
    low = body.lower()
    if ("[rejected]" in low or "[remote rejected]" in low or "non-fast-forward" in low
            or "updates were rejected" in low or "failed to push some refs" in low):
        return "push_rejected"
    if ("automatic merge failed" in low or "conflict (content" in low
            or "merge conflict in" in low or "fix conflicts and then commit" in low):
        return "merge_conflict"
    if not ok and ("permission denied" in low or "haven't granted" in low
                   or "requested permissions" in low or "not allowed to" in low):
        return "permission_denied"
    return None


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

        # Rebuild mode: collect events into a list (no file append) and stamp them
        # with transcript time so they merge coherently with preserved control-
        # plane events. Live recording leaves both unset.
        self._sink: list[dict[str, Any]] | None = None
        self.rebuild = False

        # The pinned orchestrator is a first-class agent from t=0.
        if orch_sid:
            self.agents[orch_sid] = self._new_agent("orchestrator", None, None, "opus")

    # -- emit -----------------------------------------------------------------

    def emit(self, ev: dict[str, Any]) -> None:
        ev.setdefault("run", self.run_id)
        if self._sink is not None:
            ev = {"ts": ev.get("ts"), **ev}  # keep explicit ts (transcript) if set
            self._sink.append(ev)
            return
        self.seq = history.append_event(self.rd, ev, seq=self.seq) + 1

    def _new_agent(self, role, task, feature, model):
        return {"role": role, "task": task, "feature": feature, "model": model,
                "path": None, "offset": 0, "found": False, "started": False,
                "stopped": False, "tools": {},
                # --- accounting (Tier 2) -----------------------------------
                "pending": {},          # tool_use_id -> {name, summary, src_ts, uuid, file}
                "real_model": None,     # full model id seen in transcript (for cost)
                "tok": {"input_tokens": 0, "output_tokens": 0,
                        "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
                "turns": 0, "tool_calls": 0, "fails": 0,
                "files": set(),         # distinct file paths written
                "first_ts": None, "last_ts": None,   # transcript clock (activity span)
                "last_text": None,      # final assistant message → final_note
                "last_activity": None,  # wall clock of last new transcript line (stall)
                "stalled": False}

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
            if lines:
                a["last_activity"] = time.time()
            for i, raw in enumerate(lines):
                try:
                    line = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                self._parse_transcript_line(sid, a, line)

    def _parse_transcript_line(self, sid: str, a: dict[str, Any], ev: dict[str, Any]) -> None:
        """Per transcript line: buffer tool_use, accumulate usage/turns/text, and
        emit a complete `tool` event (with ok + result tail) when its result lands.

        Depth stays "events + tool calls": we keep each call's input summary, a
        short result tail, success flag, and the agent's *final* message — not
        full IO or reasoning.
        """
        t = ev.get("type")
        ts = ev.get("timestamp")
        base = {"session": sid, "role": a["role"], "task": a["task"],
                "feature": a["feature"]}
        # track the agent's activity span on the transcript clock
        if ts:
            if a["first_ts"] is None:
                a["first_ts"] = ts
            a["last_ts"] = ts

        if t == "assistant":
            msg = ev.get("message") or {}
            if msg.get("model"):
                a["real_model"] = msg["model"]
            self._accumulate_usage(a, msg.get("usage") or {})
            content = msg.get("content") or []
            saw_block = False
            for c in content:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "text" and c.get("text", "").strip():
                    a["last_text"] = c["text"].strip()
                    saw_block = True
                elif c.get("type") == "tool_use":
                    saw_block = True
                    name = c.get("name", "?")
                    a["tools"][c.get("id")] = name
                    a["tool_calls"] += 1
                    inp = c.get("input") if isinstance(c.get("input"), dict) else {}
                    fp = inp.get("file_path") or inp.get("path")
                    if name in ("Write", "Edit", "NotebookEdit") and fp:
                        a["files"].add(fp)
                    a["pending"][c.get("id")] = {
                        "name": name, "summary": _tool_summary(name, c.get("input")),
                        "src_ts": ts, "uuid": ev.get("uuid"),
                    }
            if saw_block:
                a["turns"] += 1

        elif t == "user":
            content = (ev.get("message") or {}).get("content") or []
            for c in content:
                if not isinstance(c, dict) or c.get("type") != "tool_result":
                    continue
                self._emit_tool_result(a, base, c, ts)

    def _accumulate_usage(self, a: dict[str, Any], usage: dict[str, Any]) -> None:
        for k in a["tok"]:
            v = usage.get(k)
            if isinstance(v, int):
                a["tok"][k] += v

    def _emit_tool_result(self, a, base, c, ts) -> None:
        """Emit the buffered `tool` event for this result, plus a `problem` if the
        result text matches a known failure (push rejected, conflict, permission)."""
        ok = not c.get("is_error")
        body = c.get("content", "")
        if isinstance(body, list):
            body = " ".join(b.get("text", "") for b in body if isinstance(b, dict))
        body = str(body)
        p = a["pending"].pop(c.get("tool_use_id"), None)
        name = (p or {}).get("name") or a["tools"].get(c.get("tool_use_id"), "?")
        if not ok:
            a["fails"] += 1
        src_ts = (p or {}).get("src_ts")
        dur = None
        a_ms, b_ms = _iso_ms(src_ts), _iso_ms(ts)
        if a_ms is not None and b_ms is not None and b_ms >= a_ms:
            dur = int(b_ms - a_ms)
        rts = ts if self.rebuild else None
        self.emit({**base, "kind": "tool", "tool": name, "ok": ok, "ts": rts,
                   "summary": (p or {}).get("summary", ""),
                   "result": _short(body, RESULT_TAIL) if body.strip() else "",
                   "dur_ms": dur, "src_ts": src_ts,
                   "ref": {"transcript": str(a["path"]), "uuid": (p or {}).get("uuid")}})
        code = _problem_code(name, ok, body)
        if code:
            self.emit({**base, "kind": "problem", "severity": "error" if not ok else "warn",
                       "code": code, "tool": name, "reason": _short(body, 200), "ts": rts})

    def _flush_pending(self, a, sid) -> None:
        """Emit any tool_use whose result never arrived (agent ended mid-call)."""
        base = {"session": sid, "role": a["role"], "task": a["task"], "feature": a["feature"]}
        for tu_id, p in list(a["pending"].items()):
            self.emit({**base, "kind": "tool", "tool": p["name"], "ok": None,
                       "summary": p["summary"], "result": "", "dur_ms": None,
                       "src_ts": p["src_ts"], "ts": p["src_ts"] if self.rebuild else None,
                       "ref": {"transcript": str(a["path"]), "uuid": p["uuid"]}})
        a["pending"].clear()

    def _agent_summary(self, a: dict[str, Any]) -> dict[str, Any]:
        """The Tier-2 accounting block carried on agent_stop."""
        model = a["real_model"] or a["model"]
        cost = pricing.cost_usd(a["tok"], model)
        active = None
        f, l = _iso_ms(a["first_ts"]), _iso_ms(a["last_ts"])
        if f is not None and l is not None and l >= f:
            active = int((l - f) / 1000)
        return {
            "model": model,
            "tokens": dict(a["tok"]),
            "cost_usd": cost,
            "turns": a["turns"], "tool_calls": a["tool_calls"], "errors": a["fails"],
            "files_written": len(a["files"]),
            "active_dur_s": active,
            "final_note": _short(a["last_text"], FINAL_NOTE) if a["last_text"] else None,
        }

    def _emit_agent_stop(self, sid: str, a: dict[str, Any]) -> None:
        """Flush pending tools, emit agent_stop with the accounting summary, and
        flag a worker that ended without writing any files."""
        self._flush_pending(a, sid)
        a["stopped"] = True
        rts = a["last_ts"] if self.rebuild else None
        self.emit({"kind": "agent_stop", "session": sid, "role": a["role"],
                   "task": a["task"], "feature": a["feature"], "ts": rts,
                   **self._agent_summary(a)})
        if a["role"] == "worker" and not a["files"]:
            self.emit({"kind": "problem", "severity": "warn", "code": "zero_write_worker",
                       "session": sid, "role": a["role"], "task": a["task"],
                       "feature": a["feature"], "ts": rts,
                       "reason": "worker ended without writing any files"})

    def _stop_finished_agents(self) -> None:
        """Emit agent_stop when a task's agent is gone from active_workers and the
        task is in a terminal-for-the-agent status (the role handed off / ended)."""
        active_tasks = set(self._active_roles().keys())
        for sid, a in self.agents.items():
            if a["stopped"] or not a["started"] or a["role"] == "orchestrator":
                continue
            task = a["task"]
            if task and task not in active_tasks:
                self._emit_agent_stop(sid, a)

    def _detect_stalls(self) -> None:
        """Flag a started, non-orchestrator agent whose task is still in-progress
        but which has shown no new transcript activity for STALL_SECONDS."""
        active_tasks = set(self._active_roles().keys())
        now = time.time()
        for sid, a in self.agents.items():
            if (a["stopped"] or not a["started"] or a["stalled"]
                    or a["role"] == "orchestrator" or not a["last_activity"]):
                continue
            if a["task"] in active_tasks and now - a["last_activity"] > STALL_SECONDS:
                a["stalled"] = True
                mins = int((now - a["last_activity"]) / 60)
                self.emit({"kind": "problem", "severity": "warn", "code": "agent_stall",
                           "session": sid, "role": a["role"], "task": a["task"],
                           "feature": a["feature"],
                           "reason": f"no tool activity for ~{mins}m while in-progress"})

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
            if verdict in ("needs-changes", "escalate"):
                self.emit({"kind": "problem", "severity": "warn",
                           "code": f"qa_{verdict.replace('-', '_')}",
                           "task": t["id"], "feature": t["feature"],
                           "reason": f"QA verdict: {verdict}"})

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
        self._detect_stalls()
        if self.tick_n == 1 or self.tick_n % self.git_every == 0:
            self._poll_git()

    def finalize(self, reason: str = "teardown") -> None:
        # one last read so the orchestrator's final cycle + any trailing tool
        # results are captured, then stop every still-running agent (the pinned
        # orchestrator included — we want its tokens/cost in the rollup).
        self._tail_agents()
        for sid, a in self.agents.items():
            if a["started"] and not a["stopped"]:
                self._emit_agent_stop(sid, a)
        history.end_run(slug=self.slug, run_id=self.run_id, reason=reason)
        history.build_index()


# Event kinds the recorder derives from agent transcripts (re-derived on rebuild).
_TRANSCRIPT_KINDS = {"agent_start", "agent_stop", "tool", "error"}
# Problem codes that come from a transcript tool result / agent stop (re-derived).
_TRANSCRIPT_PROBLEMS = {"push_rejected", "merge_conflict", "permission_denied",
                        "zero_write_worker"}


def rebuild_run(project: Path, run_id: str) -> int:
    """Re-derive a run's transcript-sourced events with the current recorder, and
    *merge* them with the control-plane / git / QA events preserved from the
    original stream — so an old run gains tokens, cost, tool results and notes
    without losing its task moves and verdicts.

    Non-destructive: backs the original up to `events.ndjson.bak` (and re-reads it
    on subsequent runs) and leaves `run.json` timestamps untouched. Re-derived
    events are stamped with their transcript time so they interleave coherently
    with the preserved control-plane events.
    """
    slug = history.project_slug(project)
    rd = history.run_dir(slug, run_id)
    ep = rd / "events.ndjson"
    bak = rd / "events.ndjson.bak"
    src = bak if bak.exists() else ep
    if not src.exists():
        sys.stderr.write(f"rebuild: no events at {src}\n")
        return 2

    orig = [json.loads(l) for l in src.read_text(encoding="utf-8").splitlines() if l.strip()]
    run_meta = {}
    try:
        run_meta = json.loads((rd / "run.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    orch_sid = run_meta.get("orch_session")
    roster: dict[str, tuple] = {}
    preserved: list[dict[str, Any]] = []
    for ev in orig:
        k = ev.get("kind")
        if k == "run_start" and not orch_sid:
            orch_sid = ev.get("orch_session")
        sid = ev.get("session")
        if sid and sid not in roster and ev.get("role"):
            roster[sid] = (ev["role"], ev.get("task"), ev.get("feature"), ev.get("model"))
        # keep everything that isn't transcript-derived (task_move, qa_verdict,
        # state, git, and QA/stall problems); drop run_start/run_end (regenerated).
        if k in ("run_start", "run_end") or k in _TRANSCRIPT_KINDS:
            continue
        if k == "problem" and ev.get("code") in _TRANSCRIPT_PROBLEMS:
            continue
        preserved.append(ev)

    # Re-derive transcript events into a sink (transcript-timestamped).
    w = Watcher(project=project, run_id=run_id, slug=slug, orch_sid=orch_sid)
    w.rebuild = True
    w._sink = []
    for sid, (role, task, feature, model) in roster.items():
        if sid not in w.agents:
            w.agents[sid] = w._new_agent(role, task, feature, model)
    w._tail_agents()
    for sid, a in w.agents.items():
        if a["started"] and not a["stopped"]:
            w._emit_agent_stop(sid, a)
    derived = w._sink

    # agent_start was emitted before its first transcript line was read, so stamp
    # it with the earliest ts seen for that session.
    earliest: dict[str, str] = {}
    for ev in derived:
        s, t = ev.get("session"), ev.get("ts")
        if s and t and (s not in earliest or t < earliest[s]):
            earliest[s] = t
    for ev in derived:
        if ev.get("kind") == "agent_start" and not ev.get("ts"):
            ev["ts"] = earliest.get(ev.get("session"))

    # The original stream's bookends are immutable; prefer them for start/end so a
    # prior rebuild that clobbered run.json can't skew the wall clock.
    orig_start = next((e.get("ts") for e in orig if e.get("kind") == "run_start"), None)
    orig_end = next((e.get("ts") for e in reversed(orig) if e.get("kind") == "run_end"), None)
    started = orig_start or run_meta.get("started")
    ended = orig_end or run_meta.get("ended")
    if run_meta and (run_meta.get("started") != started or run_meta.get("ended") != ended):
        run_meta.update({"started": started, "ended": ended, "status": "ended"})
        try:
            (rd / "run.json").write_text(json.dumps(run_meta, indent=2), encoding="utf-8")
        except OSError:
            pass
    merged = preserved + derived
    # stable sort by ts; events without a ts fall back to start so they lead.
    merged.sort(key=lambda e: e.get("ts") or started or "")
    out = [{"kind": "run_start", "run": run_id, "interval": run_meta.get("interval"),
            "orch_session": orch_sid, "project": str(project), "ts": started}]
    out += merged
    out.append({"kind": "run_end", "run": run_id, "reason": "rebuild", "ts": ended})

    if not bak.exists():
        ep.rename(bak)
    lines = []
    for i, ev in enumerate(out):
        ts = ev.get("ts") or history.now_iso()
        ev = {**ev, "seq": i, "ts": ts}
        lines.append(json.dumps(ev, ensure_ascii=False, default=str))
    ep.write_text("\n".join(lines) + "\n", encoding="utf-8")
    history.build_index()  # recomputes summary.json from the merged stream
    sys.stderr.write(
        f"rebuild: {ep}\n  {len(roster)} transcripts, "
        f"{len(preserved)} preserved + {len(derived)} re-derived events\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m harness.history_watcher")
    ap.add_argument("--project", default=os.getcwd())
    ap.add_argument("--run-id", default=os.environ.get("HARNESS_RUN_ID"))
    ap.add_argument("--orch-sid", default=os.environ.get("HARNESS_ORCH_SID"))
    ap.add_argument("--poll", type=float, default=2.0, help="Seconds between ticks")
    ap.add_argument("--index-every", type=int, default=10, help="Rebuild index every N ticks")
    ap.add_argument("--once", action="store_true", help="Run a single tick and exit (testing)")
    ap.add_argument("--rebuild", action="store_true",
                    help="Re-derive an existing run's events from its transcripts and exit")
    args = ap.parse_args(argv)

    if not args.run_id:
        sys.stderr.write("history_watcher: --run-id (or $HARNESS_RUN_ID) is required\n")
        return 2

    project = Path(os.path.abspath(os.path.expanduser(args.project)))
    slug = history.project_slug(project)

    if args.rebuild:
        return rebuild_run(project, args.run_id)

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
