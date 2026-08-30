---
effect:
  with: run its own helper scripts from scripts/, in its own virtualenv
  without: every repeated computation goes through the recipe and a shared util
  when: it repeats a deterministic step often enough to be worth writing down once
tags: [tool-use, scripts, authoring]
requires:
  actions: [script]
---
# permission: scripts — the routine's own persistent helper scripts

Unlocks `script`: run a PEP 723 script from this routine's OWN `scripts/` dir, in the
routine's persistent venv (`<routine>/.venv`, dependencies installed on demand). A
script is the recipe's TOOLING, never its peer: the recipe stays the single interpreter
of the task and delegates to a script only sub-steps that need no judgment — polling a
mailbox or feed, parsing, calculations over updated data, assembling a fixed artifact.
The moment you notice yourself repeating a deterministic sub-step across turns or runs,
write it into `scripts/` ONCE with `write_file` and call it thereafter: it persists in
the routine repo (revisions are cheap and reversible), runs reproducibly, and costs no
model work — never a throwaway you re-derive next run.

Author with `write_file` to `scripts/<name>.py`: PEP 723 dependencies, then a docstring
header — first line `<name> — <one-line summary>`, optional `usage:`, `net:
outbound|none` (undeclared = none → the sandbox denies all TCP), `secrets:` naming every
credential env var it reads (only DECLARED names are injected; `NAME?` marks an optional
one, withheld rather than prompted when not granted), and `calls:` naming every library
util it shells out to. Data on stdout, diagnostics on stderr, meaningful exit codes,
`--json` for structured output. Verify a new or revised script by RUNNING it before
relying on it, and name it in the finish summary.

**Placement test — script or util?** A script is for THIS routine ONLY; a util is
GLOBAL, for every routine. Would another routine plausibly call this capability? YES →
it belongs in the shared library (`write_util` — linted, selftested, discoverable by
all); NO — it is this routine's own pipeline work → it belongs here, never in the shared
library where it would clutter every other routine's catalog.

A script is private to this routine and runs inside the routine's sandbox: the same
filesystem roots as the recipe's file actions (recipe and script read and write the SAME
files), ONLY the granted secrets its header declares, and the library utils its `calls:`
line names — whose own secrets and network fold into that same sandbox, so a declared
util needs no second grant. An UNDECLARED util the code shells out to is refused, not
quietly run without its access. There is no model access: a judgment call belongs in the
recipe, and so does a capability the routine itself does not hold. A script
NEVER routes around a rule: behavior a rule gates — asking before an irreversible
outward act, evidencing a claim, recording a decision — stays under the recipe's
judgment, and authoring or invoking a script is itself rule-bound conduct. Keep each one
single-purpose.
