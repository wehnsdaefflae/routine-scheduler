---
tags: [tool-use, utils, discovery]
---
# fragment: global utils — your tools, and how to build more

You have NO shell. Every bit of code you run is a **global util** — a small, named,
selftested script shared across all routines.

**Discover, then use.** The available utils are NOT listed in your prompt — run the `util`
action with name `list` to see every util and its one-line summary. Pick the one you need and
run it with the `util` action (`name` + optional `args`; append `--json` for structured
output you can parse); its `usage:` line names its flags. Prefer an existing util over
building one.

**Composing.** Utils call each other, so prefer building on what exists: a new util can shell
out to `gu <other-util> --json` internally. Don't reimplement a capability another util
already provides.

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
one-off. If creating/revising a util needs approval for this routine, a blocking question is
filed automatically; plan around the wait.
