---
tags: [decomposition, workflows, self-management]
requires:
  workflows: generate
---
# permission: workflow generation — draft a new pattern when none fits

Lets a run DRAFT a brand-new library workflow pattern for a `subtask` (or `spawn`) when no
existing one fits its purpose — instead of only picking from the CAPABILITIES catalog. When
you set a subtask's `workflow` to `generate`, the engine writes a self-contained Python
pattern for that child's brief (lint-gated, committed to the library, immediately in
circulation) and materializes the child from it. Use it sparingly: it costs a system-model
call (two full-context completions) that hits this run's token/cost budget, and it grows the
shared library. Reach for it only when the decomposition genuinely needs a control-flow shape
the catalog lacks; otherwise name the closest existing pattern. If the run's budget is nearly
spent the engine skips generation and falls back to the default pattern.
