---
tags: [tool-use, utils, authoring]
grants:
  actions: [util, write_util]
  confirm: revisions-only
---
# fragment: util authoring autonomous — revise utils without approval, create with it

This standard GRANTS the `write_util` action with autonomy over REVISIONS: fixing or
extending an existing util is auto-approved the moment its selftest passes (every change is
still committed, so it is reviewable and revertible). Creating a NEW util still asks the
user first (a blocking question is filed automatically). Activate at most one util-authoring
standard per routine (this one, or the user-approved variant).

**Repair before workaround.** A util that errors mid-run is a repair opportunity, not a
detour: read its source (`util` with name `show`, args `["<name>"]`), fix it, and `write_util`
the corrected script — selftest-gated and committed, so the fix benefits every routine from
now on. Never silently work around a broken util; the next routine hits the same wall. If the
failure is environmental — a missing system package, no browser libraries, hardware — no
script can fix it: file a deferred `ask_user` naming exactly what is missing, so the operator
(or the self-audit routine) can fix the image/host.

**Revise conservatively.** Autonomy is for keeping the shared toolbox healthy, not for
repurposing it: preserve each util's name, purpose, CLI contract and output shape; fix the
fault, extend behind new optional flags, and strengthen the selftest to cover what broke.
A change another routine could be surprised by belongs behind an approval — ask anyway.

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
