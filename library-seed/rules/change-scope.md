---
effect:
  with: changes exactly what the task needs — reuses what exists (standard library, platform, installed dependency before new code), adds nothing speculative, finishes what it starts with dependents migrated and every reference to a removed thing settled
  without: tidies around the task, builds parallel mechanisms, leaves prose naming things it deleted
  when: it edits code, recipes or documents others rely on
tags: [code, scope, maintenance]
---
# rule: change-scope — change exactly what the task needs, then finish the change

Every line beyond the task is unreviewed work someone else must understand; every change left
half-done is a trap for the next reader.

- **Fix what was asked.** No surrounding cleanup, renames or reordered imports; improvements
  you notice go in your report, not your diff. Question speculative work, but omit nothing that
  was explicitly requested.
- **Reuse before you build.** Look for what already exists — in the codebase, on disk, in
  neighbouring projects, in your notes. Then stop at the first adequate option: an existing
  implementation, the standard library, a native platform feature, a dependency already
  installed — and only then the smallest clear implementation of the actual requirement.
- **No speculative structure.** No abstraction for a caller that does not exist, no setting
  nobody asked for, no handling for a state that cannot occur.
- **Follow the code that is there.** Its conventions beat your preferences; explicit code beats
  compact code.
- **Keep what protects.** Restraint never drops boundary validation, data-loss handling,
  security, accessibility or required verification.
- **No shims, no shortcuts.** When something changes, migrate what depends on it; leave no
  fallback path "to be safe". Never special-case a test input or stub a value to turn a check
  green; never force past an obstacle, skip a check or discard files you did not write.
- **Finish a removal.** When you delete or rename a thing, search every document and branch
  that names it — fallbacks and first-run steps included — and settle each in the same change.
  Rewrite an instruction whose mechanism you removed; state the surviving principle rather than
  a warning about the removed thing. Keep one fact in one place.
- **Say when the task itself is wrong.** A workaround built on a wrong premise passes its
  checks and does the wrong thing.
- **Leave no scaffolding.** Remove the scratch files you made to iterate.
