---
tags: [tool-use, utils, authoring]
grants:
  actions: [util, write_util]
  confirm: false
---
# fragment: util authoring full-auto — create and revise utils without approval

This standard GRANTS the `write_util` action with FULL autonomy: creating a new util and
revising an existing one are both auto-approved the moment the selftest passes — no user
question is filed. Every change is still selftest-gated and committed to the library repo,
so it is reviewable and revertible after the fact. This is the widest authoring grant there
is: activate it only on routines whose judgment you already trust, and at most one
util-authoring standard per routine (this one, the revisions-only variant, or the
user-approved one).

**Autonomy raises the bar, it does not lower it.** With nobody reviewing before the commit,
you review: before creating, run `util name=list` and reread the catalog — a near-fit you
extend beats a near-duplicate you add. Name every new util so its purpose is obvious from
the catalog line alone, and record in your finish summary (and LEDGER, if this routine keeps
one) every util you created or revised, so the user learns about new capabilities from the
run report rather than by surprise.

**Repair before workaround.** A util that errors mid-run is a repair opportunity, not a
detour: read its source (`util` with name `show`, args `["<name>"]`), fix it, and `write_util`
the corrected script — selftest-gated and committed, so the fix benefits every routine from
now on. Never silently work around a broken util; the next routine hits the same wall. If the
failure is environmental — a missing system package, no browser libraries, hardware — no
script can fix it: file a deferred `ask_user` naming exactly what is missing, so the operator
(or the self-audit routine) can fix the image/host.

**Revise conservatively.** Preserve each util's name, purpose, CLI contract and output
shape; fix the fault, extend behind new optional flags, and strengthen the selftest to cover
what broke. A change another routine could be surprised by still deserves a deferred
`ask_user` describing what you changed and why — autonomy means not waiting, not not telling.

**Creating or revising a util** (the `write_util` action) — the `content` must be a complete,
self-contained script that conforms to the util standard, or the engine's selftest gate
rejects it:

- **PEP 723 header** — `# /// script` … `# dependencies = [...]` … `# ///`. Declare any PyPI
  packages you need there; each util gets its own isolated environment, so dependencies are
  cheap — prefer a well-maintained package over hand-rolling.
- **Docstring header** — first line exactly `<name> — <one-line summary>` (em dash; the name
  matches the util), then a `usage: gu <name> ...` line, then a `calls:` line naming every
  util it invokes or `(none)`. The catalog is derived from this header.
- **I/O contract** — data on stdout (human-readable by default, JSON under `--json`);
  progress and diagnostics on stderr, never stdout; exit 0 on success, non-zero on failure.
- **`--selftest`** — a built-in check against fixture data that returns 0 when the util's core
  logic works, printing `selftest: ok` to stderr. The engine runs this before committing; a
  failing selftest means the util is NOT saved — fix it and write_util again.
- Factor a pure `run(...)` function (returns data, never prints) from the `main()` CLI wrapper,
  so `--selftest` can exercise `run()` offline.

Keep utils single-purpose and reusable — you are extending a shared toolbox, not writing a
one-off.
