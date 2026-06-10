# Harness TODO

Running tracker of known issues and follow-ups. Most urgent at the top.

---

## 🔴 CRITICAL — A worker that dies before writing a terminal status strands its task at `in-progress` forever

**Status:** open · needs an approach
**Filed:** 2026-06-10
**Surfaced by:** portfolio e2e — TASK-003's worker pane (`%8`) died mid-task without ever writing a terminal `status:` (still `in-progress`, no commits). The orchestrator's reconcile (step 7) correctly dropped the dead agent from `active_workers` and kept the worktree, but then **refused to re-spawn**: "Harness will not re-spawn an in-progress task — needs human inspection to re-spawn or escalate." So the task sat at `in-progress` with no agent and no progress, indefinitely.

### Why
The orchestrator only spawns workers for `backlog` tasks (step 3). An `in-progress` task is assumed to be owned by a live worker. When that worker dies prematurely (crash, OOM, session closed, budget) **before** flipping status to `pr-opened`/`blocked`, nothing owns the task and nothing re-spawns it. There's no retry/heal path for "active task whose agent vanished."

### Impact
- A single crashed worker permanently stalls its task (and any dependents) with no signal beyond a buried `last_action` note. Looks identical to the other CRITICAL (a silent stall), but the cause is different — here the agent is *gone*, not idling.
- In this run it cost the whole feature's completion until manually reset.

### Manual recovery that worked (the stopgap)
Reset the task to a clean `backlog` state and let the orchestrator re-spawn: remove the stale worktree + (empty) branch, move the file back to `backlog/`, null out `assigned_to`/`pane`/`worktree`/`started`, set `status: backlog`. Next cycle spawned a fresh worker that completed and passed QA.

### Candidate approaches (decide later)
- **Reconcile re-queues orphaned active tasks.** When step 7 finds an `active_workers` entry whose pane is dead AND the task is still `in-progress` (never reached a terminal status), treat it as a crashed attempt: bump an attempt counter, and either re-spawn (move back to `backlog` / re-provision into the retained worktree) or, after N attempts, set `blocked-escalated` for a human. Don't leave it parked silently.
- **Distinguish "intentionally exited" from "crashed."** A worker that exits cleanly always writes a terminal status first; a pane that dies with status still `in-progress` is a crash. Key the heal path off that.
- Shares a fix surface with the issue below (agent lifecycle / self-exit) — design them together. **Note:** the default `/loop` shipped for that issue (approach B, 2026-06-11) does **not** help here — the loop re-runs reconcile (step 7) every interval, but reconcile still refuses to re-spawn an `in-progress` orphan, so the loop merely re-confirms the stall. This still needs the reconcile-re-queue heal path; #1 remains 🔴.

### Notes
- Root crash cause for `%8` was not captured (pane already gone; no `-p` transcript). Logging worker stdout/transcripts to a file would make this diagnosable — ties into `orch-run.sh`/`_fmt_stream.py` on the `feature/headed-tmux-orchestrator` branch.

---

## 🟡 MOSTLY RESOLVED — Finished agents don't self-exit; pipeline stalled without an external loop driver

**Status:** cadence half RESOLVED 2026-06-11 (approach **B** shipped) · root-cause self-exit (**A**) still open, now minor
**Filed:** 2026-06-10
**Update (2026-06-11):** `scripts/harness-up.sh` now defaults `HARNESS_ORCH_LOOP=60s`, so the standard headed launch self-drives `/loop 60s /orchestrator` out of the box — approach **B**. The "no driver → looks hung forever" failure mode is gone: a finished worker is now reaped within ≤1 loop interval. Validated on the `harness-dummy` todo-app run — TASK-001 auto-progressed build→PR→QA→merge→`done` with no manual ticking. What remains is cosmetic: a finished agent still doesn't self-terminate (approach **A**), so it holds its pane + concurrency slot for up to one interval before the next cycle reaps it. Downgraded 🔴→🟡.
**Surfaced by:** portfolio e2e — orchestrator broke down a spec and spawned the TASK-001 worker, the worker completed successfully (`status: pr-opened`, code committed), but the whole thing *looked hung* and went no further.

### Symptom
After a worker finishes its task it does **not** exit — its Claude session returns to the interactive `❯` prompt and sits there idle (`SNs+`, 0% CPU, sleeping on a terminal read). It keeps holding its tmux pane and its `active_workers` slot. Meanwhile nothing advances the task to QA. To an observer it is indistinguishable from a hang.

### Root cause (two compounding facts)
1. **Agents are launched as interactive (headed) Claude sessions** — `claude … <prompt>`, not `claude -p <prompt>` (see `harness/_spawn_worker.sh` / `launch_worker` in `harness/tmux_manager.py`). Interactive sessions finish the initial prompt and then wait for the next human turn forever; they have no `/exit` and no `-p` to terminate on completion. We launch them headed on purpose so the pane stays attachable/visible.
2. **The orchestrator is single-shot by design** — `skills/orchestrator/SKILL.md` line 8 / 435: "You do not loop, sleep, or poll … Then exit. Do not loop." Reaping a `pr-opened` worker's pane and spawning QA is the *next* cycle's job (step 4A/4B). Cadence is delegated to an external driver (`/loop`, cron, file-watcher).

So: a completed worker is immortal until a *subsequent* orchestrator cycle reaps it, and subsequent cycles only happen if an external driver fires them. **If no loop driver is running, the first finished worker permanently masquerades as a hang and the pipeline never advances.** (Past end-to-end runs "just worked" only because they were wrapped in `/loop` — N short cycles, not one waiting cycle.)

### Impact
- Silent stall: looks like a crash/hang, is actually "done, waiting for a driver that isn't there." Easy to misdiagnose (we did).
- Wasted resources: a finished agent holds a tmux pane + a concurrency slot doing nothing until reaped.
- Fragile UX: correct operation depends on the user *remembering* to wrap the orchestrator in `/loop`. A bare `claude /orchestrator` or `claude -p /orchestrator` does one cycle and appears to "get stuck."

### Candidate approaches (decide later — not yet chosen)
- **A. Agents self-terminate on terminal status.** After writing `status: pr-opened`/`blocked`/etc., the agent should quit (e.g. end the session / `/exit`), so its pane dies and the orchestrator's reconcile (step 7) + teardown sees a dead pane and frees the slot. Keeps panes headed/attachable *while working*, but they don't linger. Need to confirm a clean programmatic exit path for a headed session, or run workers via `-p` and tee their transcript to a log/pane instead.
- **B. Ship a default cadence driver. ✅ DONE (2026-06-11).** `scripts/harness-up.sh` now defaults `HARNESS_ORCH_LOOP=60s` — the standard headed launch runs `/loop 60s /orchestrator`, self-advancing out of the box (override with `HARNESS_ORCH_LOOP=30s`, opt out with `=off` for a single cycle). Runbook `docs/05-runbooks/running-the-harness.md` documents that a single `/orchestrator` is one tick.
- **C. Event-driven kick.** A file-watcher on task files / `qa-report-*.md` status lines fires an orchestrator cycle on change, so completion → reap/QA happens promptly without fixed-interval polling.
- **D. Liveness signal.** Have the orchestrator (or a wrapper) detect "agents present but no driver advancing them" and surface it (warn / auto-tick) rather than failing silently.

Real fix was **A + B together**: **B is now shipped** (default self-driving loop → "looks hung" can't happen from a normal start). **A** (finished agents free their own pane/slot) remains as a minor follow-up — without it a done agent wastes a slot for up to one loop interval, but it no longer stalls the pipeline.

### Notes
- Related observability tooling added on branch `feature/headed-tmux-orchestrator`: `scripts/harness-up.sh` (headed-in-tmux launch, has `HARNESS_ORCH_LOOP`), `scripts/orch-watch.sh`, `scripts/orch-run.sh`, `scripts/_fmt_stream.py`.
- Evidence from the incident: worker pid was `SNs+` / 0% CPU at an empty `❯`; task branch had commit `935e33a`, `TASK-001.yaml` was `status: pr-opened`, worktree clean — i.e. genuinely done, just not reaped.
