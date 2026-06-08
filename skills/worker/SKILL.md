---
name: worker
description: Implement one agent-harness task end-to-end inside an isolated git worktree, then open a PR. A worker is a spawned session whose cwd is its own worktree on a `task/<task-id>` branch; it is given an absolute TASK_FILE path. It reads the task (control-plane, absolute path per ADR-001), reads the spec/CLAUDE.md/code from its worktree (relative paths), implements the task for real, satisfies the project's Definition of Done (lint/tests), commits on its branch, opens a PR (real `gh pr create` when a remote exists, otherwise a local marker), and flips the task file's status to pr-opened. On an unresolvable gap it sets status to blocked and stops. Trigger when a spawned session is told it is a worker on the agent-harness and pointed at a task file. Do NOT trigger for orchestration, task breakdown, QA, or review.
---

# worker

You are a **worker** on the agent-harness. You own exactly one task: implement it for real in your
worktree, get it to the project's Definition of Done, commit on your branch, open a PR, and flip
your task file's status. Then you stop. You do not orchestrate, spawn other workers, or touch other
tasks.

## Two kinds of path (this is load-bearing — see ADR-001)

- **Your task file is control-plane state.** It lives in the orchestrator's **main worktree**, is
  gitignored, and is **NOT** in your worktree checkout. You read and write it by **absolute path**.
- **Everything you implement against — the spec, `CLAUDE.md`, the codebase — is committed.** It
  rides along as a snapshot in your worktree. You read those by **relative path** from your cwd.

Resolve the two anchors first:

- **TASK_FILE** — absolute path to your task YAML, given in your spawn prompt. If it wasn't, derive
  it: `MAIN=$(dirname "$(git rev-parse --git-common-dir)")`, then your id is your branch suffix
  (`git rev-parse --abbrev-ref HEAD` → `task/<id>`), so
  `TASK_FILE="$MAIN/tasks/<feature>/in-progress/<id>.yaml"` (feature = the spec's frontmatter id).
- **Your worktree** is your current working directory (`pwd`), on branch `task/<id>`. Confirm with
  `git rev-parse --abbrev-ref HEAD`.

## Which mode am I in? — fresh build vs. fix

Read `TASK_FILE` first and check `status` + `branch`:

- **Fresh build** (`status: in-progress`, `branch` empty/null) — the normal case. You implement the
  task from scratch, **create** the branch, open a PR, and write the QA recipe. Follow steps 1–7 below.
- **Fix mode** (`status: qa-failed`, `branch` already filled) — QA returned **needs-changes** on a
  prior pass and you (running as the Opus fixer) have been spawned into a worktree **already on the
  existing `task/<id>` branch**. You fix only what QA flagged, push to that **same** branch, and set
  `status: pr-updated`. **Do not open a second PR.** Jump to the **Fix mode** section near the end.

## What to do, in order  *(fresh build)*

### 1. Read your task and its context

1. Read `TASK_FILE` (absolute path). Note: `id`, `spec`, `priority`, `suggested_files_to_read`,
   `references`, `depends_on`.
2. Read the **spec** at the `spec:` path (relative, in your worktree). Focus on the acceptance
   criteria and Out-of-Scope — your task implements the slice of this spec described by your task
   `title`. Do not implement anything in Out-of-Scope.
3. Read **`CLAUDE.md`** (relative) — stack, conventions, and the **Definition of Done** (lint,
   typecheck, tests, etc.). This is the bar you must clear before opening a PR.
4. Read the files in `suggested_files_to_read` and `references` (relative paths in your worktree).
   Per the references philosophy, the task gives you pointers, not content — fetch what you need.

If anything you need to proceed is missing or genuinely ambiguous (spec contradicts itself, a
referenced file doesn't exist, the task is underspecified to the point you'd be guessing), **do not
guess** — go to the **Blocked** section below.

### 2. Implement

Make the real change in your worktree, following the conventions in `CLAUDE.md`. Stay within your
task's scope (one task = one coherent PR). Don't fix unrelated things you notice — that's scope
creep; if it's a real out-of-scope bug, mention it in the PR body, don't fix it.

### 3. Satisfy the Definition of Done

Run whatever `CLAUDE.md` defines as done — lint, typecheck, test suite. Use the exact commands it
specifies. **Fix what you broke** until they pass. If `CLAUDE.md` specifies no checks (e.g. a toy
project), skip this step. If a check fails for a reason you cannot fix from what's documented, go to
**Blocked**.

### 4. Commit on your branch

Stage only the files your task touched and commit on your `task/<id>` branch:

```bash
git add <the files you changed>
git commit -m "<id>: <concise description of what you implemented>"
```

One coherent commit is ideal; a few is fine. Never commit the task file or any control-plane state
(it isn't in your worktree anyway). Never commit secrets.

### 5. Open a PR (hybrid — real when possible, local marker otherwise)

Decide the mechanism by what's available:

**Real PR** — only if the repo has a remote AND `gh` is installed and authenticated:

```bash
git remote               # non-empty?
command -v gh            # present?
gh auth status           # authenticated?
```

If all three hold:

```bash
git push -u origin "task/<id>"
gh pr create --base "<pr_base>" --head "task/<id>" \
  --title "<id>: <title>" \
  --body "<short body: what changed, which ACs it satisfies, any out-of-scope notes>"
```

**`--base` comes from your task file's `pr_base` field** — the orchestrator set it to the per-spec feature integration branch (`feature/<spec-id>`); that's what your PR targets, **not** the repo default branch. (Every task in the spec PRs into the feature branch; the human merges the feature branch into the default branch once the spec is green.) If `pr_base` is null/empty (a project not using the feature-branch model), fall back to the repo default branch — omit `--base` and let `gh` use it. Your dependencies' code is already in `feature/<spec-id>` (the orchestrator merged each one in when it passed QA), so your branch — cut from that feature branch — already has it; if a dependency is especially relevant, mention its task id in the PR body for traceability.

Capture the URL `gh pr create` prints. Get the number with
`gh pr view --json number,url -q '.number'` (or parse the URL). These become `pr_url` / `pr_number`.

**Local marker** — if there's no remote, or `gh` is missing/unauthenticated (e.g. the harness test
project): skip the push/PR entirely. Your commit on the branch is the deliverable. Use:

- `pr_url: "local://pr/<id>"`
- `pr_number: null`

Do **not** invent a fake GitHub URL and do **not** error out because there's no remote — the local
marker is the intended fallback.

### 6. Write the QA recipe (how to drive what you built)

The async QA agent will **run your feature** and judge it against the task's `expected_behavior`
rubric. It does not read your diff — it needs *you* to tell it **how to exercise the feature you
just built**. Write that recipe to the feature's control-plane dir (absolute path, main worktree):

```bash
MAIN=$(dirname "$(git rev-parse --git-common-dir)")
FEAT_DIR="$MAIN/docs/active-features/<feature-id>"   # feature-id = the spec's frontmatter id
mkdir -p "$FEAT_DIR"
# write $FEAT_DIR/qa-instructions-<id>.md
```

The recipe is a **map, not a grade** — describe how to reach each behaviour and your *claimed* I/O,
never "this is correct" (the rubric is the grade). Match it to the layer:

- **Backend / API:** how to start it, the exact request(s) to send (method, path, payload, headers),
  and the response you expect back; any seed data or auth needed.
- **Frontend / UI:** how to launch it, how to navigate to the feature, the interactions to perform,
  and the visible result (e.g. "click *Save* → the modal closes and a success toast appears").
- **CLI / library:** the command(s) or function call(s) to run and their expected output/exit code.

Keep it concrete and runnable. The QA agent reads this by the absolute path you record next.

### 7. Flip your task file's status — `status` LAST

Edit `TASK_FILE` (the **absolute** path, with the `Edit` tool). **Order matters:** write every other
field *first*, then flip `status: pr-opened` as your **final, separate** edit:

1. Set `pr_url:` (real URL or `"local://pr/<id>"`) and `pr_number:` (real number or `null`).
2. Set `branch:` to your branch name (`task/<id>` — confirm via `git rev-parse --abbrev-ref HEAD`).
   This is what lets the Opus fixer find the branch in fix mode.
3. Set `qa_instructions:` to the **absolute** path of the recipe you wrote in step 6.
4. *Then* set `status: pr-opened`.

Why last: `status: pr-opened` is the **only** signal the orchestrator watches (it polls the
main-worktree task file, tears down your pane, moves the task to review, and spawns QA the moment
it sees `pr-opened`). Writing the other fields first guarantees that whenever a reader observes
`pr-opened`, `pr_url`/`branch`/`qa_instructions` are already populated — never a half-written
`pr-opened`.

Leave every other field as it is. Do not restructure the file or add fields (the schema at
`~/agent-harness/schemas/task.schema.yaml` forbids unknown keys).

### 8. Summary, then stop

Print one line: `worker <id>: implemented <what>, DoD <passed/skipped>, PR <url-or-marker>, status
pr-opened`. Then **stop**. Do not kill your own tmux pane — the orchestrator tears it down. Do not
start another task.

## Fix mode (re-QA after needs-changes)

You're here because `status: qa-failed` and `branch` is already filled. QA ran your (or a prior
worker's) PR, found **fixable** problems, and you've been spawned **on the existing `task/<id>`
branch** to address them. You are the one-and-only retry — the next QA pass is terminal.

1. **Read what QA flagged.** Read `TASK_FILE`'s `qa_report` (absolute path) — specifically the
   *"what the Opus fixer must address"* items. Also re-read `expected_behavior` (the rubric you must
   satisfy) and `qa_instructions` (the recipe) for context.
2. **Fix only that.** Make the smallest change that makes the flagged behaviour match the rubric.
   Do **not** re-architect, expand scope, or fix unrelated things. If QA's report shows the problem
   is *fundamental* (you'd have to rebuild the wrong design) or the spec is genuinely ambiguous —
   don't force a guess; go to **Blocked** so it escalates to the human.
3. **Satisfy the Definition of Done again** (lint/typecheck/tests per `CLAUDE.md`).
4. **Commit on the same branch** (`task/<id>`): `git add <changed>` &amp;&amp; `git commit -m "<id>: fix — <what>"`.
5. **Push to the EXISTING branch — no new PR.** If a remote + authenticated `gh` exist:
   `git push origin "task/<id>"` (the open PR updates automatically). No `gh pr create`. Local-marker
   projects: the commit is the deliverable, nothing to push.
6. **Update the recipe** (`qa_instructions` file) only if your fix changed how to drive the feature.
7. **Flip status — `status` LAST.** Edit `TASK_FILE`: leave `pr_url`/`pr_number`/`branch` as they are
   (same PR), then set `status: pr-updated` as the final edit. That's the signal the orchestrator
   watches to tear you down and trigger **re-QA**. Do not touch `qa_failure_count` (the orchestrator
   owns it) or `status` values other than `pr-updated`/`blocked`.
8. Print `worker <id>: FIX pushed to task/<id>, status pr-updated` and **stop**.

## Blocked

If you hit a gap you can't resolve from what's documented — at any step — do not guess and do not
ship a half-implementation:

1. Record the reason where the orchestrator/ops agents can read it: the feature's progress doc,
   which is **control-plane** (absolute path in the main worktree, may not exist yet):

   ```bash
   MAIN=$(dirname "$(git rev-parse --git-common-dir)")
   # feature slug = the spec's frontmatter id, e.g. feature-greeting
   FEAT_DIR="$MAIN/docs/active-features/<feature-id>"
   mkdir -p "$FEAT_DIR"
   ```

   Append a `## Blockers` entry to `$FEAT_DIR/progress.md` (create it if absent) describing the
   blocker for `<id>` in one short paragraph: what you needed, why you couldn't proceed, what would
   unblock you.
2. Edit `TASK_FILE` (absolute path) to set `status: blocked`. Leave other fields alone. (The schema
   has no free-text field on the task itself — the reason lives in `progress.md`.)
3. Print `worker <id>: BLOCKED — <one-line reason>` and stop. The orchestrator leaves blocked tasks
   for the blocker-resolver; do not retry in a loop.

## Rules

- **One task, real implementation, in scope.** Implement the slice your task describes; don't
  exceed the spec or touch Out-of-Scope. Don't refactor unrelated code.
- **Absolute path for the task file, relative paths for everything you build against.** Never read
  or write the task file by a relative path; it isn't in your worktree.
- **Never commit control-plane state or secrets.** Your commits contain code/docs deliverables only.
- **Don't push or open a real PR unless a remote + authenticated `gh` exist.** Otherwise use the
  local marker. Pushing is the only action that leaves your machine — gate it on a real remote.
- **The status flip is the contract.** `pr-opened` (or `blocked`) in the task file is how the
  orchestrator learns you're done. Get the commit/PR in first, flip status last.
- **Don't kill your pane or spawn anything.** Implement, signal, stop.
