---
effect:
  with: run the shared toolbox of utils
  without: cannot run any code at all — there is no shell behind it
  when: always, unless the routine is pure prose and touches nothing
tags: [tool-use, utils, discovery]
requires: {}
---
# permission: global utils — the shared toolbox, and how to reach into it

You have NO shell: every bit of code you run is a global util — a named, selftested script
shared across all routines. The CAPABILITIES list names them at one-line-summary altitude,
which tells you WHAT exists but carries no flags. Before your FIRST call to a util, run the
`util` action with name `list` and args `["<util-name>"]` for that util's exact `usage:` line
(derived live, never stale) instead of guessing arguments; append `--json` when you want
output you can parse. Prefer an existing util to a new one, and prefer building on one to
reimplementing it — utils can call each other.

When a util errors, never silently work around it: the next routine hits the same wall. Read
its source (`util` name `show`, args `["<name>"]`) to confirm the fault, then either repair it
if you hold util authoring, or file a deferred `ask_user` naming the util, the failing call
and the error. A failure that is environmental — a missing system package, no browser
libraries, hardware — no script can fix: say exactly what is missing.
