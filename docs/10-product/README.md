# Product Docs

Formal product documentation. These live above the spec layer — they describe the *why* and the *what* at a product level, before a feature gets broken into specs.

## What belongs here

- **PRDs** — Product Requirement Documents. The full scope of a product area or major initiative.
- **User flows** — how users move through the product (Markdown or linked from `11-misc/flows/`)
- **Personas** — who you're building for
- **North star metrics** — what success looks like for the product
- **Roadmap docs** — what's coming and in what order

## What doesn't belong here

- Feature specs (`01-specs/`) — those are derived FROM product docs
- Implementation decisions (`02-adrs/`) — those are engineering, not product

## How agents use these

Dev agents don't read product docs directly unless a spec or task file explicitly links to them. But the PM-you or Opus can reference them when writing specs or doing ADR checks — they're the source of truth for *intent* above the code level.

## PRD → Spec relationship

A PRD might cover a full product area (e.g. "User Account Management") that breaks down into many individual specs over time:

```
10-product/prd-user-accounts.md
  └─→ 01-specs/feature-signup.md
  └─→ 01-specs/feature-login-oauth.md
  └─→ 01-specs/feature-password-reset.md
  └─→ 01-specs/feature-account-deletion.md
```

The PRD is the parent context. Each spec is one slice of it.
