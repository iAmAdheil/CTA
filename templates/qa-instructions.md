# QA Recipe — {{TASK_ID}} / {{FEATURE}}

<!--
Written by the WORKER when it opens the PR. This is the MAP the async QA agent
uses to drive the feature you built — how to reach each behaviour and your
CLAIMED I/O. It is NOT the grading rubric: correctness is judged against the
task's expected_behavior, not against this file. Keep it concrete and runnable.
-->

**PR / branch:** {{PR_URL}} · `task/{{TASK_ID}}`
**Rubric (what QA grades against):** task `expected_behavior`

---

## How to launch

<!-- The exact commands to bring the feature up in a fresh worktree on this branch.
     e.g. `npm install && npm run dev`, or `python -m app`, or a project run skill. -->

```
{{launch commands}}
```

## How to exercise it

Match the layer of this task. One block per `expected_behavior` item where possible.

### Backend / API
- **Request:** `{{METHOD}} {{path}}` — body / headers / auth / seed data
- **Claimed response:** `{{status + body you expect}}`

### Frontend / UI
- **Navigate:** {{how to get to the feature}}
- **Interact:** {{the action, e.g. "click Save"}}
- **Claimed result:** {{visible outcome, e.g. "modal closes, success toast appears"}}

### CLI / library
- **Run:** `{{command or function call}}`
- **Claimed output / exit code:** {{...}}

---

## Notes for QA
- {{anything non-obvious: fixtures, env vars, pre-existing failures unrelated to this task}}
