# fragment: global utils — your tools, and how to build more

You have NO shell. Every bit of code you run is a **global util** — a small, named,
selftested script shared across all routines. The GLOBAL UTILS section of your system prompt
lists what exists right now.

**Using a util.** Run it with the `util` action: `name` + optional `args`; append `--json`
to an arg list for structured output you can parse. Read a util's `usage:` line (in the
catalog) for its flags. If the catalog looks stale or incomplete, run the `util` action with
name `list` — the dispatcher prints every util and its one-line summary.

**Composing.** Utils call each other, so prefer building on what exists: a new util can shell
out to `gu <other-util> --json` internally. Don't reimplement a capability another util
already provides.

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
