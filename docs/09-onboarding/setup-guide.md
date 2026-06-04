# Setup Guide

How to plug this harness into a new project.

---

## Prerequisites

Before starting, have these ready:

- [ ] macOS with tmux installed (`brew install tmux`)
- [ ] Python 3.11+ (`python3 --version`)
- [ ] Claude Code CLI installed and authenticated (`claude --version`)
- [ ] GitHub CLI installed and authenticated (`gh auth status`)
- [ ] A project repo (existing or new) that will be managed by the harness
- [ ] Optional: Telegram account for notifications
- [ ] Optional: Linear account for task tracking

---

## Step 1: Clone the Harness

The harness lives in its own repo, separate from your projects.

```bash
# The harness repo lives at agent-harness/
# Your project repos live alongside it, e.g.:
# ~/projects/my-app/
# ~/agent-harness/           ← this repo
```

---

## Step 2: Initialize Project Structure

Run the scaffold script from your project root:

```bash
cd ~/projects/my-app
bash ~/agent-harness/scripts/setup.sh
```

This creates:
- `tasks/` with backlog/, in-progress/, review/, done/ subdirectories
- `docs/specs/`, `docs/adrs/`, `docs/active-features/`
- `agent-prompts/` (symlinked from harness)
- `templates/` (symlinked from harness)
- `CLAUDE.md` (starter template — edit this)
- `orchestrator-state.yaml` (empty state)
- `.gitignore` entries for harness operational files

---

## Step 3: Write Your CLAUDE.md

This is the most important step. Open `CLAUDE.md` in your project root and fill in the project-specific sections:

```markdown
# CLAUDE.md — [Project Name] Agent Operating Manual

## Codebase Map
- src/routes/     → [what lives here]
- src/lib/        → [what lives here]
- src/services/   → [what lives here]
...

## Rules
- Never modify [protected files/dirs]
- All new [X] need a corresponding [Y]
- Use existing [patterns] from [location]
...

## Before you start any task
1. Read the linked spec file completely
2. Read any referenced ADRs
3. Run [test command] and note any pre-existing failures
4. Check git log --oneline -10

## When you're blocked
[Your criteria for when to stop vs. proceed]

## PR checklist
- [ ] [lint command] passes
- [ ] [typecheck command] passes
- [ ] [test command] passes
- [ ] progress.md is up to date
```

Also mirror this file to the vault: copy it to `agent-harness/docs/09-onboarding/CLAUDE.md`

---

## Step 4: Configure Integrations

Create a `.env` file in your project root (never commit this):

```bash
cp ~/agent-harness/.env.example .env
```

Fill in what you have:

```env
# Telegram (get from @BotFather and your bot chat)
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# Linear (from Linear settings → API → Personal API keys)
LINEAR_API_KEY=

# Staging environment for QA agent
STAGING_URL=http://localhost:3000
STAGING_USERNAME=
STAGING_PASSWORD=
```

Leave blank any integrations you're not using yet — the orchestrator has log-only fallbacks.

---

## Step 5: Start a tmux Session

The harness runs in a persistent tmux session. Start it once and leave it running.

```bash
tmux new-session -d -s main
tmux rename-window -t main:0 "orchestrator"
```

Start the orchestrator:
```bash
tmux send-keys -t main:0 "cd ~/projects/my-app && python ~/agent-harness/harness/orchestrator.py" Enter
```

Start the monitor dashboard in a new window:
```bash
tmux new-window -t main -n "monitor"
tmux send-keys -t main:monitor "bash ~/agent-harness/scripts/monitor.sh" Enter
```

Attach to the session anytime:
```bash
tmux attach -t main
```

---

## Step 6: Your First Feature

**1. Write a spec**

Create `agent-harness/docs/01-specs/feature-my-first-feature.md` using the template in `01-specs/_template.md`. Fill in the acceptance criteria carefully — these are the checklist the QA agent works from.

**2. Approve it**

Set `status: approved` in the frontmatter. Add a Kanban card in `_kanban/features.md` and move it to **Ready**.

**3. Watch the orchestrator pick it up**

```bash
tmux attach -t main
# Window 0: orchestrator detects new approved spec
# → invokes Task Breakdown Agent (window 6)
# → task files appear in tasks/<feature>/backlog/ (feature = spec's frontmatter id)
# → worker spawned (window 1)
# → Telegram notification sent
```

**4. Monitor from your phone**

SSH in from Termius:
```bash
ssh user@your-machine
tmux attach -t main
```

Or read the state directly:
```bash
cat ~/projects/my-app/orchestrator-state.yaml
cat ~/projects/my-app/docs/active-features/my-first-feature/progress.md
```

**5. Approve the PR**

When Telegram notifies you that QA passed, open the PR link, read `qa-report.md`, then:
```bash
gh pr merge {PR_NUMBER} --merge
```

---

## Day-to-Day Workflow

Once set up, your workflow is:

| You do | System does |
|---|---|
| Think about a feature | — |
| Drop rough doc in `00-inbox/` | — |
| Refine into a spec | — |
| Set `status: approved`, add kanban card | Orchestrator picks it up |
| — | Task Breakdown → tasks created |
| — | Workers spawn, execute, open PRs |
| — | QA + Review run on each PR |
| Read QA report on phone (3-5 min) | — |
| Approve or send back | — |
| — | Merge, docs updated, next task starts |

You appear twice per feature: spec approval and PR merge. Everything else runs while you're doing something else.

---

## Pausing the System

To pause all new work (e.g., you're going on holiday, or debugging):

```bash
# Edit orchestrator-state.yaml
# Set max_workers: 0
# Orchestrator will finish current tasks but not start new ones
```

To stop everything immediately:
```bash
tmux kill-session -t main
```

To resume:
```bash
tmux new-session -d -s main
# restart orchestrator on window 0
```

---

## Remote Control from Phone

See [[remote-control]] for the full Termius setup and common phone commands.

Short version:
```bash
# Check status
cat orchestrator-state.yaml

# Send a message to a running worker
tmux send-keys -t main:1 "pause, re-read the spec section on edge cases, then continue" Enter

# Approve a PR
gh pr merge 91 --merge

# Pause all new work
# (edit orchestrator-state.yaml: max_workers: 0)
```
