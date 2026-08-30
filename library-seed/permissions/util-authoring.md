---
effect:
  with: write a new util into the shared library, which every routine can then call
  without: uses only the utils that already exist
  when: it keeps needing a tool nobody has written yet
tags: [tool-use, utils, authoring]
requires:
  actions: [write_util]
---
# permission: util authoring — create a new global util

Unlocks CREATING a util: when no existing util fits, write one (single-purpose, reusable — never
a one-off); when a util is broken, repair it (read its source first: `util` name `show`, args
`["<name>"]`). **Placement test — util or script?** A util is GLOBAL, for every routine; a
script is for one routine only. Would another routine plausibly call this capability? NO —
it is your own pipeline work (your polling, your parsing, your artifact) → it belongs in
your own `scripts/` dir (the scripts permission), never in the shared library where it
clutters every routine's catalog. YES → it belongs here. Whether a change needs approval is the routine's write_util approval level
(user-set; a required approval files a blocking question automatically — batch other work
while it waits). Check the catalog first (`util name=list`) so you never duplicate a
capability; report every util you created or changed in the finish summary. The engine
selftests every script before committing, and rejects an incomplete docstring header:
`tags:`, every credential env var read on `secrets:` (only declared secrets reach the util's
env), siblings exec'd via `gu` on `calls:`, and `net: outbound` or `net: none` — utils run in
a filesystem/network sandbox; undeclared network = no TCP. NEVER recreate a util the user
deleted (a slug with a deletion in the library's history is rejected): ask_user first, mode
blocking, naming the util and why — only an explicit yes in the same run unblocks it.

Revising an existing util (`util-revision`) and deleting one (`util-removal`) are SEPARATE permissions. You emit the same action for create and revise — the engine decides which it is from whether the name already exists in the library, and refuses the half you do not hold. Holding this doc alone lets you add new utils, never touch the ones other routines already depend on.
