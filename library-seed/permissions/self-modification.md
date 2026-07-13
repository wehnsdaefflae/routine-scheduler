---
tags: [self-management, improvement, recipe]
grants:
  self_modify: true
---
# permission: self-modification — edit the routine's own recipe mid-run

Unlocks `write_file` on the routine's own recipe files: `main.md`, `steps/`, `traits/`,
and `instruction.md`. NOT held by default: recipe improvement is the routine-improver
meta routine's job, done centrally with fresh eyes — by default only it holds this
permission (for its own recipe; it reaches other routines through its filesystem roots).
Grant it to another routine only when the task genuinely requires self-rewriting mid-run.
Changing the instruction's GOAL or a hard constraint is never the routine's to decide —
that is a deferred ask_user. Without this permission the engine rejects recipe writes;
state/, outputs, and LEDGER stay writable as ever.
