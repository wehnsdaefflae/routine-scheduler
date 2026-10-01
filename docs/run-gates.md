# Run gates — skipping a scheduled fire that has nothing to do

A run gate answers one question before a scheduled fire becomes a run: **is there work?** It is
the routine's own list of CHECKS — `run_gate: {enabled, timeout_s, checks: [...]}` in
`routine.yaml` — each a known question with parameters (`rsched/gatekit/`), asked without a
model, before any engine process, endpoint or decomposition. A fire the gate skips costs
seconds instead of a run.

**The one rule every check obeys: it may answer "no work" only when it KNOWS there is none.**
Anything it cannot establish — a mailbox that refuses the login, a feed that times out, a state
file that does not parse, no earlier run to compare against — is WORK, with the reason naming
what could not be checked. A wrong "run" costs one ordinary fire; a wrong "skip" silently loses
work, which is the one failure a gate must not have. The fire is skipped only when EVERY check
answered "no work".

## The checks

| kind | work when | key parameters |
|---|---|---|
| `mail` | mail waits in a mailbox — unread, or new since the last ok run | `host`, the login secrets (or `accounts_secret` + `account`), `folders` (a special-use flag such as `\All` names a folder whatever the server calls it), a WATCH LIST (`senders_file`, `from_any`, `subject_any` — a message counts when it meets any of them), `from_domains_not` |
| `hub_feedback` | feedback waits on the routine's Steward hub page that its last publish has not consumed | `project`, `source` |
| `url_changed` | a page, feed or API answers differently than at the last ok run | `url`, `select` (`body`, `feed`, `json:<dotted.path>`) |
| `files_changed` | a file under a folder is new or changed since the last ok run | `paths`, `glob`, `nonempty` (an intake folder the routine empties) |
| `unpaired_files` | a source file still has no output beside it | `path`, `match`, `output` (`{stem}`/`{suffix}`), `exclude` |
| `repo_changed` | a git repository has new commits since the last ok run | `path`, `ref` |
| `runs_since` | other routines have run since the last ok run (reviewers) | `min_runs`, `routines` |
| `state` | a file in the routine's own directory says work is waiting | `file`, `key`, `idle_values`, `missing_field` |
| `dates` | a dated duty is due | `from`/`until` (a window), or `file` + `key` (`*` maps over a list), `within_days`, `done_key`/`done_values` |
| `weekdays` | the first fire on one of these weekdays | `days` (0 = Monday) |
| `max_quiet` | the last ok run is older than this — a backstop every gate should carry | `days` |
| `script` | the routine's own predicate `scripts/admit.py` says so | — |

A check's own limits read the same way. `mail` reads the headers of a folder's newest 500
matching messages, as a mail reader shows them (folded lines unfolded, encoded words decoded),
over verified TLS; a folder with more, none of whose newest 500 counts, is work. `url_changed`
compares at most 8 MiB of an answer; a longer one is work. `files_changed` counts a file whose
inode changed since the last ok run — its mtime OR its ctime, since a file moved or synced in
keeps the mtime it had elsewhere. `dates` and `weekdays` read TODAY on the routine's own clock —
the zone its schedule is in — never the server's, which names a different day for hours around
midnight whenever the two differ.

`gatekit.KINDS` is the vocabulary: the console builds each check's form from it and
`gatekit.validate` checks a `routine.yaml` against it, so a kind cannot be half-added. An
enabled gate needs at least one check — with nothing to ask, either answer would be a lie.
Malformed gate settings reject loading, even when disabled.

**The baseline.** "Since the last ok run" means the newest ADMITTED run that finished ok (a
skipped fire processed nothing and is passed over); the checks that compare contents keep a
FINGERPRINT per check in that run's `gate.json`. No such run is itself work.

## Reasons that run before any check is asked

Built in, never configured, because each one is work by construction:

- freight waits in the inbox (a message, a report addressed to the routine) — the ONE inbox
  predicate (`engine/inbox.has_pending_messages`), fail-open like every gate read: a `msg-*`
  file it cannot parse counts as work;
- an answer to one of its questions waits to be read (an answer never STARTS a run, but a fired
  one must not be skipped past it);
- a note from a routine sharing one of its stores waits (`rsched/sharedstores.py`);
- the last admitted run did not finish ok — it may have left work behind;
- its configuration or recipe changed since the last ok run (`routine.yaml`, `tuning.yaml`,
  `main.md`, `stages/`, `state/finish-line.json`): a granted permission can unblock parked
  work, a revised recipe can add some. A run reads its configuration and recipe at boot, so
  those count from when the last ok run STARTED; it reads the finish line again at its finish
  and stamps it there (a distance per open outcome), so the finish line counts from when that
  run ENDED — measured from the start, the run's own stamp read as a change and no routine
  whose runs keep an accounting could ever be skipped.

## Which fires are gated

Only fresh `schedule`, `catchup` and `lane` attempts. Run now, CLI run-once, triggers,
one-shots, conversations, background jobs and resume bypass it; "run lane now" still gates its
members. Inbox arrival during a gate evaluation that decided "skip" overrides the skip without
consuming anything.

## Where it is set

On the routine page, in the **Run gate** section of the *Schedule & gate* group: the gate's
on/off switch, its timeout, and the list of checks — add one from the menu of kinds, fill its
form, remove it again. The `script` kind opens `scripts/admit.py` beside the list, starting from
a template when the file does not exist yet; the script saves with its own button. Every other
change lands with the page's one accept like any other setting. *Test the gate now* asks the
checks as shown — saved or not — through the real admission path and starts no run
(`POST /api/routines/{slug}/gate/test`). A settings pattern can carry a gate for its kind
of work (`docs/patterns.md`). `GET /api/routines/{slug}` returns `run_gate` whole; `PATCH`
accepts a partial one, validated whole after the merge.

## The custom predicate

`scripts/admit.py` — not `gate.py`: three routines already run a `scripts/gate.py` as an IN-RUN
check runner; ticking the gate over those would have failed every fire. It receives one
JSON argv argument with `version: 1`, `routine`, `run_id` and the fire `reason`, exits zero and
prints exactly one UTF-8 JSON object:

```json
{"version":1,"decision":"skip","reason":"No new source records"}
```

Use `decision: "run"` to admit. The reason must be non-empty and at most 1000 characters. Each
output stream is limited to 16 KiB. Malformed output, a non-zero exit, a missing script,
authorization failure, sandbox refusal, overflow or timeout is a **failed attempt**, never an
implicit skip or run. Gate code is operator-trusted, not proven pure: it must not consume inbox
entries, advance cursors or perform the routine's work.

Its docstring header reuses `net:`, `secrets:` and validated `fs:` declarations; `calls:` is
rejected. Required central secrets need persistent grants; scoped secrets belong to their
routine; ungranted optional secrets are withheld. Dependency-bearing scripts require a
preprovisioned `.venv/bin/python`; missing packages fail normally.

## Execution and isolation

The accepted run id is reserved first. Gate execution holds the normal concurrency slot, before
any engine process. The declarative checks run as `gatekit/run.py` — standard library only —
inside a Landlock jail built from what the listed kinds need and nothing else (network only for
`mail`, `url_changed` and `hub_feedback`; only the secrets their parameters name; the paths they
read). The custom predicate gets its own jail, built from its header. Admission requires the
strict shared sandbox even if the server permits degradation for ordinary utilities: `fs: roots`
includes configured roots and the shared stores among them; `fs: none` or an absent header
includes only the shared sandbox base (own directory, temporary space, toolchain, library);
`fs: ro /path` and `fs: rw /path` mount only paths already covered by persistent grants with
the requested access; RO never upgrades to RW. Malformed, empty or duplicate declarations
fail closed; no one-run grants are inherited.

The timeout (an integer 1–300 s) covers interpreter startup, filesystem, secret and sandbox
preparation and the checks themselves. Preparation runs in a dedicated same-source interpreter
with private stdin configuration and a tracked process group, never in an uncancellable
event-loop thread; timeout and abort kill that group, and the gate ends when its process does —
a descendant that left the group (`setsid`) holding the inherited stdout/stderr no longer keeps
the gate and its slot past the deadline: its pipes are closed on it. Diagnostic stderr is kept in
`gate-stderr.txt`, bounded to 16 KiB, apart from protocol stdout.

Engine launch retains its process handle through cancellation. An abort during the launch
handshake kills and reaps the acquired process group before publishing `run_started`, keeps the
aborted status and releases ownership once.

## What a decision leaves behind

A skip writes `state: finished`, `outcome: skipped`, zero usage and turns, `result.md` and
`gate.json` (the decision, its reason and each check's answer). It advances a lane even under
stop-on-failure. An admitted run's `gate.json` carries the fingerprints the next fire compares
against. Errors write failed status and gate metadata; bypasses record their reason. There is
no second agent loop and no gate retry.

An error also writes a **`run_failed` health event** (a cancel writes `run_canceled`). It has to
be written here: no engine ever started, so the engine's own event cannot fire and the reap
finds a run that is already terminal. A withdrawn gate secret or a kernel that dropped Landlock
fails every scheduled fire of a gated routine before turn 0; `run_failed` is the event a
health sweep reads first.
