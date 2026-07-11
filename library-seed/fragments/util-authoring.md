---
tags: [tool-use, utils, authoring]
grants:
  actions: [util, write_util]
  confirm: true
---
# fragment: util authoring — create and revise global utils, user-approved

This standard GRANTS the `write_util` action: when no existing util fits, write one; when a
util is broken, repair it. Every create/revise asks the user for approval first (a blocking
question is filed automatically) — plan around the wait, and batch other work while it is
pending. Activate at most one util-authoring standard per routine (this one, or the
autonomous variant).

**Repair before workaround.** A util that errors mid-run is a repair opportunity, not a
detour: read its source (`util` with name `show`, args `["<name>"]`), fix it, and `write_util`
the corrected script — selftest-gated and committed, so the fix benefits every routine from
now on. Never silently work around a broken util; the next routine hits the same wall. If the
failure is environmental — a missing system package, no browser libraries, hardware — no
script can fix it: file a deferred `ask_user` naming exactly what is missing, so the operator
(or the self-audit routine) can fix the image/host.

**Creating or revising a util** (the `write_util` action) — when nothing fits, write one.
The `content` must be a complete, self-contained script that conforms to the util standard,
or the engine's selftest gate rejects it:

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
