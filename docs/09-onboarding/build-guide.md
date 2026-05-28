# Harness Build Guide

How to build the harness from scratch, in order. Each stage has a clear "done when" test — don't move to the next stage until it passes.

For the full description of each component, see [[harness-projects]].

---

## Before You Start

Have these in place:

- [x] tmux installed — `brew install tmux`
- [x] Python 3.11+ — `python3 --version`
- [x] Claude Code CLI installed and authenticated — `claude --version`
- [x] GitHub CLI installed and authenticated — `gh auth status`
- [ ] A test project repo (even an empty one) to run the harness against

---

## Stage 1 — Repo & Foundation

**What you're building:** The git repo for the harness itself, the directory structure it installs into projects, and all the file format definitions.

### Steps

**1. Initialize the harness repo**
```bash
cd ~/agent-harness
git init
echo ".env\n__pycache__/\n*.pyc\n.DS_Store\norchestrator-state.yaml" > .gitignore
```

**2. Create the harness directory layout**
```
agent-harness/
├── harness/           ← Python modules (orchestrator, managers, clients)
├── agent-prompts/     ← Prompt templates for each agent type
├── templates/         ← File templates (task, progress, qa-report)
├── schemas/           ← YAML schemas for task and state files
├── scripts/           ← setup.sh, monitor.sh, run-orchestrator.sh
└── docs/              ← This vault (already exists)
```

**3. Write the file templates** in `templates/`:
- `task.md` — all frontmatter fields including `references: []` and `qa_failure_count: 0`
- `progress.md` — log, blockers, decisions sections
- `qa-report.md` — AC table, pre-existing failures, manual nav test, verdict

**4. Write the schemas** in `schemas/`:
- `task.schema.yaml` — enumerates all valid `status` values, required fields
- `orchestrator-state.schema.yaml` — active_workers shape, queue, max_workers

**5. Write `scripts/setup.sh`**
The script a developer runs once in a new project repo. Creates:
- `tasks/backlog/`, `in-progress/`, `review/`, `done/`
- `docs/specs/`, `docs/adrs/`, `docs/active-features/`
- `agent-prompts/` symlink → `~/agent-harness/agent-prompts/`
- Starter `CLAUDE.md` with section placeholders
- Empty `orchestrator-state.yaml`
- `.gitignore` entries for `orchestrator-state.yaml` and `.env`

**Done when:**
- [ ] `bash scripts/setup.sh` runs on a clean repo and produces the correct structure
- [ ] All template files exist and have the correct frontmatter fields
- [ ] `git status` in the harness repo is clean

---

## Stage 2 — Core Loop

**What you're building:** The three Python modules that make the orchestrator work, wired together into a loop. No integrations yet — everything logs to stdout instead of calling Telegram or Linear.

### Steps

**1. Build `harness/state_manager.py`**

Functions: `read_state()`, `write_state(state)`, `add_worker(...)`, `remove_worker(task_id)`, `get_runnable_tasks(all_tasks, done_task_ids)`.

Atomic writes: write to a `.tmp` file, then `os.rename()` — prevents corruption if the orchestrator crashes mid-write.

**2. Build `harness/tmux_manager.py`**

Functions: `create_window(name)`, `send_to_window(window_id, text)`, `kill_window(window_id)`, `create_worktree(branch, path)`, `remove_worktree(path)`.

All functions are thin wrappers over `subprocess.run(['tmux', ...])`. Test each function manually in a Python REPL before wiring up.

**3. Build `harness/orchestrator.py`**

The main loop. Structure:

```python
while True:
    state = read_state()
    tasks = read_all_task_files()

    # 1. Check for new approved specs → task breakdown
    # 2. Compute runnable tasks (deps satisfied, slots available)
    # 3. Spawn workers for runnable tasks
    # 4. Check progress.md files for BLOCKER entries
    # 5. Check task statuses for pr-opened, pr-updated, done
    # 6. Trigger QA on pr-opened/pr-updated
    # 7. Handle qa-report.md verdicts
    # 8. Handle merges

    write_state(state)
    time.sleep(10)
```

At this stage, replace every Telegram call with `print(f"[TELEGRAM] {message}")` and every Linear call with `print(f"[LINEAR] {action}")`. You want to verify the logic before adding external dependencies.

**4. Write `scripts/run-orchestrator.sh`**

```bash
#!/bin/bash
cd $PROJECT_ROOT
python ~/agent-harness/harness/orchestrator.py
```

**Done when:**
- [ ] Manually create `tasks/backlog/TASK-001.yaml` with `status: backlog`, `depends_on: []`
- [ ] Start the orchestrator — it detects TASK-001 and logs `[TELEGRAM] 🔧 TASK-001 started on window:1`
- [ ] A new tmux window opens with a Claude Code session
- [ ] Manually set `status: pr-opened` in the task file — orchestrator logs `[TELEGRAM] 🔀 PR opened`
- [ ] Manually set `status: done` — orchestrator moves the file to `tasks/done/` and logs merge

---

## Stage 3 — Task Breakdown Agent

**What you're building:** The prompt template that decomposes an approved spec into task files, and the orchestrator trigger that invokes it.

### Steps

**1. Write `agent-prompts/task-breakdown.md`**

The prompt is handed to a Claude Code session. It must:
- Read the spec file completely
- Read all existing ADRs
- Read CLAUDE.md for codebase conventions
- Output N task YAML files (one `Write` tool call per task)
- Each task includes: `depends_on`, `blocks`, `can_parallelize_with`, `suggested_files_to_read`, `references` (if any misc docs apply)
- Not output anything else

Key constraint to include in the prompt: *"Each task must be completable in a single agent session of 30–90 minutes. If a task would be longer, split it."*

**2. Write `scripts/run-breakdown.sh`**

Invoked by the orchestrator when a new approved spec is detected:
```bash
claude --print "$(cat ~/agent-harness/agent-prompts/task-breakdown.md)" \
       --context "spec: $(cat $SPEC_PATH)" \
       --context "adrs: $(cat docs/adrs/*.md)" \
       --context "claude_md: $(cat CLAUDE.md)"
```

**3. Wire into orchestrator**

When `status: approved` detected on a new spec file → call `run-breakdown.sh` → wait for task files to appear in `tasks/backlog/`.

**Done when:**
- [ ] Write a simple 3-AC spec in `docs/specs/`
- [ ] Set `status: approved`
- [ ] Orchestrator invokes task breakdown
- [ ] 2–3 task files appear in `tasks/backlog/` with correct frontmatter
- [ ] Dependency fields are coherent (no circular deps, correct blocking relationships)

---

## Stage 4 — Worker Agent Protocol

**What you're building:** The prompt template workers receive at session start, and the signaling conventions they use to communicate back to the orchestrator.

### Steps

**1. Write `agent-prompts/worker.md`**

This is the instruction set injected at the start of every worker session. Must cover:

- **Context loading:** "Your first action: read the task file, then the spec, then CLAUDE.md, then all referenced ADRs, then all files listed under `references:` in the task file. Do not write any code until you have read all of these."
- **Progress logging:** Write a timestamped entry to `progress.md` every time you complete a meaningful step. Format: `HH:MM — [what you did]`
- **Blocker protocol:** "If you hit something the spec doesn't cover that would affect the interface or architecture, STOP. Write to the `## Blockers` section of `progress.md` with the format: `BLOCKER: [precise description of the gap and why it matters]`. Do not guess."
- **Self-review checklist:** Before signalling done, run lint, typecheck, full test suite. Note any pre-existing failures.
- **PR description:** Generated from the spec's acceptance criteria. Each AC item as a checklist item in the PR body.
- **Signalling done:** Write `status: pr-opened` to the task file's frontmatter after opening the PR.
- **Fix mode:** When re-invoked with a QA report, "Fix only what the QA report flagged. Do not touch unrelated code."

**2. Update the orchestrator's worker spawn logic**

Build the handoff prompt dynamically from:
```
worker.md
+ task file contents
+ spec file contents
+ CLAUDE.md
+ all referenced ADR files
+ all files listed in task references:
```

**Done when:**
- [ ] Run a worker manually: hand it a real task file and the worker.md prompt
- [ ] Verify it reads all context files before writing code
- [ ] Verify it writes `progress.md` entries during execution
- [ ] Verify it writes a BLOCKER entry when you give it a spec with a deliberate gap
- [ ] Verify it writes `status: pr-opened` after opening a PR
- [ ] Orchestrator detects the status change and responds correctly

---

## Stage 5 — Integrations

**What you're building:** Telegram notifications and Linear task tracking, wired into the orchestrator at gate transitions. Add one at a time.

### Telegram first

**1. One-time setup**
- Create a bot via @BotFather → copy the token
- Send a message to your new bot → get your chat ID via `https://api.telegram.org/bot{TOKEN}/getUpdates`
- Add both to `.env`: `TELEGRAM_BOT_TOKEN=` and `TELEGRAM_CHAT_ID=`

**2. Write `harness/telegram_notifier.py`**

Single function: `send(message: str)`. POST to the Telegram Bot API. The module reads credentials from `.env` at import time — if not set, falls back to `print()` so the orchestrator keeps running.

**3. Replace all `print("[TELEGRAM] ...")` calls in the orchestrator** with `telegram_notifier.send(...)`.

**Done when:**
- [ ] Trigger a task start → receive Telegram message on your phone
- [ ] Kill `TELEGRAM_BOT_TOKEN` from `.env` → orchestrator still runs, logs to stdout

### Linear second

**1. One-time setup**
- Create a Linear personal API key (Settings → API → Personal API keys)
- Add to `.env`: `LINEAR_API_KEY=`

**2. Write `harness/linear_client.py`**

Functions: `create_epic`, `create_issue`, `move_issue`, `add_comment`, `attach_pr`. All use the Linear GraphQL API. Same fallback pattern: if key not set, log and return `None`.

**3. Wire into orchestrator** at the 5 gate transitions:
- Task breakdown completes → create epic + issues
- Worker starts → move issue to "In Progress"
- PR opened → move to "In Review", attach PR link
- QA passes → post qa-report summary as comment
- Merged → move to "Done"

**Done when:**
- [ ] Run a full task cycle end-to-end
- [ ] Verify the Linear issue moves through all statuses correctly
- [ ] Verify the QA report comment appears on the Linear issue
- [ ] Kill `LINEAR_API_KEY` → orchestrator continues without error

---

## Stage 6 — QA and Review Agents

**What you're building:** The two agents that run after every PR opens. Review agent is simpler — build it first.

### Review Agent

**1. Write `agent-prompts/review-agent.md`**

Checks:
- Every AC item in the spec has corresponding code changes in the diff
- No files modified outside the task's expected scope
- No obvious regressions (functions deleted that other files import)
- Diff is consistent with `decisions.md` entries

Output: `gh pr review --comment --body "..."` — posts directly to the PR. One-shot, no interaction.

**2. Write `scripts/run-review.sh`**

Invoked by orchestrator on `pr-opened`. Passes: PR number, spec path, `decisions.md` path.

**Done when:**
- [ ] Run against a real PR — verify a review comment appears on GitHub
- [ ] Deliberately introduce a diff that misses an AC item — verify the review flags it

### QA Agent

This is the most complex component. Requires a running staging environment.

**1. Set up Playwright + Stagehand**
```bash
npm install @browserbasehq/stagehand playwright
npx playwright install chromium
```

**2. Write `agent-prompts/qa-agent.md`**

Two passes:
- **Pass 1:** Run the test suite, map results to AC items
- **Pass 2:** For each AC item, describe the navigation steps needed to verify it. Stagehand executes them.

Output format: the `qa-report.md` template exactly, written to `docs/active-features/{feature}/qa-report.md`.

Verdict rules:
- All ACs pass → `PASS`
- All ACs pass but out-of-scope issues found → `CONDITIONAL PASS` with bug descriptions
- Any AC fails → `FAIL` with specific failure details

**3. Wire `qa_failure_count` into orchestrator**

Increment on each FAIL. At 2: invoke Opus blocker-resolver. At 3: hard stop.

**Done when:**
- [ ] Run QA agent against a PR that correctly implements its spec → `PASS` verdict
- [ ] Deliberately break one AC → `FAIL` verdict with the correct item flagged
- [ ] Verify `qa-report.md` is written in the correct format
- [ ] Verify the orchestrator correctly re-queues the task on FAIL

---

## Stage 7 — Ops Agents

**What you're building:** The three short-lived utility agents that run at specific events. Each is a prompt template + invocation script.

### Blocker Resolver (Opus)

Write `agent-prompts/blocker-resolver.md`. The prompt receives: the BLOCKER text from `progress.md`, the spec, all ADRs, and `decisions.md`. Opus returns a decision. Orchestrator writes it to `decisions.md` and sends it to the worker via `tmux send-keys`.

### Doc Closeout Agent

Write `agent-prompts/doc-closeout.md`. Triggered post-merge. Reads the merged diff + spec. Updates `docs/api/` if new endpoints, stubs `docs/runbooks/` if ops-relevant, updates `docs/architecture/data-model.md` if schema changed, archives `docs/active-features/{feature}/`.

### Backlog Triage (Opus, scheduled)

Write `agent-prompts/backlog-triage.md`. Add a cron or scheduled check in the orchestrator (runs once at 6am). Reads all backlog tasks + Linear state. Re-orders tasks by priority, posts daily plan to Telegram.

**Done when:**
- [ ] Deliberately write a BLOCKER to `progress.md` → orchestrator invokes Opus → decision written to `decisions.md` → worker receives it via tmux
- [ ] Merge a PR with a new endpoint → doc closeout agent updates `docs/api/`
- [ ] Trigger backlog triage manually → Telegram daily plan message received

---

## Stage 8 — Polish

**What you're building:** The monitor dashboard and remote control setup. Not load-bearing — the harness works without these.

### Monitor Dashboard

Write `scripts/monitor.py`. Uses `rich` library for a terminal dashboard in window 7. Reads `orchestrator-state.yaml` every 5 seconds and displays: active workers, task queue, recent log entries, running cost estimate.

### Remote Control

Follow the steps in [[remote-control]] (to be written). Key things:
- Termius SSH profile for your dev machine
- tmux key bindings tuned for mobile (larger targets, less chording)
- Telegram bot two-way commands: `/status`, `/pause`, `/resume`

### File Watcher (optional upgrade)

Replace the 10-second polling loop with `watchdog`-based filesystem events. Faster response, less unnecessary cycling. Add only after the polling version is stable — it's an optimization, not a requirement.

**Done when:**
- [ ] Monitor window shows correct state during an active task run
- [ ] SSH from phone, attach to tmux, send a message to a running worker
- [ ] Receive a Telegram update while away from keyboard

---

## Minimum Viable Harness

If you want a working harness as fast as possible, stop after Stage 4. That gives you:

- Directory structure and file formats
- Orchestrator loop that reads task files and spawns workers
- Task breakdown from approved specs
- Worker agents that execute and signal completion

No Telegram, no Linear, no QA agent — but the core loop works and agents are building real code. Add the other stages incrementally once you've run a few real tasks through it.

---

## Sequence Summary

```
Stage 1   Repo & Foundation          dirs, templates, schemas, setup.sh
Stage 2   Core Loop                  state_manager, tmux_manager, orchestrator
Stage 3   Task Breakdown             agent-prompts/task-breakdown.md + orchestrator trigger
Stage 4   Worker Protocol            agent-prompts/worker.md + worker spawn logic      ← MVH stops here
Stage 5   Integrations               telegram_notifier, linear_client
Stage 6   QA & Review Agents         agent-prompts/qa-agent.md, review-agent.md
Stage 7   Ops Agents                 blocker-resolver, doc-closeout, backlog-triage
Stage 8   Polish                     monitor, remote control, file watcher
```
