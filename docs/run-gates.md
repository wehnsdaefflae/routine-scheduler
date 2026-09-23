# Optional automatic run admission

`run_gate: {enabled: true, timeout_s: 30}` explicitly opts a routine into a pre-engine
predicate at `scripts/gate.py`. Omission defaults off; timeout is a strict integer 1–300.
Malformed gate settings reject loading, even when disabled. Unrecoverable unrelated
configuration errors reject an enabled gate rather than silently dropping it.

The control is on the routine page, inside the **Schedule** section and saved by that
section's own button — the gate decides whether a scheduled fire becomes a run at all, so it
belongs beside the schedule rather than in a config file. The timeout field appears once the
gate is on. `GET /api/routines/{slug}` returns `run_gate: {enabled, timeout_s}` in the same
payload; `PATCH` accepts partial nested fields and a top-level null is a no-op.

Only fresh `schedule`, `catchup`, and `lane` attempts evaluate the predicate. Direct
Run now, CLI run-once, triggers, one-shots, conversations, background jobs, and resume
bypass it. Run lane now still gates its members. Pending inbox files bypass admission;
inbox arrival during a successful gate evaluation overrides skip without consuming files.

The accepted run ID is reserved first. Gate execution holds the normal concurrency slot,
before any engine process, endpoint/model resolution or decomposition. The script receives
one JSON argv argument with `version: 1`, `routine`, `run_id`, and fire `reason`.
Exit zero and print exactly one UTF-8 JSON object:

```json
{"version":1,"decision":"skip","reason":"No new source records"}
```

Use `decision: "run"` to admit. The reason must be nonempty and at most 1000 characters.
Each output stream is limited to 16 KiB. Malformed output, nonzero exit, missing scripts,
authorization failure, sandbox refusal, overflow, or timeout is a **failed attempt**, never
an implicit skip or run. Subprocess groups are killed on completion, timeout and abort.
The timeout includes interpreter startup, filesystem/secret/sandbox preparation and script
execution. Preparation runs in a dedicated same-source interpreter, with private stdin
configuration and a tracked process group, not in an uncancellable event-loop thread.
Timeout and abort kill that group; no dependency installs occur. Diagnostic stderr is
retained in `gate-stderr.txt`, bounded to 16 KiB, separately from protocol stdout.

Script docstring headers reuse `net:`, `secrets:`, and validated `fs:` declarations. `calls:` is rejected in
this initial scope. Required central secrets need persistent grants; scoped secrets belong
to their routine, and ungranted optional secrets are withheld. No approval loop or model
fallback runs. Dependency-bearing scripts require a preprovisioned `.venv/bin/python`;
missing packages fail normally. Admission requires the strict shared sandbox even if the
server permits degradation for ordinary utilities. `fs: roots` includes configured roots
and domain stores (subject to the shared private-store exclusion); `fs: none` or an absent
header includes only the shared sandbox base (own directory, temporary space, toolchain
and library). `fs: ro /path` and `fs: rw /path` only mount paths already covered by
persistent grants with the requested access; unauthorized paths are not mounted. RO
never upgrades to RW merely because the grant permits writes. Base mounts are not narrowed
by declarations. Malformed/empty/duplicate declarations fail closed; no one-run grants
are inherited. Enable required Landlock kernel/LSM support on refusal, deliberately run
manually to bypass admission, or explicitly disable the opt-in gate; changing server
sandbox mode does not relax gate isolation. Gate code is operator-trusted, not proven
pure: it must not consume inbox entries, advance cursors, or perform the routine's work.

Engine launch retains its process handle through cancellation. An abort during the launch
handshake kills/reaps the acquired process group before publishing `run_started`, preserves
aborted status, and releases ownership once. This is not a child-side launch barrier: the
child may execute instructions before the parent receives its process handle.

A skip writes `state: finished`, `outcome: skipped`, zero usage/turn, `result.md`, and
`gate.json`. It advances a lane even under stop-on-failure. Errors write failed status and
gate metadata; bypasses record their reason. There is no second agent loop and no gate retry.

An error also writes a **`run_failed` health event** (a cancel writes `run_canceled`). It has
to be written here: no engine ever started, so the engine's own event cannot fire and the reap
finds a run that is already terminal. A withdrawn gate secret or a kernel that dropped Landlock
fails every scheduled fire of a gated routine forever, before turn 0, and `run_failed` is the
event a health sweep reads first.
