# QA Report — {{TASK_ID}} / {{FEATURE}}

**PR:** {{PR_URL}}
**Spec:** {{SPEC_PATH}}
**Run at:** {{TIMESTAMP}}
**Pre-existing failures considered:** see progress.md

---

## Acceptance Criteria

| Criterion | Result | Notes |
|-----------|--------|-------|
| {{AC-1}}  | ✅ / ⚠️ WARN / ❌ | |
| {{AC-2}}  | | |

Legend: ✅ pass · ⚠️ partial/conditional · ❌ fail

---

## Automated Test Suite (Pass 1)

```
{{lint / typecheck / test output, mapped to ACs where possible}}
```

Pre-existing failures (not caused by this PR):

- {{list, or "none"}}

---

## Manual Navigation Test (Pass 2)

Browser steps executed via Stagehand. One block per AC item.

### {{AC-1}}
- Steps: {{describe}}
- Observed: {{describe}}
- Result: ✅ / ⚠️ / ❌

### {{AC-2}}
- ...

---

## Out-of-Scope Findings

Bugs or rough edges noticed that are not covered by any AC in this spec. The orchestrator may file these as `BUG-NNN` tasks.

- {{describe, or "none"}}

---

## Verdict

**{{PASS | CONDITIONAL PASS | FAIL}}**

<!-- Rules:
- All ACs pass and no out-of-scope issues → PASS
- All ACs pass but out-of-scope issues found → CONDITIONAL PASS (list above)
- Any AC fails → FAIL with the specific item flagged
-->
