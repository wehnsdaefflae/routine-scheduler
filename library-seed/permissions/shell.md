---
tags: [tool-use, shell, escape-hatch]
requires:
  utils: [shell]
---
# permission: shell — run arbitrary shell commands (escape hatch)

Unlocks the reserved `shell` util: one-off shell commands on the host
(`util` name `shell`, args `["<command>", "--json"]`; add `--timeout N` / `--cwd DIR`).
This is the escape hatch AROUND the no-shell design — hold it only for routines whose task
genuinely needs ad-hoc system access (builds, package queries, one-off host inspection).
Prefer a proper util for ANYTHING you run twice: a shell one-liner helps once, a util
helps every routine forever. Commands run non-interactively with the routine's
environment; long or destructive operations belong in a reviewed util, not a shell call.
