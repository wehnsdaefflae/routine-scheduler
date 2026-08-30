---
tags: [self-management, authoring, recipe]
requires:
  actions: [write_recipe]
---
# permission: recipe authoring — revise this routine's own instructions

Unlocks writing this routine's OWN recipe: `main.md`, `stages/` and `tuning.yaml`. Everything
else about the routine stays as it was — `routine.yaml` is config and no run edits it, so
permissions, capabilities, budgets, roots and schedule are still the user's alone.

Hold this only where rewriting the instructions IS the job: a routine that improves recipes,
or one whose own procedure it is meant to refine from what its runs learn. An ordinary routine
that finds its instructions wrong reports that instead — a run that can silently reword its own
task can also silently change what it is for, and the next run has no way to notice.

When you do revise: change the smallest thing that fixes the observed problem, say in the run
summary what you changed and why, and never delete a constraint you merely found inconvenient.
The recipe is versioned in the routine's git repo, so a bad edit is recoverable — but only if
the summary says it happened.
