---
id: feature-{slug}
title: ""
status: draft          # draft | approved | in-breakdown | blocked | superseded
priority: medium       # critical | high | medium | low
linear_epic_id: null
---

# {Title}

## Overview

<!-- One paragraph. What is this feature, who uses it, why now. Keep it short —
detailed mechanics belong in ACs or referenced ADRs. -->

## Acceptance Criteria

<!-- Numbered, testable. Each AC becomes:
  - one row in the QA report's AC table
  - input to the breakdown agent for shaping tasks
  - a checklist item in the worker's PR body

Each AC should be small enough that QA can verify it in a single browser flow
or test run. If an AC needs multiple sub-checks, split it. -->

1. AC-1: …
2. AC-2: …
3. AC-3: …

## Out of Scope

<!-- What this spec explicitly does NOT cover. Prevents scope creep during
breakdown and gives QA a basis for "out-of-scope finding" verdicts. -->

- …

## References

<!-- Files the breakdown agent and workers should read alongside this spec.
Per the harness's references philosophy, this spec should NOT restate content
from these files — just link to them. -->

- docs/adrs/ADR-XXX-…
- docs/11-misc/wireframes/…
- docs/11-misc/screenshots/…

## Implementation Notes

<!-- Optional. Constraints, performance targets, prior-art hints. Brief; link
to ADRs for anything load-bearing. -->
