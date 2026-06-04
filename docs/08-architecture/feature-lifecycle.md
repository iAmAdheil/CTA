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

## Stage 2 — Architectural Advisor (Orchestrator + Opus)

Once `state_manager next-spec` releases an approved spec (highest priority, and only when the previous spec has drained — see "One Spec at a Time" below), the orchestrator runs the **advisor** *before* breakdown:

```bash
bash ~/agent-harness/scripts/run-advisor.sh <spec-path>
```

Opus (the `/advisor` skill) reads the spec + existing ADRs + CLAUDE.md and answers one question: *"Does implementing this spec force an architectural decision not already settled by an existing ADR or convention?"* It then does one of three things, and prints a final `ADVISOR_VERDICT:` line the orchestrator branches on:

- **No gap → PROCEED.** Nothing to decide; orchestrator goes straight to breakdown.
- **Low-risk gap → decide + PROCEED.** If the decision is reversible, local, conventional, with no new external contract / persisted schema / auth boundary, the advisor *makes the call itself*, writes a complete `docs/adrs/ADR-NNN-*.md` with `status: proposed`, **and updates any existing `docs/architecture/` docs the decision makes inaccurate** (citing the proposed ADR, since you ratify it at review), then proceeds. You **ratify or override it at PR-review time** — autonomy with an audit trail, not a 2-minute stub-filling chore. (This runs only on the decide path — never on no-gap PROCEED or on BLOCK, where no decision was made.)

  The orchestrator decides the verdict from the **durable side effect** — it re-reads the spec's `status:` after the advisor runs (`blocked` → BLOCK) and requires an `ADVISOR_VERDICT: PROCEED` line to proceed — rather than trusting the stdout marker alone.
- **Significant gap → BLOCK.** If it's hard to reverse, introduces a data model/migration, touches auth/security, adds an external contract, is cross-cutting, or needs product/business/legal judgment, the advisor writes `status: blocked` + the open question into the spec and stops. The spec leaves the `next-spec` pool until you answer and flip it back to `approved`.

This still guarantees the original property — an agent never discovers an architectural ambiguity mid-task — but it resolves the common case autonomously instead of stubbing every gap for you. The advisor is the agent responsible for blocking specs.

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
→ opens a pane in the shared `agents` window (pane id e.g. %7)
→ starts Claude Code session in that worktree
→ hands it: task file + spec + CLAUDE.md + relevant ADRs
→ Linear: issue → "In Progress"
→ Telegram: "🔧 LIN-51 started on pane %7"
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

Worker opens PR via `gh pr create` with description generated from the spec's acceptance criteria. It also **fills in the task file's `branch:` field** with the `task/<id>` branch name it created (empty until now), writes the **QA recipe** to `qa-instructions-<TASK-ID>.md` (how to drive *this* feature — routes + request/response for backend, navigate + interactions for frontend — plus its claimed I/O) and points `qa_instructions:` at it. Then it writes `status: pr-opened` to its task file.

Orchestrator detects the status change:
- Linear: issue → "In Review", PR link attached
- Moves task file to `tasks/review/`
- Tears down the worker's tmux **pane only** — the worktree persists (the `task/<id>` branch + open PR survive regardless; the local checkout is *kept* for QA to reuse)
- Telegram: `"🔀 PR #91 opened for LIN-51. QA starting."`
- **Triggers the QA agent** — an async, tracked agent (in `active_workers` with `role: qa`) that **reuses the task's worktree** on `task/<id>`. The worktree is one-per-task and lives until the task leaves its active states (see orchestrator-design.md, "One worktree per task"). The Review agent is a later, separate addition and is **not** part of this retry loop.

---

## Stage 6 — QA (QA Agent)

QA is **behavioral, not a code review.** It runs the feature and forms its verdict from *observed runtime behaviour* — it never reads the diff. ("Don't review code" = no static/diff review; that's the deferred Review agent's job.) Its inputs:

- **The rubric** — `expected_behavior` in the task file, authored by **task-breakdown** from the spec, *before code existed*. Implementation-agnostic ("given X, observable outcome Y"); its vocabulary tracks the task's layer (backend = API/data contract, frontend = UI behaviour, E2E = journey). **This is the grading truth.**
- **The recipe** — `qa-instructions-<TASK-ID>.md`, authored by the **worker**, telling QA *how* to drive the specific feature it built (and the worker's claimed I/O). **A map, never the rubric.**
- **How to run the project** — the project's own run/launch skill.
- The task's **worktree** on the `task/<id>` branch — the same one the worker used (reused, not freshly cut).

**Why three authors?** If the worker authored the expectations, it could write them to match its own wrong code (false pass); if QA authored them, it could invent its own (false fail). Authoring the rubric upstream in task-breakdown — a party that neither implements nor tests — removes both biases.

QA runs **two checks**:
1. **Execute the recipe** — does the implementation do what the worker claims? (catches a recipe that lies about its own code)
2. **Recipe vs rubric** — does the claimed/observed behaviour actually satisfy the rubric? (catches a worker that confidently built the *wrong* feature)

QA writes the **review-file status** (`WIP` → terminal) and findings to `04-active-features/{feature}/qa-report-<TASK-ID>.md`. The verdict is one of three, by **owner of the next action**:

### `looks-good` → you
Observed matches the rubric on every in-scope item. Orchestrator sets the task to **`qa-passed`** (board → Human Review), Telegram `"✅ QA passed LIN-51. Ready for your review."` The PR awaits your manual merge (Stage 7).

### `needs-changes` → Opus (one retry)
Reached two ways:
- **A bug** — the worker's intent was right but execution is broken (crashes, won't run, behaves wrong). **Always** an Opus pass, no judgment.
- **A *trivial* rubric mismatch** — the feature cleanly does something, but it's a small miss vs the rubric that a focused fix closes.

Orchestrator sets `qa-failed`, spawns an **Opus fixer** on the same task. The fixer reads `qa-report-<TASK-ID>.md`, sees `branch:` is already filled → **fix mode: pushes commits to that existing branch, sets `status: pr-updated`** (no second PR). That re-triggers QA.

**Second pass is terminal:** on the re-QA, *anything but `looks-good`* → straight to `escalate` (below). No further retries — the ladder is one retry.

### `escalate` → you (blocked)
A **fundamental** rubric mismatch: the worker cleanly built the *wrong* idea, or the spec is genuinely ambiguous — not a one-pass fix. Orchestrator sets `blocked-escalated` (board → Human Review) and **writes the reason into the task file** (`failure_reason`). This is yours to resolve, not Opus's.

> **The bug-vs-mismatch line:** *broken execution of the right idea* → always retry (bug). *Clean execution of the wrong idea* → judge trivial (Opus) vs fundamental (you). "Is this fixable in one pass?" is asked **only** on the mismatch path.

**Out-of-scope bugs** QA stumbles on (real defects outside this task's rubric) are **ignored** by the verdict — only in-rubric problems route. They're handled by a separate future "random-bugs" subsystem (see `build-roadmap.md`), not this loop.

**QA-run infra failure:** if the QA agent's pane dies with the review file stuck at `WIP` (no verdict), that's a failed *run*. Orchestrator retries the QA spawn up to **N=2** times; if still no verdict, it `escalate`s to you (`blocked-escalated` + `failure_reason: "QA couldn't complete after N attempts"`). A QA agent that keeps dying is an infra/env problem — yours, not an Opus fix. This infra-retry is separate from the one-retry verdict ladder.

---

## Stage 7 — Human Review (You, on your phone)

A task at `qa-passed` (or `blocked-escalated`) sits in the **Human Review** column with its PR open. You read `qa-report-<TASK-ID>.md` and check the PR on GitHub mobile. Takes 3–5 minutes.

- **Merge** → you merge the PR **manually on GitHub** (no auto-merge — human review is the point). A separate merge-detection/archival agent notices the merge and drives Stage 8.
- **Send back** → leave a note in the task file; the harness re-queues it.
- **Escalate** → for `blocked-escalated` tasks, read the `failure_reason`, then either fix the spec/rubric and re-approve, or decide the edge case is acceptable.

---

## Stage 8 — Merge & Closeout (separate agent)

This is **not** done inline by the orchestrator. A separate merge-detection/archival agent (see `build-roadmap.md`):
- detects the human merged the PR (`gh pr view --json state,mergedAt`) and sets the task `status: done`
- Kanban: task card → "Done"; the orchestrator's archive step then `mv`s the task file to `tasks/done/`
- Archives `04-active-features/{feature}/`; stubs runbooks / updates `06-api/` / `08-architecture/data-model.md` as relevant
- Telegram: `"✅ LIN-51 merged."`

Once a task is `done`, any tasks whose `depends_on` are now all done become runnable for the next worker slot.

---

## One Spec at a Time (serial-per-spec)

The orchestrator works **exactly one spec at a time**, enforced in code by the
`state_manager next-spec` gate — not by your approval cadence. You can approve
several specs at once; the orchestrator still drains them serially, highest
priority first.

**How the gate works:**
- A spec is "being worked" while any of its tasks sit in `tasks/backlog/` or
  `tasks/in-progress/`.
- While a spec is being worked, no other spec is broken down.
- Once the current spec's tasks are all in a **"now handled by the human"** state —
  `qa-passed`, `done`, `blocked`, or `blocked-escalated` — the gate releases the
  next spec. The principle: advance when the **harness has no more automated work**
  on the task, but still **don't wait on the human to merge**.
- **`pr-opened`, `qa-failed`, and `pr-updated` are NOT terminal** — they mean the
  async QA→Opus→re-QA loop is (or will be) running on this spec's tasks. Starting
  the next spec then would reintroduce the cross-spec concurrency we rejected, so
  the gate **holds** until every task clears the loop into a human-handled state.
  (Earlier this design released at `pr-opened`; the QA loop, which `pr-opened` now
  *triggers*, pushed the release point down to `qa-passed`.)
- Among approved specs, the gate picks the highest `priority`
  (critical → high → medium → low, tie-broken by filename).

**Why serial, not cross-feature parallel:** simpler mental model, easier review,
and no cross-feature merge conflicts. Parallelism still happens *within* a spec —
tasks whose `depends_on` are satisfied and that don't share files run
concurrently up to `max_workers`. (This reverses the earlier "parallel mode"
design, where idle slots were filled across features for throughput.)

**Important constraint:** tasks that share files within the same feature must not be marked `can_parallelize_with`. The task breakdown agent enforces this. Parallelism is only safe when tasks work on different parts of the codebase.

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
Worker done           → fills branch + qa-instructions, PR opened via gh cli
Orchestrator          → tears down worker pane (worktree persists), triggers async QA (reuses it)
QA agent              → runs the feature, writes qa-report-<ID>.md verdict

QA looks-good:        → task qa-passed → You (phone) → merge manually
QA needs-changes:     → bug OR trivial mismatch → Opus fixer pushes to same
                        branch (pr-updated) → re-QA (2nd pass = looks-good or block)
QA escalate:          → fundamental mismatch → blocked-escalated + failure_reason → you
QA run died (×N):     → retry N=2, then escalate to you
Out-of-scope bug:     → IGNORED by the loop (future random-bugs subsystem)

Merge (manual, you)   → separate archival agent → done + doc closeout
                      → next unblocked task starts automatically
```

You appear when QA hands a task to you: `qa-passed` (merge) or `blocked-escalated` (resolve). For the feature overall: spec approval and the manual merges. Everything else runs autonomously.
