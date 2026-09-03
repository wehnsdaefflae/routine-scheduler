---
effect:
  with: run arbitrary shell commands on the host
  without: runs code only through selftested, sandboxed utils
  when: the task genuinely cannot be done by a util — this is the escape hatch
tags: [tool-use, shell, escape-hatch]
requires:
  actions: [shell]
---
# permission: shell — run arbitrary shell commands (escape hatch)

Unlocks the `shell` ACTION: one ad-hoc command per turn (`command` as a single string, plus
optional `timeout_s` and `path` for the working directory), run through `bash -c` inside the
same Landlock jail a util gets — this routine's granted roots, no secret from the store, no
interactive input. This is the escape hatch AROUND the util library — hold it only for
routines whose task genuinely needs ad-hoc system access (builds, package queries, one-off
host inspection). Prefer a proper util for ANYTHING you run twice: a shell one-liner helps
once, a util helps every routine forever. Long or destructive operations belong in a
reviewed util, not a shell call.
