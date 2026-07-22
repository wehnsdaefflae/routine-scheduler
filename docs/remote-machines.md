# Remote machines

A **machine** lets a routine run commands and move files on a remote host over SSH — for work
that needs specific hardware the daemon box doesn't have (a GPU for training/inference, a big
build server). The operator registers a host once in the web UI; a routine BINDS it by name; the
run acts on it through the reserved `remote` util, never touching credentials directly.

Machines are a **resource binding**, like `models:`, `connections:`, and `fs_roots` — not a
capability. The `config.yaml` `machines:` catalog is operator-only; the routine.yaml `machines:`
list *is* the grant. No run creates or changes either (routine.yaml stays sealed). The `remote`
util also has to be switched on (the `remote-machines` permission), and a key only ever reaches a
util the routine explicitly binds.

## The split that makes it work

Like OAuth connections, the two halves live in different places because a run is headless and
sandboxed:

- **Enrollment — the daemon/web process.** Adding a machine, scanning its host key, and testing
  reachability happen in Settings → Machines. The host-key SCAN and the TEST run the real `remote`
  util server-side, so what Settings proves is exactly what a run gets.
- **Use — the run.** A run only READS the bound machines' connection details + private keys, which
  the engine injects as env vars (below). A run never edits the catalog.

## Where the pieces live

- **`config.py` `MachineConfig` + `ServerConfig.machines`** — the catalog, one entry per host:
  `host`, `user`, `port` (default 22), `key_var`, `host_key`, `share`, `workdir`, `description`,
  `tags`. Instance-wide config in `config.yaml`. **No secret lives here**: `key_var` names a
  Secrets-store key holding the private key (PEM); `host_key` is the server's PUBLIC host key.
- **The Secrets store** holds the actual private key under the `key_var` name (e.g.
  `GPUBOX_SSH_KEY`). Use an UNENCRYPTED key for a supported type (ed25519 / ecdsa / rsa). Set it on
  Settings → Secrets like any other secret.
- **`machines.py`** — `machines_for_routine(names, catalog)` resolves a routine's bindings +
  the Secrets store into the two env vars the `remote` util receives:
  - `RSCHED_MACHINES` — non-secret connection metadata (host/user/port/host_key/workdir/…).
  - `RSCHED_MACHINE_KEYS` — `{name: private-key PEM}` (a credential; its name ends in `KEYS`, so
    the util-authoring gate forces its declaration).
  Both obey the SAME declared-var rule store secrets obey (`utils_lib._child_env`): a value reaches
  a util iff the routine binds the machine AND the util declares the var.
- **The `remote` util** (`library-seed/utils/remote`) — the ONLY thing that opens an SSH
  connection. Host keys are PINNED (a mismatch, or an unscanned machine, refuses to connect).
- **`web/settings/machines.py`** — the Settings CRUD + `scan-host` + `test`. The routine page's
  Machines card binds catalog machines to the routine (`api_routines` PATCH `machines`).

## Setting one up (a GPU box)

1. **Prepare the host.** On the remote machine, create a dedicated unprivileged user (e.g.
   `rsched`) and add an SSH public key to its `~/.ssh/authorized_keys`. Prefer a locked-down entry
   (`command=…`, `restrict`) if the routine only needs specific operations.
2. **Settings → Secrets**: set the PRIVATE key as a secret, e.g. `GPUBOX_SSH_KEY` (paste the whole
   unencrypted PEM).
3. **Settings → Machines → add**: name (`gpu-box`), host, user, port, `key_var` = `GPUBOX_SSH_KEY`,
   an optional **`share`** (a remote dir to mount, e.g. `/srv/shared`), an optional workdir, a
   description (shown to the model), tags. Click **scan host key** to read and pin the server's host
   key, review it, then save. Click **test** to confirm reachability.
4. **Bind it**: on a routine's page, *Machines* → check `gpu-box` → save. Also switch on the
   **`remote-machines`** permission (which enables the `remote` util).

## Using it (in a routine)

The model sees its bound machines in the prompt's CAPABILITIES section and acts through the
`remote` util:

```
remote list                                     # the machines this routine can reach
remote exec gpu-box --command "nvidia-smi"      # run and WAIT (short commands only)
remote submit gpu-box --command "python train.py"   # DETACHED job → a job id
remote status gpu-box --job <id>                # running | exit=<code> | nojob
remote logs   gpu-box --job <id>                # stdout + stderr so far
remote cancel gpu-box --job <id>                # terminate the job's process group
remote push gpu-box --src ./data.tar --dest /srv/data.tar   # SFTP upload
remote pull gpu-box --src /srv/out.ckpt --dest ./out.ckpt   # SFTP download
```

**Long jobs.** `exec` has a timeout and loses output if the connection drops — it is for short
commands. A multi-minute-to-hours GPU job belongs in `submit`: it starts a detached process (its
own session/process group, survivable, killable via `cancel`) and returns a job id. Then either
poll `status`/`logs`, or pass `submit … --notify-webhook <the routine's own trigger URL>` so the
remote job POSTs the routine on completion — the routine fires to collect the result, and no run
sits polling for hours. (See [triggers](triggers.md) for the routine's webhook URL.)

## Filesystem: mounting a share

Set a machine's **`share`** (a remote directory) and, whenever a routine binds that machine, the
engine mounts it over sshfs at **`<routine>/mnt/<machine>/`** for the run's lifetime. Then
**ordinary filesystem utils (and `read_file`/`write_file`/`edit_file`) work on remote files with
no transfer step** — `push`/`pull` become unnecessary for anything under the share.

This is the clean division: **compute crosses via `remote exec`, the filesystem via the mount.**
The mount virtualizes *files*, not *compute* — a util reading a mounted file runs on the daemon,
not the GPU, so the actual GPU work still goes through `remote exec`; the mount just makes staging
inputs and collecting outputs seamless around it.

How it works, and why it is safe:

- The **engine** (not a sandboxed util) does the mount, like OAuth consent — so the private key
  never enters a util. The key + a pinned-host-key `known_hosts` are written to a daemon-private
  dir the util sandbox keeps invisible, and removed on unmount.
- The routine dir is already a sandbox **write root**, and a Landlock rule on it covers the sshfs
  sub-mount (verified) — so a util reads/writes the mounted files under the exact same jail as any
  local file, with no extra grant. Reaching the *filesystem* of a machine needs only the binding +
  a `share`; it does not need the `remote-machines` permission (that gates the compute util).
- `mnt/` is gitignored, so the engine's autocommit never pulls the remote filesystem into the
  routine's repo.
- Mounting is **best-effort**: an unreachable host, a missing key, or no `sshfs` on the host logs a
  warning and the run proceeds without the mount. It is unmounted on every exit path; a crash
  leaves a stale mount that the next run clears before remounting.

**Deployment note (Docker):** FUSE inside the container needs the fuse device + `CAP_SYS_ADMIN` +
AppArmor unconfined, and the `sshfs` package. The shipped `Dockerfile` installs `sshfs` and
`docker-compose.yml` grants the three — inert unless a bound machine sets a `share`. On a bare-host
install, just `apt install sshfs` (or your distro's equivalent).

**Performance:** sshfs is a network round-trip per I/O — ideal for stage → compute → collect, but
for heavy random I/O (or mmap / file locking) `pull` the file, work locally, then `push` it back.

## Security

- **Host keys are pinned.** Each catalog entry stores the server's public host key; the run
  verifies it strictly (paramiko `RejectPolicy` — no trust-on-first-use in a headless run). A
  mismatch (the host's key changed, or a MITM) refuses to connect with a clear message; re-scan in
  Settings if the change was legitimate.
- **Keys come from the Secrets store, not `~/.ssh`.** The util sandbox keeps `~/.ssh` invisible
  exactly as before (see [sandboxing](sandboxing.md)); a machine's key is injected only into the
  `remote` util, only for a bound machine. `SSH_AUTH_SOCK` / `SSH_AGENT_PID` are scrubbed from
  every util, so a forwarded agent can never route around the binding.
- **The remote host is NOT sandboxed.** A command there runs with the login account's full
  privileges. Use a dedicated, least-privilege remote user; the sandbox protects the daemon host,
  not the target.
- Private keys never enter the prompt, transcripts, or the search index (the config dir and tool
  observations are excluded); only the non-secret catalog metadata is ever shown.

## Limits (today)

- **One key per machine** (the `key_var` secret). Key rotation is a Secrets edit.
- **Unencrypted keys only** — a headless run has no passphrase channel.
- Host-key scanning offers ed25519 / ecdsa / rsa; paste an `ssh-keyscan` line by hand for anything
  else.
- No connection multiplexing — each `remote` call opens and closes its own SSH connection.
