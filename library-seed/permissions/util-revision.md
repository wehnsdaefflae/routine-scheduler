---
effect:
  with: change a util the library already has, for every routine that calls it
  without: reports a broken util instead of repairing it
  when: it uses utils heavily enough to run into their bugs
tags: [tool-use, utils, authoring]
requires:
  actions: [revise_util]
---
# permission: util revision — change a util the library already has

Unlocks REVISING an existing util: repair it when it is broken, or extend it when it is
nearly right. Read its current source before you touch it, and prefer patching the exact
region over rewriting the file — a revision costs the diff, not the document.

Separate from `util-authoring` on purpose. Creating a util adds a capability nobody had, and
the worst case is clutter. Revising one changes what every routine already calling it gets,
silently, at their next run — the blast radius is the caller list, not this routine. So the
two acts are two decisions, and holding one never implies the other. You emit the same action
for both; the engine decides which this is from whether the name already exists, and refuses
the half you do not hold.

Because the callers cannot re-test themselves, the burden is here: keep every documented
invocation working, or you have broken a routine that never changed. Additive is safe —
a new subcommand, a new optional flag, a clearer error. Renaming a flag, changing a default,
or narrowing accepted input is a breaking change to somebody else's pipeline; when a change
cannot be additive, say so in the finish summary and name what might break. The engine
selftests before committing, and the approval level for revisions is the routine's
`write_util` dial.
