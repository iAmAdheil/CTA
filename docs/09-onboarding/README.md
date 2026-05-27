# Agentic Engineering Harness

## What This Is

A system for running serious software development with AI agents — not vibe coding, but a structured pipeline where every stage has defined inputs, outputs, owners, and quality gates.

The shift in mindset: **agents are workers, not architects**. You spend your energy on specs, task design, and quality gates. The orchestrator enforces the process. Opus makes the judgment calls you'd otherwise block on. Sonnet/Haiku execute. You become the engineering manager, not the IC.

## The Problem This Solves

| Vibe Coding | Agentic Engineering |
|---|---|
| Random prompting, no tracking | Spec-first, every task has a file |
| No idea if something breaks | QA agent + test gates before merge |
| Single file, 10,000+ lines | Modular, agents work in isolated worktrees |
| Context lost between sessions | State lives in files, not in memory |
| No review before merge | Review agent + human sign-off |
| Can't run overnight | Orchestrator loops indefinitely |

## Core Principles

**1. The spec is the single source of truth.**
Everything else — tasks, progress logs, QA reports — is navigation toward it or evidence of work done against it.

**2. The orchestrator is stateless.**
All state lives in files. The orchestrator reads files, takes one action, writes files, sleeps. It can crash and restart cleanly at any point.

**3. Agents signal, they don't decide.**
Workers write to `progress.md` and their task file's `status` field. The orchestrator watches and acts. Workers never touch Linear, Telegram, or the kanban — that's the orchestrator's job.

**4. Compute starts only after a human approves the spec.**
The system is idle until you set `status: approved` on a spec. You are the only gate before work begins.

**5. Quality gates are non-negotiable.**
Nothing merges without: lint pass, typecheck pass, tests pass, QA agent verdict, human review. The orchestrator enforces this sequence.

## What's in This Vault

| Directory | Purpose |
|---|---|
| `00-inbox/` | Raw ideas, shower thoughts, voice memo transcripts |
| `01-specs/` | Approved feature specs — the agent contract |
| `02-adrs/` | Architecture Decision Records — permanent, never deleted |
| `03-tasks/` | Reference view of task files (live in the repo) |
| `04-active-features/` | Living docs while a feature is in progress |
| `05-runbooks/` | How to operate things in production |
| `06-api/` | Endpoint documentation |
| `07-postmortems/` | What broke, why, what changed |
| `08-architecture/` | System design, agent map, orchestrator design |
| `09-onboarding/` | This section — how to use the harness |
| `10-product/` | PRDs, user stories, roadmap docs — product intent above the spec level |
| `11-misc/` | Reference files linked from tasks: wireframes, mockups, flows, any format |
| `_kanban/` | Human-facing kanban boards (you navigate here, not agents) |

## Key Documents

- [[build-guide]] — Ordered stages to build the harness from scratch, with "done when" tests
- [[harness-projects]] — The 16 buildable components described in detail
- [[setup-guide]] — How to plug the finished harness into a new project
- [[agent-map]] — All agents, their roles, models, and concurrency
- [[feature-lifecycle]] — Full E2E from idea to merged PR
- [[file-system]] — Directory structures for repo and vault
- [[orchestrator-design]] — How the orchestrator works
- [[doc-maintenance]] — The documentation cycle for a feature
