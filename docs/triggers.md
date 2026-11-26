# Event triggers

Routines fire on cron (their own — or their LANE's, which suppresses the member crons
while it is set: docs/lanes-tags.md, D71), manually — or on an
**external event**. A trigger is a routine.yaml
config entry that lets the outside world start a run: today that means an authenticated
**webhook** URL a third party POSTs to (CI finished, a form was submitted, a monitor
alerted) — the one trigger type there is. Event triggers are
what let a radar-style routine stop polling on a schedule and burn tokens only when
something actually happened.

## Config shape (`triggers:` in routine.yaml)

One canonical list — every trigger type uses the same envelope (`id`, `type`, `cooldown_s`)
plus its own keys. This is user config like everything else in routine.yaml: added, edited and
removed on the routine page and saved by its one accept (or by editing the file), never writable
by a run.

```yaml
triggers:
  - id: t-4f9a01bc          # server-generated, the stable handle (delete, fire ledger)
    type: webhook
    token: "kJ8…32-url-safe-chars…Qw"   # server-generated URL secret — the hook's auth
    cooldown_s: 60           # min seconds between trigger-initiated fires (coalescing window)
    created: "2026-07-17T09:00:00+02:00"
```

Validation lives in `rsched/triggers.py` (`validate_triggers`, called from
`config.load_routine`): malformed entries are reported as routine problems and dropped
(fail closed — a webhook without a token can never be matched), and so is an entry whose
`type` is not in `TRIGGER_TYPES` — a type nothing can fire is not config to preserve.

## The webhook

Create one on the routine page (*Schedule & gate* → *more* → *Triggers* → *+ add webhook
trigger*, then *accept changes*). The server generates the id and the URL token when the change is
accepted; the row then shows the full hook URL with a copy button. A trigger keeps its id and its
token for as long as its cooldown stays the same. Changing a webhook's cooldown replaces it with a
new one under a NEW URL — the row says so before you accept.

```
POST /api/webhooks/<slug>/<token>
```

```bash
curl -X POST "https://sched.example.org:8321/api/webhooks/arxiv-radar/kJ8…Qw" \
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
- **No existence oracle.** Unknown slug, wrong token, and a routine that may not fire
  (switched off, or retired — its finish line reached) all return the same generic
  `404 unknown hook`, with an equalized token comparison on the unknown-slug path and the same
  whole-catalog lookup for every slug. Rejections are logged with slug + client address, never
  the payload.
- **Size cap** — bodies over 64 KiB are rejected `413` before anything is stored.
- **Rate limit + spool cap** — per routine, accepted events are limited per minute (`429`)
  and at most 32 events may wait unprocessed (`429`). Combined with the cooldown below, a
  leaked URL can at most cause one run per cooldown window — it cannot burn budget or fill
  the disk.
- **Tokens never leave the instance.** The library-sync routine's export redacts every
  webhook `token` in a routine.yaml and withholds from the mirror any other file that carries
  one — a hook URL pasted into a script or a publish payload (docs/architecture.md, Libraries &
  seeds).

## A report waits for the next run

An addressed report (`report` with `target`) is delivered into the target's `inbox/` and read
on the target's NEXT SCHEDULED RUN: delivery never starts a run (docs/items.md). 0.329.0 once
gave every routine a trigger that fired on inbox freight, and the reversal came the same
morning (0.330.0): a message is cheap and a run is a whole recipe, and routines answer each
other, so A's report woke B, whose reply woke A, each hop a full run. The opt-in form that
remained was removed later, with no routine using it.

## An answer waits for the next run — or for your click

A run that asks a **deferred** question finishes without the answer: by definition it did not
need it to continue, so "tell me when you get to it" means the routine's next scheduled run.
That is the contract, and the schedule is the only clock. A routine that cannot continue
without the answer asks a **blocking** question instead — the run parks and resumes the moment
the answer lands, which is the explicit mechanism for urgency.

0.330.0 made every answer fire a run at once ("the answer wake"), and the operator's verdict
was exact: three routines started in the same second when he cleared his inbox, and answering
a question had become a way of starting runs out of schedule. Reversed in 0.333.0. What
replaced it is a deliberate control: the Decisions page's **answer & run now** files the answer
AND fires ONE manual run of the routine (`POST /api/questions/<qid>/answer` with
`run_now: true` — the same `manual` fire as the routine page's Run now). It is offered only for
a deferred routine question whose run has ended; a blocking question resumes its own run, a
meta decision is self-audit's to read on its schedule, and a routine with an active run drains
the answer at its next turn boundary, so the button fires nothing there. Nothing starts a run
but the schedule, a trigger the operator configured, or the operator's own click.

## Firing semantics: coalescing and cooldown

The web handler only **records** the event durably (an `evt-*.json` file under
`<routines_home>/.control/triggers/<slug>/`, the same request-file idiom the restart
sentinel and detached-task `.requests/` use). **Firing is the daemon's job**: the
scheduler tick (≤ ~5 s) hands spooled events to the `TriggerManager`
(`daemon/triggers.py`), which fires through the same runner as cron — one run per routine,
`max_concurrent_runs`, and the restart drain all apply unchanged.

These are the trigger analog of the schedule's overrun rule:

- **Overrun → queue, not skip.** A cron fire that finds its routine running is skipped
  (`overrun_skipped`); a trigger event that finds it running (or queued, or the daemon
  draining) **waits in the spool**. N events while busy → **ONE fire** when the routine is
  free, and every coalesced event still lands as its own inbox message for that fire — no
  payload is lost, no run per event.
- **Cooldown.** `cooldown_s` (default 60) is the minimum gap
  between trigger-initiated fires; events inside the window coalesce into the next fire.
  When pending events span triggers with different cooldowns, the largest applies. It is
  set on the Triggers card like any other setting and lands with the page's one accept
  (`POST /api/routines/<slug>/settings`, guarded while a run is active like every multi-field
  config edit); the daemon reads the new window at the next rescan. `id`, `token` and `type`
  are the trigger's IDENTITY, never its configuration, so the accept matches a row to a saved
  trigger by its cooldown: a row whose cooldown changed stands for a NEW trigger, and
  accepting it replaces the old one — its URL with it, which the row says before you
  accept. `0` means *no wait* — fire on every event. The accept refuses (`422`) a type it
  cannot create (`CREATABLE_TRIGGERS`) and a cooldown that is not a whole number `≥ 0`.
- **Durability.** An accepted event survives restarts: it is either in the spool (fired at
  a later tick) or already injected into the routine's inbox (drained by the routine's
  next run, whoever starts it). Injection uses deterministic filenames, so a crash between
  steps can't duplicate or lose a message.
- **Dropped events.** Events for a routine that was deleted, switched off or retired (its
  finish line reached, so nothing later will drain the spool), or whose trigger was deleted after
  arrival, are dropped with a log line — the hook itself already rejects new ones in those
  states.
- **Interplay with cron.** A trigger fire is an ordinary run (`reason: "trigger"` in the
  log/live stream); the schedule keeps its own rhythm. A missed CRON fire is skipped, never made
  up (D237); an event waiting in the spool is not a missed fire and still fires once the daemon
  is back.

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

## Another trigger type

A new type slots in WITHOUT reshaping config: it is a daemon-side watcher that drops the
same `evt-*.json` spool files a webhook does (payload = the mail, the dropped file's path,
whatever it watched), and everything downstream — coalescing, cooldown, inbox injection,
the fire ledger, the UI card — is already type-agnostic. Its entries carry their own keys
in the same `triggers:` list. What it does NOT get is a NAME held in the vocabulary before
it can fire: a type in `TRIGGER_TYPES` with no watcher is a config value that validates,
renders and never does anything, and the house rule against backwards-compatibility
placeholders applies to forwards-compatible ones too. The type and its watcher land
together; until then such an entry is an unknown type, reported and dropped.
