---
tags: [tool-use, utils, authoring]
requires:
  actions: [write_util]
---
# permission: util authoring — create and revise global utils

Unlocks the `write_util` action: when no existing util fits, write one; when a util is
broken, repair it (read its source first: `util` name `show`, args `["<name>"]`). Whether
a change needs the user's approval is the routine's write_util approval level (a
capability the user sets: every change / new utils only / fully autonomous) — when
approval is required, a blocking question is filed automatically; plan around the wait
and batch other work while it is pending. Autonomy raises the bar, not lowers it: check
the catalog first (`util name=list`) so you never duplicate a capability under a new
name, and report every util you created or changed in the finish summary. The engine
selftests every script before committing it; the write_util action description states the
full script contract (PEP 723 header, docstring with usage/tags/secrets lines, `--json`,
`--selftest`). The docstring is the machine-read surface: `tags:` is required, and every
credential env var the code reads must appear on its `secrets:` line — the engine rejects
the save otherwise. Utils are a shared toolbox: single-purpose, reusable, never a one-off.
