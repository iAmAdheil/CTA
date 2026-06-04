---
kanban-plugin: basic
---

%% Per-feature TASK kanban for one feature workspace (tasks/<feature>/).
   Seeded by task-breakdown (all tasks land in Backlog); the orchestrator MOVES
   cards as a task's status: changes — it never regenerates the board.
   Columns are grouped by who owns the next action (the review/ folder's hidden
   states surface here as In Review / QA Failed / Human Review).
   Card format: - <TASK-NNN> — short title
   Status -> column map:
     backlog               -> Backlog
     in-progress           -> In Progress
     pr-opened, pr-updated -> In Review        (PR up; QA loop churning)
     qa-failed             -> QA Failed         (Opus fixer's turn)
     qa-passed,
       blocked-escalated   -> Human Review      (your turn)
     blocked               -> Blocked
     done                  -> Done %%

## Backlog



## In Progress



## In Review



## QA Failed



## Human Review



## Blocked



## Done



%% kanban:settings
```
{"kanban-plugin":"basic"}
```
%%
