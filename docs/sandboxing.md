# Util sandboxing — the trust boundary around code execution

Routines have no shell: the only way a run executes code is a **global util**. The engine
enforces its capability boundaries at the action layer (`read_file` is jailed to the
routine's roots, `write_file` to its write roots) — but a util is a real subprocess, and
without its own boundary it would run with the daemon user's full filesystem and network
access. The demonstrated bypass: `read_file` on a path outside the roots is rejected, yet
`gu page-fetch file:///same/path` returned it — any util could read anything (including
`~/.config/routine-scheduler/secrets.env`) and any network-capable util could exfiltrate
it after a prompt injection.

Every util subprocess therefore runs inside a **Landlock jail** (the kernel LSM,
unprivileged, inherited by all descendants — a util shelling out to `gu <sibling>` stays
jailed) plus **scoped secrets injection**. Three cooperating layers:

- `rsched/landlock.py` — the kernel binding and the child wrapper. The wrapper runs as
  `python landlock.py '<spec>' -- uv run --script …`, applies `no_new_privs` + the
  ruleset, then execs the util in place. It is **always strict**: if the jail can't close
  it exits 97 and the util never runs — degradation is decided daemon-side, never in the
  child. (Hand-rolled stdlib ctypes on purpose: the PyPI `landlock` package is dev-status
  and lacks the ABI-4 network rules; evaluated 2026-07-17.)
- `rsched/sandbox.py` — the policy layer: derives the visible filesystem from the run,
  decides strict/permissive/off, assembles the spec, wraps the command.
- `rsched/utils_lib.py` — the dispatch seam: every `run_util` call (the `util` action,
  the vision fallback, `write_util` selftests, the web Library editor's selftest, the
  notify channel) takes a `SandboxPolicy` and builds the scoped environment.

## What a util can see

Derived from the RUN's permissions, per dispatch:

- **read+write** — the routine's own dir, its `fs_write_roots`, `/tmp` + `/var/tmp` +
  `/dev`, and tool state: `~/.cache` (uv script envs, playwright browsers),
  `~/.local/share/uv` (managed pythons), `~/.local/state`, `~/.claude` + `~/.claude.json`
  (the claude CLI's session state — the same state the claude-cli endpoint uses).
- **read+execute** — the routine's `fs_read_roots`, the util library itself, the system
  trees (`/usr /bin /sbin /lib* /etc /opt /run /sys /proc /var/log`), the daemon's venv,
  `~/.local/bin` (uv on host installs), and the git/gh identity files (`~/.gitconfig`,
  `~/.config/git`, `~/.config/gh`) so git-workflow utils can still push.
- **invisible** — everything else. In particular the daemon user's HOME:
  `~/.config/routine-scheduler` (config + the central secrets store), `~/.credentials`,
  `~/.ssh`, browser profiles, other apps' data.

Known tradeoffs, accepted and documented: `/proc` is readable (headless chromium needs
it), so keep secrets out of the daemon's environment — the compose file already prefers
file-based credentials; `~/.config/gh` is readable so utils can push over the gh
credential helper — treat the gh token as toolchain-grade, like the claude session.

## Network — a per-util declaration

The docstring header (the util's only machine-read surface) declares network need:
`net: outbound` (TCP unrestricted) or `net: none` (ALL TCP bind+connect denied, Landlock
ABI ≥ 4). **Undeclared = none — fail closed**; `header_problems` rejects a new util
without the line. Landlock cannot restrict UDP/ICMP today: the network boundary is
TCP-only — honest, not oversold. Sibling calls resolve transitively: `util_needs` walks
the `calls:` graph, so a util calling a `net: outbound` sibling gets (and needs) the open
network, and inherits the sibling's declared secrets.

## Secrets — declared-only injection (every mode)

`_child_env` injects from the central store ONLY the vars the util (or a `calls:`
sibling, transitively) declares on `secrets:`; every other store key is scrubbed even
when the daemon's own environment carries it. `STRIP_VARS` (the LLM billing keys) never
pass, declared or not. This layer needs no kernel support and applies even with
`sandbox: off`. Blast radius after both layers: a prompt-injected util can leak at most
its own declared secrets, not the store.

## The mode — config.yaml `sandbox:`

- `permissive` (**default**) — jail whenever the kernel supports Landlock; warn once in
  the daemon log and run unsandboxed when it doesn't. A capable host is protected, an
  incapable one keeps working.
- `strict` — refuse to run utils unsandboxed: the util call returns an error observation
  naming the fix. Same for a `net: none` util on a fs-only (ABI < 4) kernel.
- `off` — never wrap (pre-0.61 behavior). Secrets scoping still applies.

Verified 2026-07-17 on the production deployment: Landlock ABI 4 (filesystem + TCP) is
fully functional **inside the rsched Docker container** under Docker's default seccomp
profile (kernel 6.8) — no compose changes needed.

## Never recreate a user-deleted util

A related trust rule with the same shape (the user's deliberate act outranks a run's
convenience): `write_util` for a slug whose `utils/<name>/main.py` has a **deletion in
the library's git history** is rejected inside the schema-retry cycle (never costs a
turn). The correction tells the model to `ask_user` (blocking) naming the util; an
explicit yes in the same run unblocks the recreate (`interact.recreate_denial`, probe:
`utils_lib.was_deleted`). Any prior deletion counts — the web UI is the only deliberate
delete path, so every deletion is user intent. The boot seed-sync obeys the same rule:
a user-deleted seed util is never resurrected (`bootstrap.sync_seed_utils`).

## Migration (one-shot, expires 2026-08-17)

`bootstrap.migrate_util_headers` runs at daemon boot until deleted: pre-sandbox utils
gain `net: outbound` (behavior-preserving — tighten per util from there), `calls:` lines
seeded from literal `["gu", "<name>"` invocations, and undeclared credential env vars
appended to `secrets:`. Idempotent; committed to the library repo once.
