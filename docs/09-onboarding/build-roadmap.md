# Harness Build Roadmap

What's left to build, grouped by area. Each box is atomic — one coherent file or wiring step. Pairs with `build-guide.md` (the how) and `build-progress.md` (the done).

---

## Skills — finish the MVH set

These are written in the dedicated skill-creation session and live in `~/.claude/skills/<name>/SKILL.md`.

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
- [x] Teardown + move to `tasks/review/`; archive `done/` → verified
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

Still open (next):
- [ ] Run the real `/orchestrator` skill as its own session (test drove the steps manually)

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

## Stage 6 — QA + Review agents

- [ ] Install Playwright + Stagehand in target projects (`npm install …`)
- [ ] Write `/review-agent` skill  
  Reads diff vs spec, posts comment via `gh pr review`. Simpler than QA — build first.
- [ ] Write `scripts/run-review.sh`
- [ ] Write `/qa-agent` skill  
  Pass 1: test suite mapped to ACs. Pass 2: Stagehand browser nav per AC. Writes `qa-report.md`.
- [ ] Write `scripts/run-qa.sh`
- [ ] Update `/orchestrator` skill — at `pr-opened`/`pr-updated`, run review + qa in parallel
- [ ] Update `/orchestrator` skill — read `qa-report.md` verdict and route (PASS / CONDITIONAL / FAIL)
- [ ] Update `/orchestrator` skill — implement `qa_failure_count` escalation (1st fail re-queue, 2nd invoke Opus, 3rd hard stop)

## Stage 7 — Ops agents

- [ ] Write `/blocker-resolver` skill (Opus model, budget-capped)  
  Inputs: BLOCKER text, spec, ADRs, decisions.md. Output: a decision written to `decisions.md`.
- [ ] Update `/orchestrator` skill — step 6 detects `status: blocked`, invokes blocker-resolver, nudges worker via `tmux_manager nudge`
- [ ] Write `/doc-closeout` skill  
  Post-merge: update `docs/api/`, stub `docs/runbooks/`, update `docs/architecture/data-model.md` if schema changed, archive `docs/active-features/<feature>/`.
- [ ] Update `/orchestrator` skill — step 5 calls doc-closeout after archiving a done task
- [ ] Write `/backlog-triage` skill (Opus, scheduled)  
  Reads all backlog tasks + Linear state. Reorders by priority, posts a daily plan.
- [ ] Decide scheduling — cron at 6am, `/schedule`, or `/loop` daily

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
  Skills moved to `~/.claude/skills/` per the location decision; `setup.sh` now removes any stale symlink instead of creating one.
- [ ] Add "running the harness" section to `build-guide.md`  
  Covers `/orchestrator` invocation patterns: manual, `/loop`, cron, file-watcher.
- [ ] Run `skill-creator` description-optimization on each finished skill  
  Improves triggering accuracy by running held-out eval queries.
