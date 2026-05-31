# QA Report — {{TASK_ID}} / {{FEATURE}}

<!--
This is BEHAVIORAL QA. You RUN the feature and judge what you OBSERVE.
Do NOT review the diff / critique code — that is the Review agent's job.

`status:` is the durable signal the orchestrator polls. Start at WIP and only
move it to a terminal verdict once you've actually exercised the feature.
-->

**status:** WIP   <!-- WIP → looks-good | needs-changes | escalate -->

**PR:** {{PR_URL}}
**Branch:** {{BRANCH}}            <!-- task/<id>, from the task file -->
**Rubric source:** task `expected_behavior` (authored by task-breakdown — the grading truth)
**Recipe source:** {{QA_INSTRUCTIONS_PATH}}   <!-- worker's how-to-drive map -->
**Run at:** {{TIMESTAMP}}

---

## How I ran it

- Launched via: {{project run/launch skill + command}}
- Drove the feature using the worker's recipe ({{QA_INSTRUCTIONS_PATH}}).

---

## Rubric results

One row per `expected_behavior` item (the implementation-agnostic contract). Judge
**observed behaviour vs the rubric** — the recipe only tells you *how to look*, never
*what counts as correct*. If the recipe's claimed outcome contradicts the rubric, side
with the rubric and flag it.

| Expected behaviour (rubric) | Observed | Result |
|-----------------------------|----------|--------|
| {{behaviour-1}}             |          | ✅ / ❌ |
| {{behaviour-2}}             |          |        |

Legend: ✅ matches rubric · ❌ does not

---

## The two checks

1. **Recipe executes as claimed?** — did the implementation do what the worker's recipe said it would? {{yes/no + details}}
2. **Claimed/observed behaviour satisfies the rubric?** — {{yes/no + details}}

---

## Verdict

**{{looks-good | needs-changes | escalate}}**

<!-- Decision rule (first pass):
- Every in-scope rubric item matches            → looks-good   (→ you merge)
- A BUG (right intent, broken execution:
  crashes / won't run / behaves wrong)          → needs-changes (always Opus, no judgment)
- A TRIVIAL rubric mismatch (clean execution,
  small miss a focused fix closes)              → needs-changes (Opus)
- A FUNDAMENTAL rubric mismatch (clean execution
  of the WRONG idea, or genuinely ambiguous)    → escalate     (→ the human)

The line: broken execution of the RIGHT idea → always retry (bug);
          clean execution of the WRONG idea → judge trivial (Opus) vs fundamental (you).
          "Fixable in one pass?" is asked ONLY on the mismatch path.

SECOND PASS (re-QA after an Opus fix): anything but looks-good → escalate. No more retries.
-->

### If `needs-changes`: what must the Opus fixer address
- {{specific, behaviour-level — "X should do Y, currently does Z"}}

### If `escalate`: why this needs the human
- {{the fundamental mismatch or ambiguity — the orchestrator copies this into the task's failure_reason}}

<!--
Out-of-scope bugs (real defects outside this task's rubric) are NOT handled here —
ignore them for the verdict. The future random-bugs subsystem owns those.
-->
