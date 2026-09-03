# Util sandboxing — the trust boundary around code execution

A run executes code three ways, and all three land in the same jail: a **global util** (the
shared library), a **script** (the routine's own `scripts/<name>.py` helper) and — for the
one-off, behind the `shell` capability — the **`shell` action**. "Util" below names the
common case; every rule stated for a util holds for the other two, with the per-callable
differences noted where they matter. The engine
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
  notify channel) takes a `SandboxPolicy` and builds the scoped environment. Its two
  siblings pass the SAME policy through the same `sandbox.wrap`: `rsched/scripts.py`
  (`fs_roots=True`, the script's declared `net:`, its declared secrets) and
  `rsched/shellrun.py` (`fs_roots=True`, network open, **no secret at all** — a shell
  command declares nothing, so `scoped_env(set())` scrubs the whole store out of its
  environment). The `shell` action was a reserved util until 0.287.0 and that util
  declared `fs: roots` + `net: outbound`, the widest terms available — so its
  intersection term was already a no-op and the promotion to an action kind left the
  jail byte-for-byte where it was. What changed is only what can GENERATE the call: a
  gated kind is projected out of the schema, where `capabilities.utils` was an exception
  list that gated 6 of 114 utils.

## What a util can see

Derived from the RUN's permissions, per dispatch — the policy is rebuilt from the run's
EFFECTIVE roots (config `fs_*_roots` plus this run's one-time fs grants) on every util
call, so a mid-run fs grant answered on the Decisions page applies from the very next
call, live run included:

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
  `~/.ssh`, browser profiles, other apps' data. `~/.ssh` stays invisible even for the
  remote-machine feature: a bound machine's private key comes from the Secrets store
  (injected only into the `remote` util, declared-var gated), never from disk — see
  [remote-machines](remote-machines.md).

Known tradeoffs, accepted and documented: `/proc` is readable (headless chromium needs
it), so keep secrets out of the daemon's environment — the compose file already prefers
file-based credentials; `~/.config/gh` is readable so utils can push over the gh
credential helper — treat the gh token as toolchain-grade, like the claude session.

## Network — a per-util declaration

The docstring header (the util's only machine-read surface) declares network need:
`net: outbound` (TCP unrestricted) or `net: none` (ALL TCP bind+connect denied, Landlock
ABI ≥ 4). **Undeclared = none — fail closed**; `header_problems` rejects a new util
without the line. Landlock cannot restrict UDP/ICMP today: the network boundary is
TCP-only — honest, not oversold. It also cannot restrict a *destination*: the rules are
per-port, and the spec carries `net` as a bool, so `outbound` means the whole internet. A
capability that must egress a particular way — `darknet`, which has to reach Tor and nothing
else — gets that property from the util's own code, not the kernel (see
[darknet](darknet.md)). Sibling calls resolve transitively: `util_needs` walks
the `calls:` graph, so a util calling a `net: outbound` sibling gets (and needs) the open
network, and inherits the sibling's declared secrets.

## Filesystem — a per-util declaration too

The same header declares what the util needs to SEE, on the same terms as `net:`:

- `fs: roots` — the run's granted read/write roots wholesale. The right answer for a util
  that opens paths its CALLER hands it (`dir-tree ROOT`, `batch-replace MANIFEST`), and what
  every util got before this axis existed.
- `fs: none` — nothing beyond the always-mounted base (the routine's own dir, tmp, the
  toolchain caches). A util that only talks to an API needs no more.
- `fs: rw <path>` / `fs: ro <path>` — a PRIVATE store the util reaches on its own rather than
  being told about: a messenger's session directory, a state file. `$VAR` and `~` are allowed
  and resolved DAEMON-side, never from the run's environment — a run that could set the
  variable could aim the mount.

**Undeclared = none — fail closed**, and `header_problems` rejects a util without the line.
Entries combine (`fs: roots, rw $SIGNAL_SESSION_DIR`), and they resolve transitively over
`calls:` exactly as secrets and network do.

### A declaration only ever subtracts

A declared path is mounted **only when the run already holds a grant covering it**. Declaring
one asks for nothing. That is what keeps the axis safe in a system where a routine can author
its own utils: a util declaring `rw ~/.ssh` mounts nothing, because no grant covers it (and
`entities.never_grantable_fs` would refuse the grant anyway).

### Why a private store is subtracted from `roots`

The case the axis exists for is a credential that lives in a directory. Signal, Telegram and
WhatsApp authenticate by a LINKED SESSION on disk — the session store *is* the credential.
Before this, the only way to give `signal` its store was a routine-wide `fs_write_root`, and
the jail mounted the run's roots into **every** util it called: granting Signal its session
directory handed the Signal identity to every other util in the same run, which is a
prompt-injection away from exfiltration.

So a path some util claims as private is removed from the wholesale `roots` mount
(`sandbox.private_store_paths`, computed across the library and cached on the newest util
directory mtime). Claiming a path private is a statement about the PATH, so it binds every
util that did not claim it. The grant stays exactly what it was — one explicit, auditable,
four-state decision on `fs-write:<path>` — but its blast radius is now one util instead of all
of them.

The routine's OWN directory is held apart from all of this (`SandboxPolicy.own_dir`): it is the
working directory relative paths resolve against, not a grant to be narrowed, so every util
keeps it whatever its `fs:` line says.

## Secrets — declared-only injection (every mode)

`_child_env` injects from the stores ONLY the vars the util (or a `calls:`
sibling, transitively) declares on `secrets:`; every other store key is scrubbed even
when the daemon's own environment carries it. `STRIP_VARS` (the LLM billing keys, and the
SSH-agent vars `SSH_AUTH_SOCK` / `SSH_AGENT_PID` — so a forwarded agent can't route around
the per-routine machine binding) never pass, declared or not. The engine also injects a few
per-run secrets through this SAME gate — the routine's OWN scoped secrets (D103), its OAuth
connection tokens (`<PROVIDER>_ACCESS_TOKEN`) and its bound remote machines
(`RSCHED_MACHINES` / `RSCHED_MACHINE_KEYS`) — each reaching a util only if the util declares
the var. This layer needs no kernel support and applies even with `sandbox: off`. WHICH
CENTRAL store secrets a routine's calls may receive at all is the user's per-routine decision
(the four-state grant rows `secret:<NAME>` in routine.yaml `grants:` — an undecided REQUIRED
name files a blocking access request on first use; docs/rules-permissions.md). A
ROUTINE-SCOPED secret (`secrets.d/<slug>.env`, the routine page's *Own secrets* section) has
no such decision: it is the routine's own, implicitly exposed to its runs and to no other
routine, and it SHADOWS a central value of the same name — so those engine extras winning
the merge is the shadowing rule, not an accident. It stays under the declared-only gate like
everything else. An **OPTIONAL** secret
(declared `NAME?`, D51/F290 — it backs a feature most calls don't use, like page-fetch's
Basic auth) never files that request: not granted → it is WITHHELD from the child env and
the util observation says so, so a public call runs prompt-free and an auth-needing one
requests exposure explicitly (`ask_user` with `request: "secret:NAME"`). Blast radius
after both layers: a prompt-injected util can leak at most its own declared secrets, not
the store.

**Never run `uv` in the container as root.** `docker exec` defaults to root, and uv creates a
per-script environment under `~/.cache/uv` at every util call — so one root-run `uv` leaves
`environments-v2/` root-owned inside a tree the uid-1000 daemon must write, and EVERY util call
in the instance then dies with `Permission denied` (observed 2026-08-26). Use
`docker exec -u 1000:1000`. The entrypoint repairs the ownership on start, so a restart heals it.

## The mode — config.yaml `sandbox:`

- `permissive` (**default**) — jail whenever the kernel supports Landlock; warn once in
  the daemon log and run unsandboxed when it doesn't. A capable host is protected, an
  incapable one keeps working.
- `strict` — refuse to run utils unsandboxed: the util call returns an error observation
  naming the fix. Same for a `net: none` util on a fs-only (ABI < 4) kernel.
- `off` — never wrap (pre-0.61 behavior). Secrets scoping still applies.

Set it in **Settings → Server** (takes effect on the next util call) or in `config.yaml` directly.

Verified 2026-07-17 on the production deployment: Landlock ABI 4 (filesystem + TCP) is
fully functional **inside the rsched Docker container** under Docker's default seccomp
profile (kernel 6.8) — no compose changes needed.

## Never recreate a user-deleted util

A related trust rule with the same shape (the user's deliberate act outranks a run's
convenience): `write_util` for a slug whose `utils/<name>/main.py` has a **deletion in
the library's git history** is rejected inside the schema-retry cycle (never costs a
turn). The correction routes to a blocking ACCESS REQUEST for the entity
`recreate:<slug>` (docs/rules-permissions.md); an allow-now decision in the same run
unblocks the recreate (`interact.recreate_denial`, probe: `utils_lib.was_deleted`) —
deliberately with no allow-forever, so a fresh deletion always outranks an old grant. Any prior deletion counts — the web UI is the only deliberate
delete path, so every deletion is user intent. The boot seed-sync obeys the same rule:
a user-deleted seed util is never resurrected (`bootstrap.sync_seed_utils`).

## Migration (one-shot, expires 2026-08-17)

`bootstrap.migrate_util_headers` runs at daemon boot until deleted: pre-sandbox utils
gain `net: outbound` (behavior-preserving — tighten per util from there), `calls:` lines
seeded from literal `["gu", "<name>"` invocations, and undeclared credential env vars
appended to `secrets:`. Idempotent; committed to the library repo once.
