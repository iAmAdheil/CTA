# Harness Projects

The harness decomposes into 16 discrete buildable projects across 4 phases. Each is independently testable. Build in order — later phases depend on earlier ones.

---

## Phase 1 — Foundations

Build these before anything else. Everything depends on them.

---

### Project 1: Directory Scaffold

**What:** A shell script that creates the full directory structure for a new project using the harness.

**Creates:**
- All `tasks/` subdirectories
- All `docs/` subdirectories
- Template files in each directory
- `.gitignore` pre-configured for harness files

**Deliverable:** `scripts/setup.sh` — run once per new project.

---

### Project 2: Schema Definitions

**What:** Template files and YAML schemas that define every file format agents read and write. The contract layer.

**Files:**
- `templates/task.md` — task file format with all frontmatter fields
- `templates/progress.md` — agent journal format
- `templates/qa-report.md` — QA report format with AC table
- `schemas/task.schema.yaml` — validates task file frontmatter
- `schemas/orchestrator-state.schema.yaml` — validates orchestrator state

**Why first:** Every other component depends on knowing the file format. Define it once here.

---

### Project 3: CLAUDE.md Template

**What:** The agent operating manual. Lives in both repo root and `09-onboarding/`.

**Sections:**
- Codebase map (which directories own what responsibility)
- Hard rules (never modify, naming conventions, singleton imports)
- Pre-task checklist (read spec, read ADRs, run tests, check git log)
- Blocker protocol (when to stop vs. proceed)
- PR checklist (lint, typecheck, test, progress.md up to date)
- Signaling protocol (how to communicate state to orchestrator)

**Note:** This is a template — you fill in project-specific details for each project that uses the harness.

---

## Phase 2 — Core Loop

The harness engine. Build and test each piece before wiring them together.

---

### Project 4: State Manager

**What:** Python module that reads and writes `orchestrator-state.yaml` safely.

**Operations:**
- `read_state()` → dict
- `write_state(state)` → atomic write (write to tmp, rename — prevents corruption)
- `add_worker(task_id, worktree, window, model)`
- `remove_worker(task_id)`
- `get_runnable_tasks(all_tasks, done_tasks)` → tasks where all `depends_on` are done

**File:** `harness/state_manager.py` — ~80 lines.

---

### Project 5: tmux Session Manager

**What:** Python module that abstracts all tmux operations.

**Operations:**
- `create_window(name)` → opens a new tmux window, returns window ID
- `send_to_window(window_id, text)` → `tmux send-keys`
- `kill_window(window_id)` → tears down a finished worker
- `create_worktree(branch, path)` → `git worktree add`
- `remove_worktree(path)` → `git worktree remove`
- `read_window_last_lines(window_id, n)` → check if agent is idle

**File:** `harness/tmux_manager.py` — ~100 lines.

---

### Project 6: Orchestrator

**What:** The stateless loop. Reads state, takes one action, writes state, sleeps.

**Depends on:** Projects 4 (State Manager) and 5 (tmux Manager).

**Responsibilities:**
- Watch `01-specs/` for approved specs → invoke Task Breakdown
- Compute runnable tasks from dependency graph
- Spawn/tear down workers
- Watch `progress.md` for BLOCKER entries
- Watch task file status changes
- Trigger QA + Review agents on PR opened
- Call Linear + Telegram at gate transitions
- Merge approved PRs

**File:** `harness/orchestrator.py` — the main loop, ~300 lines.

**Test first with:** `max_workers: 1`, no Linear/Telegram (log-only mode), simple tasks.

---

### Project 7: File Watcher (optional upgrade)

**What:** Event-driven trigger layer. Replaces the sleep-poll loop with filesystem events.

**Technology:** Python `watchdog` library.

**Monitors:**
- `01-specs/` — new file with `status: approved`
- `tasks/in-progress/*/` — task status field changes
- `docs/active-features/*/progress.md` — BLOCKER section appears

**Note:** Start with the sleep-poll orchestrator (Project 6). Add this once the loop is working and you want faster response times.

**File:** `harness/file_watcher.py` — ~80 lines.

---

## Phase 3 — Integrations

Add one at a time. Each is independent — the orchestrator has log-only fallbacks when they're not configured.

---

### Project 8: Telegram Notification Bot

**What:** Python module wrapping the Telegram Bot API.

**Setup (one-time):**
1. Create a bot via @BotFather → get bot token
2. Message your bot → get your chat ID
3. Store both in `.env`

**Module operations:**
- `send(message)` → POST to Telegram Bot API

**Standard message templates:** task started, PR opened, QA pass/conditional/fail, blocked, Opus invoked, merged — each a one-liner with emoji.

**File:** `harness/telegram_notifier.py` — ~40 lines. `.env.example` for documentation.

---

### Project 9: Linear Integration

**What:** Python module wrapping the Linear GraphQL API.

**Setup (one-time):** Linear personal API key → store in `.env`

**Operations:**
- `create_epic(title, description)` → returns epic ID
- `create_issue(title, epic_id, spec_link)` → returns issue ID
- `move_issue(issue_id, status)` → "In Progress" / "In Review" / "QA Passed" / "Done"
- `add_comment(issue_id, body)`
- `attach_pr(issue_id, pr_url)`

**Branch naming convention:** `feature/LIN-{id}-{slug}` — Linear auto-links.

**File:** `harness/linear_client.py` — ~100 lines.

---

## Phase 4 — Agent Protocols

Write the prompt templates that define how each agent behaves. These are Markdown files, not code. Stored in `agent-prompts/`.

---

### Project 10: Worker Agent Protocol

**What:** The instruction set embedded in every worker's starting prompt.

**Contents:**
- What files to read first (task file, spec, CLAUDE.md, ADRs)
- Progress.md update protocol (what to write, how often, BLOCKER format)
- Signaling protocol (how to set `status` in the task file)
- PR description template (generated from spec acceptance criteria)
- Self-review checklist

**File:** `agent-prompts/worker.md`

---

### Project 11: Task Breakdown Agent

**What:** Prompt that decomposes an approved spec into atomic task files.

**Input context:** spec + ADRs + CLAUDE.md
**Required output:** YAML task files with dependency frontmatter

**Key constraint:** Each task should be completable in one agent session (30–90 minutes). If a task would be longer, it must be split.

**File:** `agent-prompts/task-breakdown.md` + `scripts/run-breakdown.sh`

---

### Project 12: Review Agent

**What:** Prompt that reads a PR diff against the spec and produces a review.

**Checks:**
- Every acceptance criterion has corresponding code changes
- No files modified outside the task's expected scope
- No obvious regressions
- Diff is consistent with `decisions.md`
- PR description is accurate

**Output:** GitHub PR review via `gh pr review` + summary posted to Linear.

**File:** `agent-prompts/review-agent.md` + `scripts/run-review.sh`

---

### Project 13: Operations Agents (3 prompts, one project)

Three short-lived utility agents:

**Doc Closeout Agent** — runs post-merge, updates API docs, runbook stubs, data model, archives active-features folder.
File: `agent-prompts/doc-closeout.md`

**Backlog Triage (Opus)** — runs daily at 6am, re-prioritizes backlog, posts daily plan to Telegram.
File: `agent-prompts/backlog-triage.md`

**Blocker Resolver (Opus)** — formats the blocker + context for an Opus judgment call, writes decision to `decisions.md`.
File: `agent-prompts/blocker-resolver.md`

---

## Phase 5 — QA (most complex, needs staging environment)

---

### Project 14: QA Agent

**What:** Browser-based verification agent. Most complex component — requires a live app to test against.

**Two passes:**
- **Pass 1 (automated):** Run test suite, map results to acceptance criteria
- **Pass 2 (browser):** Use Playwright + Stagehand to navigate the app and verify each AC item

**Technology choice:** Stagehand (by Browserbase) > Browser Use > raw Playwright with Claude prompt. Stagehand is higher-level and handles dynamic navigation better.

**Setup required:**
- Staging environment running
- Playwright + Stagehand installed
- Credentials for staging in `.env`

**Files:** `agent-prompts/qa-agent.md` + `scripts/run-qa.sh` + `qa/` directory for Playwright config.

---

## Phase 6 — Polish

---

### Project 15: Monitor Dashboard

**What:** Terminal dashboard for window 7. Not an agent — a display script.

**Shows:**
- `orchestrator-state.yaml` live view
- Task status summary (N backlog, N in-progress, N in review)
- Last 10 Telegram messages sent
- Running cost estimate (from a SQLite log the orchestrator writes)
- Recent orchestrator log entries

**Technology:** Python `rich` library, or `watch` + pretty-printed yaml. Refreshes every 5 seconds.

**File:** `scripts/monitor.py` or `scripts/monitor.sh`

---

### Project 16: Remote Control Setup

**What:** Documentation + scripts for phone-based control. No novel code.

**Contents:**
- Termius SSH connection profile setup
- tmux key bindings optimized for phone navigation
- Standard phone-based commands (approve PR, send message to worker, check status, pause all workers)
- Optional: Telegram bot two-way commands (`/status`, `/pause`, `/approve {task_id}`)

**File:** `docs/09-onboarding/remote-control.md`

---

## Build Order Summary

```
Phase 1  Projects 1, 2, 3     Foundations (do first)
Phase 2  Projects 4, 5, 6     Core loop (start here for first working harness)
         Project 7 (optional) File watcher (upgrade after loop works)
Phase 3  Projects 8, 9        Integrations (add one at a time)
Phase 4  Projects 10-13       Agent prompts (write before running agents)
Phase 5  Project 14           QA agent (needs staging environment)
Phase 6  Projects 15, 16      Polish (do last)
```

**Minimum viable harness (first working version):** Projects 1–6 + Project 10.
That gives you: directory structure, orchestrator loop, worker agents, state management, tmux session management. No Linear, no Telegram, no QA yet — but the core loop works.
