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

Inside your own directory the jail refuses what the file actions refuse — the memory seal, the
engine-owned record directories, `routine.yaml` and your recipe — so a redirect at one of them
fails with a kernel error, not a readable refusal. Touch those through the actions that own them.
Creating a NEW top-level entry in your own directory fails the same way: make it with
`write_file` or `mkdir`, then shell may write inside it. Elsewhere a write passes no gate at
all, and the record only says it may have happened.
A command you run every run (a disk check, a repo status) repeats even when no single run
repeats it: make it a script.
