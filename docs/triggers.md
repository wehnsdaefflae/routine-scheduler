# Event triggers

Routines fire on cron, manually — or on an **external event**. A trigger is a routine.yaml
config entry that lets the outside world start a run: today that means an authenticated
**webhook** URL a third party POSTs to (CI finished, a form was submitted, a monitor
alerted); `imap` (mail arrival) and `watch_path` (file drop) are reserved trigger types in
the same config shape, to be implemented as daemon-side watchers later. Event triggers are
what let a radar-style routine stop polling on a schedule and burn tokens only when
something actually happened.

## Config shape (`triggers:` in routine.yaml)

One canonical list — every trigger type uses the same envelope (`id`, `type`, `cooldown_s`)
plus its own keys. This is user config like everything else in routine.yaml: created and
deleted on the routine page (or by editing the file), never writable by a run.

```yaml
triggers:
  - id: t-4f9a01bc          # server-generated, the stable handle (delete, fire ledger)
    type: webhook
    token: "kJ8…32-url-safe-chars…Qw"   # server-generated URL secret — the hook's auth
    cooldown_s: 60           # min seconds between trigger-initiated fires (coalescing window)
    created: "2026-07-17T09:00:00+02:00"
  # reserved types — same envelope, own keys; nothing fires them yet:
  # - {id: t-…, type: imap, host: imap.example.org, mailbox: INBOX, cooldown_s: 300}
  # - {id: t-…, type: watch_path, path: ~/drop/incoming, glob: "*.csv", cooldown_s: 60}
```

Validation lives in `rsched/triggers.py` (`validate_triggers`, called from
`config.load_routine`): malformed entries are reported as routine problems and dropped
(fail closed — a webhook without a token can never be matched); reserved-type entries are
kept verbatim and flagged as inert.

## The webhook

Create one on the routine page (Triggers card → *add webhook trigger*). The server
generates the id and the URL token; the card shows the full hook URL with a copy button.

```
POST /api/hooks/<slug>/<token>
```

```bash
curl -X POST "https://sched.example.org:8321/api/hooks/arxiv-radar/kJ8…Qw" \
     -H 'content-type: application/json' \
     -d '{"event": "new-feed-items", "count": 3}'
→ 202 {"ok": true}
```

Any body (or none) is accepted up to 64 KiB and reaches the run **verbatim as an injected
user message**, one message per event, headed by a provenance line
(`[webhook event] trigger t-… received <ts> (content-type)`). The response never echoes
the payload.

### Security posture

- **The URL token is the only auth.** The hook route is the one API route outside the
  global bearer (third parties can't hold your console token). Tokens are server-generated
  (24 bytes url-safe), compared constant-time, and never client-supplied. Treat the URL as
  a secret; rotating it = delete the trigger, create a new one.
- **No existence oracle.** Unknown slug, wrong token, and disabled routine all return the
  same generic `404 unknown hook` (with an equalized token comparison on the unknown-slug
  path). Rejections are logged with slug + client address, never the payload.
- **Size cap** — bodies over 64 KiB are rejected `413` before anything is stored.
- **Rate limit + spool cap** — per routine, accepted events are limited per minute (`429`)
  and at most 32 events may wait unprocessed (`429`). Combined with the cooldown below, a
  leaked URL can at most cause one run per cooldown window — it cannot burn budget or fill
  the disk.
- **Tokens never leave the instance.** The library sync's instance export redacts
  `token` values in routine.yaml exactly like the server config's secrets.

## Firing semantics: coalescing and cooldown

The web handler only **records** the event durably (an `evt-*.json` file under
`<routines_home>/.control/triggers/<slug>/`, the same request-file idiom the restart
sentinel and detached-task `.requests/` use). **Firing is the daemon's job**: the
scheduler tick (≤ ~5 s) hands spooled events to the `TriggerManager`
(`daemon/triggers.py`), which fires through the same runner as cron — one run per routine,
`max_concurrent_runs`, and the restart drain all apply unchanged.

These are the trigger analog of the schedule's catchup/overrun rules:

- **Overrun → queue, not skip.** A cron fire that finds its routine running is skipped
  (`overrun_skipped`); a trigger event that finds it running (or queued, or the daemon
  draining) **waits in the spool**. N events while busy → **ONE fire** when the routine is
  free, and every coalesced event still lands as its own inbox message for that fire — no
  payload is lost, no run per event.
- **Cooldown.** `cooldown_s` (default 60) is the minimum gap between trigger-initiated
  fires; events inside the window coalesce into the next fire. When pending events span
  triggers with different cooldowns, the largest applies.
- **Durability.** An accepted event survives restarts: it is either in the spool (fired at
  a later tick) or already injected into the routine's inbox (drained by the routine's
  next run, whoever starts it). Injection uses deterministic filenames, so a crash between
  steps can't duplicate or lose a message.
- **Dropped events.** Events for a routine that was deleted/disabled, or whose trigger was
  deleted after arrival, are dropped with a log line — the hook itself already rejects new
  ones in those states.
- **Interplay with cron.** A trigger fire is an ordinary run (`reason: "trigger"` in the
  log/SSE); the schedule keeps its own rhythm. Boot catchup (`run_once`) considers cron
  fires only.

The Triggers card shows the per-trigger fire ledger (last fired, delivered events, pending
count), read from the daemon-maintained `state.json` next to the spool.

## What the run sees

Each event is one injected user message, drained at the first turn boundary (i.e.
immediately at kickoff for a fresh trigger-fired run):

```
[webhook event] trigger t-4f9a01bc received 2026-07-17T09:12:31+02:00 (application/json):

{"event": "new-feed-items", "count": 3}
```

Recipes that expect webhook input should say so in a stage ("runs may start from a
webhook event message — parse its payload before polling anything").

## Later trigger types

`imap` and `watch_path` slot in WITHOUT reshaping config: each becomes a daemon-side
watcher that drops the same `evt-*.json` spool files a webhook does (payload = the mail /
the dropped file's path), and everything downstream — coalescing, cooldown, inbox
injection, the fire ledger, the UI card — is already type-agnostic. Their entries carry
their own keys in the same `triggers:` list; until a watcher exists, such entries are
accepted but flagged inert on the routine page.
