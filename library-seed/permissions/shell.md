---
effect:
  with: runs ad-hoc commands on the host, inside its sandbox
  without: runs code only through the shared utils and its own scripts
  when: its work needs host inspection, builds or one-off commands no util covers
tags: [tool-use, shell, escape-hatch]
requires:
  actions: [shell]
---
# permission: shell — run ad-hoc host commands

A shell write or delete passes none of the file actions' gates: the `.memory/` seal, `runs/`,
`routine.yaml`, your recipe. Touch those only through the actions that own them, or not at all.
A command you run every run (a disk check, a repo status) repeats even when no single run
repeats it: make it a script.
