---
tags: [tool-use, utils, authoring]
requires:
  actions: [write_util]
---
# permission: util authoring — create and revise global utils

Unlocks `write_util`: when no existing util fits, write one (single-purpose, reusable — never
a one-off); when a util is broken, repair it (read its source first: `util` name `show`, args
`["<name>"]`). Whether a change needs approval is the routine's write_util approval level
(user-set; a required approval files a blocking question automatically — batch other work
while it waits). Check the catalog first (`util name=list`) so you never duplicate a
capability; report every util you created or changed in the finish summary. The engine
selftests every script before committing, and rejects an incomplete docstring header:
`tags:`, every credential env var read on `secrets:` (only declared secrets reach the util's
env), siblings exec'd via `gu` on `calls:`, and `net: outbound` or `net: none` — utils run in
a filesystem/network sandbox; undeclared network = no TCP. NEVER recreate a util the user
deleted (a slug with a deletion in the library's history is rejected): ask_user first, mode
blocking, naming the util and why — only an explicit yes in the same run unblocks it.
