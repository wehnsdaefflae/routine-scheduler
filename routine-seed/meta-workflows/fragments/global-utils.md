---
tags: [tool-use, utils, discovery]
grants:
  actions: [util]
---
# fragment: global utils — your tools, and how to use them

You have NO shell. Every bit of code you run is a **global util** — a small, named,
selftested script shared across all routines.

**Discover, then use.** The CAPABILITIES section of your prompt lists every util at
name + one-line-summary altitude — that map tells you WHAT exists, but it carries no flags.
Before your FIRST call to a util, run the `util` action with name `list`: it returns the
live catalog with each util's exact `usage:` line (derived live, so it can never be stale).
Then call it with the `util` action (`name` + optional `args`; append `--json` for
structured output you can parse). Prefer an existing util over building one.

**Composing.** Utils call each other, so prefer building on what exists: a new util can shell
out to `gu <other-util> --json` internally. Don't reimplement a capability another util
already provides.

**When a util errors, never silently work around it** — the next routine hits the same wall.
If this routine carries a util-authoring grant, repair it (that standard tells you how).
Otherwise read the source (`util` with name `show`, args `["<name>"]`) to confirm the fault,
then file a deferred `ask_user` naming the util, the failing call, and the error, so the
operator or a maintenance routine fixes it. If the failure is environmental — a missing
system package, no browser libraries, hardware — no script can fix it: file a deferred
`ask_user` naming exactly what is missing.

Creating or revising utils (`write_util`) is a separate, granted capability: it needs the
util-authoring standard active on this routine. Without it the engine rejects `write_util` —
work with the existing toolbox and escalate gaps via deferred `ask_user`.
