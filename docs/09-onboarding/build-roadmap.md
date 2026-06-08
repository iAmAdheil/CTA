# Harness Build Roadmap

What's left to build, grouped by area. Each box is atomic — one coherent file or wiring step. Pairs with `build-guide.md` (the how) and `build-progress.md` (the done).

---

## Skills — finish the MVH set

These are version-controlled in the harness repo at `skills/<name>/SKILL.md` and installed by `setup.sh` into each project's `.claude/skills/` (project-scoped, committed).

- [x] Write `/orchestrator` skill
- [x] Write `/task-breakdown` skill (real, AC-aware, dependency-shaping — replaced the test stub 2026-05-28)
- [x] Write `/worker` skill (real implementer, hybrid PR: `gh pr create` w/ remote else `local://` marker — replaced the test stub 2026-05-28)

## MVH end-to-end validation

Goal: prove the loop runs on a real (toy) project before adding integrations on top.
**First full run done 2026-05-28** with stub `/task-breakdown` + `/worker` skills — the procedure
is captured in `harness-test-playbook.md`. Gaps it surfaced are noted below.

- [x] Set up a toy project repo (run `setup.sh`, init git, write minimal `CLAUDE.md`)
- [x] Drop a sample spec into `docs/specs/` with `status: approved`
- [x] Run breakdown — verified task files appear, schema-valid
- [x] Spawn workers in tmux windows — both ran to `pr-opened` hands-off
- [x] Teardown + move to `tasks/<feature>/review/`; archive `done/` → verified
- [x] Patch wrappers/skills based on what broke — see resolved gaps below

Resolved during/after the test:
- [x] Folder-trust dialog blocked unattended workers → `tmux_manager.pretrust_path`
- [x] Tool-permission prompts hung unattended sessions → `--dangerously-skip-permissions` in wrappers
- [x] Task file invisible across the worktree boundary → ADR-001: control-plane state is gitignored,
  lives in the main worktree, accessed by absolute path (`setup.sh` + `/orchestrator` + `/worker` updated)
- [x] `python -m harness.*` not importable + no `run-orchestrator.sh` → ADR-002: dedicated `harness`
  conda env + editable install (`pyproject.toml`), invoked via `conda run -n harness`; `setup.sh`
  provisions the env; `run-orchestrator.sh` launcher added
- [x] Base branch hardcoded to `main` (broke `master` repos) → `tmux_manager.resolve_base_branch`
  auto-detects the repo's current branch; `HARNESS_BASE_BRANCH` / `--base-branch` override

Second full run done 2026-05-28 with the **real** `/task-breakdown` + `/worker` skills (string-utils
toy: 2-AC spec → 2 parallel tasks → real implementations, DoD passed, `local://` PR markers → review
→ done → archive). Both skills behaved correctly end-to-end.

Third run done 2026-05-28 validated the **race-free spawn**: `tmux_manager` now splits spawning into
`provision-worker` (worktree + idle placeholder window, no claude) and `launch-worker` (start claude),
and `/orchestrator` step 3 does all task-file/state bookkeeping *between* them. Re-ran the toy e2e
driving the new ordering: both workers reached `pr-opened` with orchestrator metadata
(`assigned_to`/`worktree`/`window`/`started`) and worker fields (`status`/`pr_url`) **coexisting** —
no clobber, no stranded task. `/worker` also tightened to write `status` last (PR fields first).

Still open (next):
- [ ] Run the real `/orchestrator` skill as its own session (all three tests drove the steps manually)

Resolved:
- [x] Orchestrator status-write race — fixed via the provision/launch split + bookkeeping-before-launch
  ordering (worker can't exist until the task file is fully set up). Disjoint field ownership
  (worker owns `status`/`pr_url`/`pr_number`; orchestrator owns the rest) kept as a stated invariant.

## Stage 5 — Notifications

- [ ] Write `harness/telegram_notifier.py`  
  POST to Telegram bot API, reads `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` from `.env`. Fallback to stdout if creds missing.
- [ ] Add Telegram bot credentials to `.env` (one-time setup via @BotFather)
- [ ] Update `/orchestrator` skill — emit Telegram calls at gate transitions (spawn, pr-opened, qa-verdict placeholder, merge)
- [ ] Write `harness/linear_client.py`  
  GraphQL functions: `create_epic`, `create_issue`, `move_issue`, `add_comment`, `attach_pr`.
- [ ] Add Linear API key to `.env`
- [ ] Update `/orchestrator` skill — emit Linear calls at the 5 gate transitions
- [ ] Update `/task-breakdown` skill — create Linear epic + issues at breakdown time

## Stage 6 — QA retry loop (issue #4)

Design fully locked 2026-06-01 — see the `qa-loop-verdict-design` memory + the
rewritten `feature-lifecycle.md` Stage 6 / `orchestrator-design.md`. QA is
**behavioral** (runs the feature, verdict from observed behaviour — NOT a diff review),
**async** (tracked agent, **reusing the task's one persistent worktree** on `task/<id>`), with a **one-retry**
ladder and **owner-based verdicts** (`looks-good` / `needs-changes` / `escalate`).

**Schema changes first** (the connective tissue everything else references):
- [ ] `schemas/task.schema.yaml` — add `expected_behavior` (rubric, written by task-breakdown),
  `branch` (empty at creation, worker fills), `qa_instructions` (→ recipe artifact),
  `qa_report` (→ report artifact), `failure_reason` (orch writes on escalate/block),
  `qa_run_attempts` (infra-retry counter, N=2). Add status **`qa-passed`**.
- [ ] Repurpose/rename `qa_failure_count` (old "escalate@2/stop@3" is dead — ladder is one retry).
  Keep `qa_out_of_scope_bugs` + `BUG-NNN` but mark **unused** (random-bugs subsystem deferred).

**A. QA verifier** — mirror the `run-advisor.sh` shape (`claude --print`, budget cap,
`--dangerously-skip-permissions`), but spawned **async** like a worker.
- [ ] Write `/qa-agent` skill — inputs: task `expected_behavior` (rubric), worker recipe
  (`qa_instructions`), project run/launch skill, the task's reused worktree. Two checks (execute recipe;
  recipe-vs-rubric). Writes `qa-report-<TASK-ID>.md` with `status:` WIP→verdict. Browser nav
  (Playwright/Stagehand) only for web apps w/ a runnable URL — skip for CLI/toy/MVH.
- [x] `tmux_manager` — variant that provisions a worktree on an **existing** branch (QA + fixer) — `--existing-branch`.
- [x] **Wire a real test environment + remove the interim auto-pass.** `/qa-agent` now drives features through real tooling — the **`playwright-cli`** skill for web/frontend, the **`bruno`** (`bru`) skill for backend APIs; CLI/library projects run the compile/unit-test DoD (no browser). The interim `HARNESS_QA_AUTOPASS` gate has been removed from the qa-agent skill and the test playbook. Two test hooks remain (mutually exclusive): **`HARNESS_QA_FORCE`** (QA-side) forces a verdict to drive the retry/escalate **routing** deterministically; **`HARNESS_QA_VERIFY`** (orchestrator-side, step 4S) injects one real behavioral defect into a worker's branch after `pr-opened` so **real** QA has to catch it and the Opus fix loop heal it — verifying QA itself works, not just the plumbing. The throwaway test project is now a small Express + React todo app (playbook §6) so behavioral QA has something real to drive.
- [N/A] ~~`scripts/run-qa.sh`~~ — not needed; QA is async (spawned via `tmux_manager` like a worker), not a sync `claude --print` wrapper.

**B. Orchestrator triggers QA** (async, tracked):
- [ ] At `pr-opened`/`pr-updated`: spawn QA into `active_workers` with `role: qa`, reusing the task's worktree.
- [ ] Update the `next-spec` advance gate — `pr-opened`/`qa-failed`/`pr-updated` are ACTIVE
  (hold the next spec); release only at `qa-passed`/`done`/`blocked`/`blocked-escalated`.

**C. Verdict routing + retry ladder** in `/orchestrator` (+ small `state_manager` helpers):
- [ ] `looks-good` → `qa-passed`, board → Human Review.
- [ ] `needs-changes` → `qa-failed`, spawn **Opus fixer** on the same task (fix mode: push to
  existing `branch`, set `pr-updated` — needs `/worker` fix-mode, see below). 2nd-pass re-QA:
  anything but `looks-good` → escalate.
- [ ] `escalate` → `blocked-escalated` + write `failure_reason` into the task file.
- [ ] QA-run died (window gone, review file stuck WIP) → retry QA spawn up to N=2 → escalate.
- [ ] Crash-safe teardown for the QA/fixer agents (move-card→kill-pane→remove-worker LAST; remove-worktree only when the task goes terminal, not at every handoff).

**`/worker` fix-mode** (gap #9):
- [ ] Worker fills `branch` + writes `qa-instructions-<TASK-ID>.md` on a fresh task.
- [ ] Worker detects fix mode from state (filled `branch` + `qa-failed`): push to existing branch,
  set `pr-updated`, NO second `gh pr create`.

**D. Review agent — DEFERRED** (adjacent, NOT part of the retry ladder):
- [ ] Write `/review-agent` skill (reads diff vs spec, posts comment via `gh pr review`) + `scripts/run-review.sh`.

**Separate (future) — random-bugs subsystem + merge/archival agent:** see the dedicated
sections below; out of scope for the QA retry loop.

## Stage 7 — Ops agents

- [ ] Write `/blocker-resolver` skill (Opus model, budget-capped)  
  Inputs: BLOCKER text, spec, ADRs, decisions.md. Output: a decision written to `decisions.md`.
- [ ] Update `/orchestrator` skill — step 6 detects `status: blocked`, invokes blocker-resolver, nudges worker via `tmux_manager nudge`
- [ ] Write `/doc-closeout` skill  
  Post-merge: update `docs/api/`, stub `docs/runbooks/`, update `docs/architecture/data-model.md` if schema changed, archive `docs/active-features/<feature>/`.
- [ ] **Merge-detection / archival agent (separate from the QA loop, decided 2026-06-01)**  
  The QA loop ends at `qa-passed` (card in Human Review, PR open, awaiting the human's manual merge — no auto-merge). A **separate agent** owns the rest: detect that the human merged the PR (e.g. `gh pr view --json state,mergedAt`), set the task `status: done`, and trigger the archive (orchestrator step 5 then `mv`s to `tasks/<feature>/done/`). The merge is the signal, pulled from GitHub — not a manual status flip. The orchestrator does NOT do this inline.
- [ ] Update `/orchestrator` skill — step 5 calls doc-closeout after archiving a done task
- [ ] Write `/backlog-triage` skill (Opus, scheduled)  
  Reads all backlog tasks + Linear state. Reorders by priority, posts a daily plan.
- [ ] Decide scheduling — cron at 6am, `/schedule`, or `/loop` daily

## Random-bugs subsystem (FUTURE — separate from the QA retry loop)

Deferred decision (2026-06-01): out-of-scope bugs that the QA agent stumbles on while
exercising a feature are **explicitly NOT handled by the QA retry loop**. The QA agent
**ignores** them for verdict purposes — only in-rubric problems drive needs-changes/escalate.
A standalone subsystem will own "random bugs" later:

- [ ] A **separate bugs board** that the **user curates / adds to** (the `docs/_kanban/bugs.md`
  skeleton already exists with Reported→Triaged→In Progress→QA→Human Review→Blocked→Fixed).
- [ ] QA (or the user) **files each bug as a Linear issue** with **screenshots / screen recordings**
  — richer than a text task file, because these are visual/behavioral defects.
- [ ] A **second, separate fleet of agents** that works these random bugs — distinct from the
  spec→task→worker→QA pipeline, so unspecced bugs never hijack the serial, priority-ranked,
  spec-driven main loop.
- [ ] Decide the QA→Linear filing mechanism (form? `/qa-agent` opens the issue directly? attaches
  the recording from its browser session?).

Until then: the schema's `qa_out_of_scope_bugs` field + `BUG-NNN` id reservation stay **unused**;
the `qa-report.md` "Out-of-Scope Findings" section is dropped from the QA agent's job for now.

## Stage 8 — Polish

- [ ] Write `scripts/monitor.py`  
  Rich-based terminal dashboard. Reads `orchestrator-state.yaml` every 5s, shows active workers, queue, recent actions, rough cost estimate.
- [ ] Set up remote control  
  Termius SSH profile, mobile-tuned tmux keys, two-way Telegram bot commands (`/status`, `/pause`, `/resume`).
- [ ] Replace polling with file-watcher  
  `watchdog`-based driver that invokes `/orchestrator` only on relevant file changes. Lighter than `/loop` every N seconds.

## Docs + cleanup

- [ ] Rewrite `build-guide.md` Stage 2  
  Original describes a Python `orchestrator.py` with `while True`. Actual design is LLM agent (skill) + wrappers + external driver.
- [x] Remove dead `agent-prompts/` symlink from `setup.sh`  
  Base agent skills are now version-controlled in the repo's `skills/` and installed by `setup.sh` into `.claude/skills/`; `setup.sh` also removes any stale `agent-prompts` symlink instead of creating one.
- [ ] Add "running the harness" section to `build-guide.md`  
  Covers `/orchestrator` invocation patterns: manual, `/loop`, cron, file-watcher.
- [ ] Run `skill-creator` description-optimization on each finished skill  
  Improves triggering accuracy by running held-out eval queries.
