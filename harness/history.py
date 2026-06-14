"""Harness history store — the debugging/visibility spine.

Every harness *run* (one `harness-up.sh` launch → teardown) is recorded as an
append-only, **mechanically generated** event stream the user can browse after
the fact. Nothing here is written by an LLM: events come from tailing agent
transcripts, diffing the control plane (task files / state / boards), and
polling git. This module owns the on-disk layout + the event-append + index
primitives; `harness.history_watcher` produces the events.

## Layout (all under $HARNESS_HISTORY_DIR, default ~/.agent-harness-history)

    <history-root>/
      index.json                         # roll-up across every project+run (the viewer's landing data)
      _viewer/                           # viewer assets, copied in by harness-history.sh at serve time
      <project-slug>/
        project.json                     # path, friendly name, repo, first/last seen
        runs/
          <run-id>/                      # run-id = <launch-ts>-<short-uuid>; ONE harness launch == one "session"
            run.json                     # started/ended/status/interval/orchestrator session id
            events.ndjson                # the mechanical spine — one JSON event per line, in order
            snapshots/                   # point-in-time copies of state.yaml / boards as they change

Project scope = the project root path (slugified). Session scope = the run id,
minted at launch and inherited by the watcher + the pinned orchestrator session.

## Event envelope (events.ndjson, one per line)

    {"seq": int, "ts": ISO-8601-Z, "kind": str, "run": str, ...kind-specific}

`kind` is one of: run_start, run_end, agent_start, agent_stop, tool, error,
task_move, qa_verdict, git, state. Kind-specific fields are documented at each
emit site in history_watcher. Events are never mutated, only appended — the
file IS the timeline.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


# ---------------------------------------------------------------------------
# Paths / identity
# ---------------------------------------------------------------------------

def history_root() -> Path:
    """Root of the central history store. Override with $HARNESS_HISTORY_DIR."""
    env = os.environ.get("HARNESS_HISTORY_DIR")
    return Path(env).expanduser() if env else Path.home() / ".agent-harness-history"


def project_slug(path: str | os.PathLike) -> str:
    """Filesystem-safe, stable key for a project, derived from its root path.

    Mirrors the spirit of Claude Code's own project-dir encoding (path with
    separators flattened) so the key is recognizable, but we only require it to
    be stable + collision-free, so any non-alphanumeric run collapses to '-'.
    """
    p = os.path.abspath(os.path.expanduser(str(path)))
    return re.sub(r"[^A-Za-z0-9]+", "-", p).strip("-") or "root"


def run_dir(slug: str, run_id: str) -> Path:
    return history_root() / slug / "runs" / run_id


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def new_run_id() -> str:
    """A run id: sortable launch timestamp + short random suffix for uniqueness."""
    return datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


def _git_remote(project: Path) -> str | None:
    try:
        r = subprocess.run(
            ["git", "-C", str(project), "remote", "get-url", "origin"],
            text=True, capture_output=True,
        )
        url = r.stdout.strip()
        return url or None
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Event append (the single low-level write primitive)
# ---------------------------------------------------------------------------

def _events_path(rd: Path) -> Path:
    return rd / "events.ndjson"


def _next_seq(rd: Path) -> int:
    """Next sequence number = current line count of events.ndjson.

    The watcher is the single live writer and caches this in memory; this is the
    cold-start / out-of-band-append path, so a full count is fine.
    """
    p = _events_path(rd)
    if not p.exists():
        return 0
    n = 0
    with p.open("r", encoding="utf-8") as f:
        for _ in f:
            n += 1
    return n


def append_event(rd: Path, event: dict[str, Any], *, seq: int | None = None) -> int:
    """Append one event to a run's events.ndjson. Returns the assigned seq.

    Fills `seq`/`ts` if absent. Append-mode write + flush keeps the file a
    valid, never-half-written ndjson even across a crash (worst case: a process
    death loses the in-flight line, never corrupts prior ones).
    """
    rd.mkdir(parents=True, exist_ok=True)
    if seq is None:
        seq = event.get("seq")
    if seq is None:
        seq = _next_seq(rd)
    event = {"seq": seq, "ts": event.get("ts") or now_iso(), **event}
    event["seq"] = seq  # ensure it wins even if caller passed one in the dict
    line = json.dumps(event, ensure_ascii=False, default=str)
    with _events_path(rd).open("a", encoding="utf-8") as f:
        f.write(line + "\n")
        f.flush()
    return seq


# ---------------------------------------------------------------------------
# Run lifecycle
# ---------------------------------------------------------------------------

def mint_run(
    *,
    project: str | os.PathLike,
    run_id: str | None = None,
    orch_session: str | None = None,
    interval: str | None = None,
) -> dict[str, Any]:
    """Create the run directory + run.json, register the project, stamp run_start.

    Called once by harness-up.sh at launch, BEFORE the orchestrator/watcher
    start, so the run exists and is seq 0 by the time anything else writes.
    Returns the run.json dict (includes the resolved run_id + paths).
    """
    project = Path(os.path.abspath(os.path.expanduser(str(project))))
    slug = project_slug(project)
    run_id = run_id or new_run_id()
    rd = run_dir(slug, run_id)
    rd.mkdir(parents=True, exist_ok=True)
    (rd / "snapshots").mkdir(exist_ok=True)

    # Register / refresh the project record.
    proj_file = history_root() / slug / "project.json"
    if proj_file.exists():
        try:
            proj = json.loads(proj_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            proj = {}
    else:
        proj = {}
    proj.update({
        "slug": slug,
        "path": str(project),
        "name": project.name,
        "repo": _git_remote(project),
    })
    proj.setdefault("first_seen", now_iso())
    proj["last_seen"] = now_iso()
    proj_file.parent.mkdir(parents=True, exist_ok=True)
    proj_file.write_text(json.dumps(proj, indent=2), encoding="utf-8")

    run = {
        "run": run_id,
        "project_slug": slug,
        "project_path": str(project),
        "project_name": project.name,
        "started": now_iso(),
        "ended": None,
        "status": "running",
        "interval": interval,
        "orch_session": orch_session,
        "host": socket.gethostname(),
    }
    (rd / "run.json").write_text(json.dumps(run, indent=2), encoding="utf-8")

    append_event(rd, {
        "kind": "run_start", "run": run_id,
        "interval": interval, "orch_session": orch_session,
        "project": str(project),
    }, seq=0)
    return run


def end_run(*, slug: str, run_id: str, reason: str = "teardown") -> None:
    """Stamp run.json ended + status and append the terminal run_end event."""
    rd = run_dir(slug, run_id)
    rf = rd / "run.json"
    if rf.exists():
        try:
            run = json.loads(rf.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            run = {"run": run_id}
        run["ended"] = now_iso()
        run["status"] = "ended"
        rf.write_text(json.dumps(run, indent=2), encoding="utf-8")
    append_event(rd, {"kind": "run_end", "run": run_id, "reason": reason})


# ---------------------------------------------------------------------------
# Index (roll-up the viewer's landing page reads)
# ---------------------------------------------------------------------------

# kinds we surface as headline counts on each run card
_HEADLINE_KINDS = ("tool", "error", "task_move", "qa_verdict", "git", "agent_start")


def _summarize_run(rd: Path, run: dict[str, Any]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    sessions: set[str] = set()
    features: set[str] = set()
    last_ts = run.get("started")
    total = 0
    ep = _events_path(rd)
    if ep.exists():
        with ep.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                total += 1
                k = ev.get("kind", "?")
                counts[k] = counts.get(k, 0) + 1
                if ev.get("session"):
                    sessions.add(ev["session"])
                if ev.get("feature"):
                    features.add(ev["feature"])
                if ev.get("ts"):
                    last_ts = ev["ts"]
    return {
        "run": run.get("run"),
        "started": run.get("started"),
        "ended": run.get("ended"),
        "status": run.get("status"),
        "interval": run.get("interval"),
        "last_ts": last_ts,
        "events": total,
        "agents": len(sessions),
        "errors": counts.get("error", 0),
        "features": sorted(features),
        "counts": {k: counts.get(k, 0) for k in _HEADLINE_KINDS if counts.get(k)},
    }


def build_index() -> dict[str, Any]:
    """Scan the whole history store and (re)write index.json. Returns it."""
    root = history_root()
    projects: list[dict[str, Any]] = []
    if root.exists():
        for slug_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            if slug_dir.name.startswith("_"):  # skip _viewer etc.
                continue
            proj_file = slug_dir / "project.json"
            if not proj_file.exists():
                continue
            try:
                proj = json.loads(proj_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            runs: list[dict[str, Any]] = []
            runs_dir = slug_dir / "runs"
            if runs_dir.exists():
                for rd in runs_dir.iterdir():
                    rf = rd / "run.json"
                    if not rf.exists():
                        continue
                    try:
                        run = json.loads(rf.read_text(encoding="utf-8"))
                    except (json.JSONDecodeError, OSError):
                        continue
                    runs.append(_summarize_run(rd, run))
            runs.sort(key=lambda r: r.get("started") or "", reverse=True)
            proj["runs"] = runs
            proj["run_count"] = len(runs)
            proj["last_run"] = runs[0]["started"] if runs else None
            projects.append(proj)
    projects.sort(key=lambda p: p.get("last_run") or "", reverse=True)
    index = {"generated": now_iso(), "projects": projects}
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
    return index


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m harness.history",
        description="harness history store: run lifecycle + event append + index",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_mint = sub.add_parser("mint-run", help="Create a run dir + run.json and stamp run_start")
    p_mint.add_argument("--project", default=os.getcwd(), help="Project root (default: cwd)")
    p_mint.add_argument("--run-id", default=None, help="Run id (default: generated)")
    p_mint.add_argument("--orch-sid", default=None, help="Pinned orchestrator --session-id UUID")
    p_mint.add_argument("--interval", default=None, help="Orchestrator loop interval (for the record)")

    p_end = sub.add_parser("end-run", help="Stamp run end + run_end event")
    p_end.add_argument("--slug", required=True)
    p_end.add_argument("--run-id", required=True)
    p_end.add_argument("--reason", default="teardown")

    p_app = sub.add_parser("append", help="Append one event (JSON on --event or stdin)")
    p_app.add_argument("--slug", required=True)
    p_app.add_argument("--run-id", required=True)
    p_app.add_argument("--event", default=None, help="Event as a JSON object; reads stdin if omitted")

    sub.add_parser("build-index", help="Rescan the store and rewrite index.json")

    p_paths = sub.add_parser("paths", help="Print resolved paths for a project (debug)")
    p_paths.add_argument("--project", default=os.getcwd())

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.cmd == "mint-run":
        run = mint_run(
            project=args.project, run_id=args.run_id,
            orch_session=args.orch_sid, interval=args.interval,
        )
        json.dump(run, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    if args.cmd == "end-run":
        end_run(slug=args.slug, run_id=args.run_id, reason=args.reason)
        return 0

    if args.cmd == "append":
        raw = args.event if args.event is not None else sys.stdin.read()
        ev = json.loads(raw)
        append_event(run_dir(args.slug, args.run_id), ev)
        return 0

    if args.cmd == "build-index":
        idx = build_index()
        sys.stderr.write(
            f"index: {len(idx['projects'])} project(s) -> {history_root()/'index.json'}\n"
        )
        return 0

    if args.cmd == "paths":
        slug = project_slug(args.project)
        print(json.dumps({
            "history_root": str(history_root()),
            "project_slug": slug,
            "project_dir": str(history_root() / slug),
        }, indent=2))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
