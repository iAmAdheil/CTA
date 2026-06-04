# Misc Reference Docs

Arbitrary reference files used to steer agent work. Any format: HTML wireframes, screenshots, Figma exports, CSVs, PDFs, flow diagrams, design tokens, example data, API response samples.

The key property of everything in this directory: **it is linked from a task or spec file**. Files here with no inbound links are dead weight — delete them.

## Subdirectories

```
11-misc/
├── wireframes/     ← HTML, SVG, or image wireframes — layout and interaction intent
├── screenshots/    ← reference screenshots, design mockups, "build it like this" images
├── flows/          ← user flow diagrams, state diagrams, sequence diagrams
└── [add more as needed — data/, examples/, exports/, etc.]
```

## How to link from a task file

Add a `references` field to the task YAML:

```yaml
# tasks/data-export/backlog/TASK-051.yaml
id: TASK-051
title: "Export modal UI"
spec: "docs/01-specs/feature-data-export.md"
references:
  - docs/11-misc/wireframes/export-modal.html
  - docs/11-misc/screenshots/export-design-v2.png
  - docs/11-misc/flows/export-user-flow.md
```

The worker agent reads these files at the start of the session alongside the spec and CLAUDE.md. The task handoff prompt from the orchestrator explicitly tells the agent: "Read all files listed under `references` before writing any code."

## How to link from a spec file

Add a `references` section at the bottom of the spec:

```markdown
## References

- [Export modal wireframe](../11-misc/wireframes/export-modal.html)
- [Design mockup v2](../11-misc/screenshots/export-design-v2.png)
```

## Naming convention

```
{feature-slug}-{description}.{ext}

export-modal-wireframe.html
dashboard-layout-v3.png
onboarding-flow-diagram.md
auth-sequence.svg
```

Include the feature slug so it's clear what each file is for when you're looking at the directory.

## HTML Wireframes

HTML wireframes are the most useful format here — agents can read HTML directly and understand layout, component hierarchy, and interaction intent better than from a description. Keep them self-contained (inline CSS, no external dependencies) so the agent can read them as a single file.

A minimal useful wireframe:

```html
<!-- export-modal-wireframe.html -->
<!-- Wireframe: Export Modal
     Feature: Data Export
     Status: Approved for implementation
-->
<!DOCTYPE html>
<html>
<head>
  <style>
    /* ... inline styles ... */
  </style>
</head>
<body>
  <!-- Layout with annotated comments explaining intent -->
</body>
</html>
```

Add comments that explain *intent*, not just layout. "This button triggers an async job — show a spinner, not a page redirect" is more useful to the agent than just marking where the button is.
