# Documentation Maintenance

How documentation works in this system: what types exist, who writes them, and the full cycle a feature goes through.

---

## Core Principle

Every document has **one job at one stage**. The mistake is writing a single "feature doc" that tries to be a spec, a changelog, and a runbook simultaneously.

A feature passes through distinct document *types*, each owned by a different stage of the pipeline. When the stage is done, that document type is done.

---

## Document Types

### `00-inbox/` — Raw Ideas
**Written by:** You  
**Format:** Anything. Stream of consciousness. Voice memo transcripts.  
**Purpose:** Capture before you lose the thought. No structure required.  
**Lifecycle:** Read when you sit down to write the spec. Deleted or archived after the spec is approved.

---

### `01-specs/` — Feature Specs
**Written by:** You  
**Format:** Structured Markdown with frontmatter  
**Purpose:** The contract. Agents execute against acceptance criteria here.  
**Lifecycle:** Created before any agent touches code. Never modified after approval (changes go through a revised spec or new ADR). Archived after merge.

**Template structure:**
```markdown
---
status: draft          # → approved when you're ready
author: you
created: YYYY-MM-DD
linear_epic: null
adrs_referenced: []
---

# Spec: {Feature Name}

## Goal
One sentence on what problem this solves.

## Acceptance Criteria
- [ ] Specific, verifiable criterion
- [ ] Another criterion

## Out of Scope
- What you are NOT building

## Edge Cases
- Non-obvious situations that need handling

## Open Questions
- Anything you're unsure about (resolve these before approving)
```

---

### `02-adrs/` — Architecture Decision Records
**Written by:** You (stubs created by Orchestrator, you fill them in)  
**Format:** Short Markdown, 4 sections  
**Purpose:** Permanent record of *why* the codebase is shaped the way it is. Agents read these to understand context before acting.  
**Lifecycle:** Never deleted. Never updated. If a decision is reversed, a new ADR is written that supersedes the old one.

**Template structure:**
```markdown
---
status: proposed       # → accepted | rejected | superseded
date: YYYY-MM-DD
supersedes: null
superseded_by: null
---

# ADR-{NNN}: {Title}

## Context
Why this decision needed to be made.

## Decision
What was decided.

## Consequences
What changes as a result. Both positive and negative.
```

---

### `04-active-features/{feature}/progress.md` — Agent Journal
**Written by:** Worker agents  
**Format:** Timestamped log  
**Purpose:** You read this to know what the agent is doing right now. Orchestrator watches this for BLOCKER entries.  
**Lifecycle:** Exists only while the task is active. Archived with the rest of `04-active-features/` after merge.

Key sections the orchestrator parses:
```markdown
## Blockers
BLOCKER: [description of what's unclear and why it matters]
```

---

### `04-active-features/{feature}/decisions.md` — Micro-Decisions
**Written by:** Worker agents + Orchestrator (when recording Opus decisions)  
**Format:** Dated log entries  
**Purpose:** Captures small decisions that don't warrant a full ADR — implementation choices, tradeoffs made during execution.  
**Lifecycle:** Archived after merge.

---

### `04-active-features/{feature}/qa-report.md` — QA Report
**Written by:** QA Agent  
**Format:** Structured table + verdict  
**Purpose:** The evidence that a PR meets the spec. You read this before approving the merge.  
**Lifecycle:** Archived after merge.

---

### `05-runbooks/` — Operational Runbooks
**Written by:** Doc Closeout Agent (stub), you (details)  
**Purpose:** How to operate and debug a feature in production.  
**Lifecycle:** Permanent. Updated when the feature changes significantly.

---

### `06-api/` — API Documentation
**Written by:** Doc Closeout Agent (maintained post-merge)  
**Purpose:** What endpoints exist and how to call them.  
**Lifecycle:** Permanent. One file per resource.

---

### `09-onboarding/CLAUDE.md` — Agent Operating Manual
**Written by:** You  
**Purpose:** The first thing every agent reads. The employee handbook. Project-specific conventions, rules, and checklists.  
**Lifecycle:** Permanent. Updated whenever codebase conventions change.  
**Mirror:** Also lives at project repo root. Keep them in sync.

---

## The Documentation Cycle for a Feature

```
IDEA
  └─→ 00-inbox/idea-{feature}.md
        (raw thought, no structure needed)

DECISION TO BUILD
  └─→ _kanban/features.md: card added → "Speccing" column
  └─→ 01-specs/feature-{name}.md created (you write)

ARCHITECTURE QUESTION?
  └─→ Orchestrator + Opus check
  └─→ 02-adrs/ADR-{NNN}-{title}.md stub → you fill in

SPEC APPROVED
  └─→ frontmatter: status: approved
  └─→ _kanban: card → "Ready"

TASK CREATED
  └─→ tasks/{feature}/backlog/TASK-{NNN}.yaml (Task Breakdown Agent writes; feature = spec's frontmatter id)
  └─→ _kanban: card → "In Progress"
  └─→ Telegram: "📋 Feature broken into N tasks"

AGENT WORKING
  └─→ 04-active-features/{feature}/progress.md (worker writes)
  └─→ 04-active-features/{feature}/decisions.md (micro-decisions)
  └─→ Telegram: "🔧 TASK-NNN started on pane %7"

PR OPENED
  └─→ _kanban: card → "QA"
  └─→ Telegram: "🔀 PR #{N} opened"

QA RUNS
  └─→ 04-active-features/{feature}/qa-report.md (QA agent writes)
  └─→ 01-specs/feature-{name}.md: AC checkboxes updated ✅/❌
  └─→ Linear comment: QA verdict

HUMAN REVIEW
  └─→ You read qa-report.md on your phone
  └─→ Approve or send back with a note

MERGED
  └─→ _kanban: card → "Done"
  └─→ 05-runbooks/ stub created if ops-relevant
  └─→ 06-api/ updated if new endpoints
  └─→ 08-architecture/data-model.md updated if schema changed
  └─→ 04-active-features/{feature}/ archived
  └─→ Telegram: "✅ Feature merged. PR #{N}."
```

---

## What NOT to Document

As important as what you do document:

- **Don't document obvious code.** If the code is readable, don't paraphrase it in a doc. Comments should explain WHY, not WHAT.
- **Don't keep stale docs alive.** A wrong doc is worse than no doc. Archive feature docs after merge.
- **Don't let agents write specs.** Agents write progress logs and QA reports. Specs come from you. An agent writing its own spec is an agent with no oversight.
- **Don't duplicate.** The kanban card links to the spec. The task file links to the spec. Nothing copies the spec content. One source of truth.
- **Don't write ADRs for obvious choices.** Not every technical decision needs an ADR. Only decisions that: affect the system architecture, would surprise a future reader, or explain why an obvious alternative was rejected.

---

## The Kanban Board

The kanban in `_kanban/features.md` is **for you**, not for agents. It's navigation.

```
## Inbox
- [ ] [[idea-smart-search]] — raw idea, not yet specced

## Speccing
- [ ] [[feature-rate-limiting]] — spec in progress

## Ready
- [ ] [[feature-data-export]] — spec approved, tasks not yet created

## In Progress
- [ ] [[feature-oauth]] — TASK-039, worker on pane %7

## QA
- [ ] [[feature-pdf-export]] — PR #84, QA running

## Human Review
- [ ] [[feature-export-core]] — PR #91, QA passed, awaiting your approval

## Blocked
- [ ] [[feature-payments]] — TASK-051 failed QA 3 times, needs your attention

## Done
- [x] [[feature-dark-mode]] — merged 2026-05-20
```

Each card is a wikilink to the spec — not a description of it. The board is navigation to the spec, not a copy of it.

**Column → task status mapping:**

| Kanban column | Task status | Who moves it |
|---|---|---|
| Inbox | — | You (manually) |
| Speccing | — | You (manually) |
| Ready | `status: approved` in spec | You (manually, at spec approval) |
| In Progress | `in-progress` | Orchestrator |
| QA | `pr-opened` or `pr-updated` | Orchestrator |
| Human Review | QA verdict: PASS/CONDITIONAL | Orchestrator |
| Blocked | `blocked-escalated` (QA failed 3×) or `blocked` | Orchestrator |
| Done | `done` | Orchestrator |

**Human Review is the column that needs your eyes.** When a card lands here, you have a Telegram notification waiting. Read the qa-report, check the PR, approve or send back. The goal is to keep this column empty.
