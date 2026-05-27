# Feature Lifecycle: E2E Flow

From raw idea to merged PR, with every agent, file, and human touchpoint.

---

## Stage 0 — Idea (You)

You think about a feature. Talk it through with Claude chat, a voice memo, your notes app — whatever. The system is **not involved yet**.

When you have enough clarity, drop a rough doc:

```
00-inbox/idea-data-export.md
```

No structure required. Stream of consciousness is fine. This is your thinking, not a spec.

---

## Stage 1 — Spec (You)

Read the inbox note. Rewrite it into a proper spec using the template in `01-specs/_template.md`.

What a good spec contains:
- **Goal** — one sentence on what problem this solves
- **Acceptance Criteria** — the checklist the QA agent will work from. Be specific.
- **Out of Scope** — what you are explicitly NOT building
- **Edge Cases** — the non-obvious situations that need handling
- **Open Questions** — anything you're not sure about yet

When you're satisfied:
1. Save the file to `01-specs/feature-data-export.md`
2. Set `status: approved` in the frontmatter
3. Add a Kanban card in `_kanban/features.md` and move it to **Ready**

This is the **only manual gate** before compute starts. A human approves every spec. Agents never self-approve.

---

## Stage 2 — ADR Check (Orchestrator + Opus)

Orchestrator detects the new approved spec (watches `01-specs/` for `status: approved`).

Before creating tasks, it invokes **Opus** with: the spec + existing ADRs + CLAUDE.md.

Opus answers one question: *"Does this feature require architectural decisions not already covered by existing ADRs?"*

- **If yes:** Orchestrator writes ADR stubs to `02-adrs/`. You fill them in (2–3 minutes each — they're short).
- **If no:** Stage is skipped entirely.

ADRs are written before implementation starts. An agent should never discover an architectural ambiguity mid-task.

---

## Stage 3 — Task Breakdown (Orchestrator + Sonnet)

Orchestrator invokes the Task Breakdown Agent with the approved spec + all ADRs.

Agent output: individual task files in `tasks/backlog/`, each with:

```yaml
id: TASK-051
title: Export service core
spec: docs/01-specs/feature-data-export.md
depends_on: []
blocks: [TASK-052, TASK-054]
can_parallelize_with: [TASK-053]
status: backlog
model: sonnet
```

Each task should be completable in one agent session (30–90 minutes of agent time). If a task takes longer, it should be split.

Orchestrator then:
- Creates a Linear Epic + one issue per task
- Moves Kanban card to **In Progress**
- Sends Telegram: `"📋 Data Export: 5 tasks created. Epic LIN-48."`

---

## Stage 4 — Execution (Orchestrator + Workers)

Orchestrator reads the dependency graph. Tasks where all `depends_on` are in `done/` are runnable.

For each runnable task (up to `max_workers`):

```
git worktree add ../project-export-core feature/LIN-51-export-core
→ opens tmux window:N
→ starts Claude Code session in that worktree
→ hands it: task file + spec + CLAUDE.md + relevant ADRs
→ Linear: issue → "In Progress"
→ Telegram: "🔧 LIN-51 started on window:1"
```

Workers write to `04-active-features/{feature}/progress.md` continuously. You can check this anytime from your phone.

Tasks that can parallelize run simultaneously in separate worktrees. The orchestrator tracks active workers in `orchestrator-state.yaml`.

---

## Stage 4a — Blocker Handling (Worker → Orchestrator → Opus)

If a worker hits something the spec didn't cover, it writes to `progress.md`:

```
## Blockers
BLOCKER: Spec says "email delivery" but doesn't specify what happens
if the user has no email set. Affects the service interface design.
```

Orchestrator detects the blocker. Two paths:

**Answerable from existing docs** → orchestrator resolves it and sends the answer back to the worker via `tmux send-keys`.

**Judgment call** → orchestrator invokes Opus with the specific question. Opus returns a decision. Orchestrator:
- Writes the decision to `04-active-features/{feature}/decisions.md`
- Sends it to the worker via `tmux send-keys`
- Telegram: `"🤔 Opus resolved blocker on LIN-51: [decision summary]"`

Worker unblocks and continues.

---

## Stage 5 — PR Opening (Worker → Orchestrator)

Worker runs its self-review checklist before signaling:

```
npm run lint        ✅
npm run typecheck   ✅
npm test            ✅ (pre-existing failures noted in progress.md)
git diff main       reviewed
```

Worker opens PR via `gh pr create` with description generated from the spec's acceptance criteria. Then writes `status: pr-opened` to its task file.

Orchestrator detects the status change:
- Linear: issue → "In Review", PR link attached
- Moves task file to `tasks/review/`
- Tears down the worker's tmux window and worktree
- Telegram: `"🔀 PR #91 opened for LIN-51. QA starting."`
- Simultaneously triggers QA Agent (window 4) and Review Agent (window 5)

---

## Stage 6 — QA (QA Agent)

QA agent receives: PR diff, spec (acceptance criteria), staging URL, pre-existing failure list.

**Pass 1 — Automated:** Runs the test suite against the PR branch. Maps each test result to an acceptance criterion.

**Pass 2 — Browser navigation:** Uses Playwright + Stagehand to manually exercise the feature. Works through each acceptance criterion by actually using the app.

Writes to `04-active-features/{feature}/qa-report.md`:

```markdown
| Criterion            | Result | Notes                          |
|----------------------|--------|-------------------------------|
| CSV export works     | ✅     | File structure verified        |
| Large account async  | ⚠️ WARN | 8s actual vs 5s spec target   |

Verdict: CONDITIONAL PASS
```

Orchestrator reads verdict and routes based on failure type:

### PASS
- Linear comment: QA verdict
- Telegram: `"✅ QA passed LIN-51. Ready for your review."`
- Task moves to Stage 7 (human review)

### CONDITIONAL PASS
- Telegram: `"⚠️ QA conditional LIN-51. [specific issue]. Original ACs: all pass."`
- If the issue is out-of-scope (not in the spec's ACs): new `BUG-NNN.yaml` created in `tasks/backlog/`, task still moves to human review with the bug noted
- If the issue touches an AC item: treated as FAIL

### FAIL — AC failure (first time)
- Task status: `qa-failed`
- Worker re-spawned with: original task file + qa-report.md + instruction to fix only what failed
- Worker pushes new commits to the same PR branch
- Worker signals `status: pr-updated`
- Orchestrator triggers QA again on the updated PR

### FAIL — AC failure (second time)
- Orchestrator invokes Opus: spec + both QA reports + current diff → diagnosis
- If fixable: Opus sends specific fix instructions to a new worker on the same task
- If spec is unclear: Opus flags it → Telegram to you for human input

### FAIL — third time
- Hard stop. Task status: `blocked-escalated`
- Telegram: `"🚨 LIN-51 failed QA 3 times. Needs human attention."`
- You SSH in and diagnose directly

---

## Stage 7 — Human Review (You, on your phone)

You get the Telegram notification. Open Obsidian on your phone, read `qa-report.md`. Check the PR on GitHub mobile. Takes 3–5 minutes.

Three options:

**Approve** → SSH into tmux or reply to Telegram bot. Orchestrator merges.

**Send back** → SSH in, leave a note in the task file. Orchestrator re-queues to a worker with your note attached.

**Escalate** → You decide the edge case is acceptable or create a new backlog task to address it later.

---

## Stage 8 — Merge & Closeout (Orchestrator)

On merge, orchestrator:
- Linear: issue → "Done", checks if all Epic tasks are complete
- Kanban: feature card → "Done"
- Archives `04-active-features/{feature}/`
- If ops-relevant: stubs `05-runbooks/{feature}-runbook.md`
- If new endpoints: updates `06-api/endpoints/`
- If schema changed: updates `08-architecture/data-model.md`
- Telegram: `"✅ LIN-51 merged. PR #91. 3/5 tasks done on Data Export epic."`

Orchestrator then checks the dependency graph again. Any tasks now unblocked (because their `depends_on` are all done) get queued for the next worker slot. The loop continues automatically.

---

## Single Spec vs Parallel Specs

The orchestrator picks up any spec with `status: approved`. Parallelism across features is controlled entirely by you.

**Serial mode (recommended to start):**
Approve one spec at a time. Let it run fully to merge before approving the next. Simpler mental model, easier to review, no cross-feature merge conflicts.

**Parallel mode (once the harness is mature):**
Approve multiple specs. The orchestrator fills idle worker slots across all active features. While QA is blocking Feature A, workers execute Feature B tasks. Higher throughput.

To switch between modes: no code changes. Just approve the next spec (or don't) based on what you want.

**Important constraint regardless of mode:** tasks that share files within the same feature must not be marked `can_parallelize_with`. The task breakdown agent enforces this. Parallelism is only safe when tasks work on different parts of the codebase.

---

## The Full Picture

```
You                   → idea in 00-inbox/
You                   → approve spec in 01-specs/, set status: approved
Orchestrator + Opus   → ADR check, stubs written if needed
You                   → fill ADR stubs (2-3 min each)
Orchestrator + Sonnet → task breakdown + Linear epic
Orchestrator          → spin up workers in worktrees (parallelizes where deps allow)
Workers               → execute, write progress.md
Blockers              → Orchestrator → Opus → decision → worker unblocked
Worker done           → PR opened via gh cli
Orchestrator          → Linear + Telegram + QA + Review triggered
QA agent              → qa-report.md + Linear comment
Review agent          → PR review comment

QA PASS:              → You (phone) → approve → merge
QA FAIL (1st):        → Worker re-spawned to fix only what failed → QA again
QA FAIL (2nd):        → Opus diagnoses → targeted fix or escalates to you
QA FAIL (3rd):        → Hard stop → Telegram alert → you SSH in
Out-of-scope bug:     → Filed as BUG-NNN in backlog → original task continues

Merge                 → Orchestrator → Linear done + doc closeout
                      → next unblocked task starts automatically
```

You appear twice per task: never (it runs), or when QA escalates to you. For the feature overall: spec approval and final merge. Everything else runs autonomously.
