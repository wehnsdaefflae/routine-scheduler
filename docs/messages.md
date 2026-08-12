# Messages — the four folders of a routine

Every text in this system is for or from an individual routine (or both). D74 (operator
order 2026-08-05) gave that one surface: each routine's page carries four message folders,
and the maintenance index (formerly the Items page) is the **Messages** page (`#/messages`,
see docs/items.md — the item model, ids and `GET /api/items` keep the item vocabulary).

The folders are a READ MODEL over the EXISTING stores (`rsched/readmodels/messages.py` —
no new store, no new writer):

| folder | source | meaning | writes |
| --- | --- | --- | --- |
| **inbox** | `<routine>/inbox/msg-*.json` | waiting for this routine's next run | create · edit · delete |
| **outbox** | ledger rows with `target`, no `delivered` | hand-offs the recipient has not consumed | retract only |
| **read** | `runs/<ts>/consumed/msg-*.json`, newest first (capped) | consumed by this routine | none |
| **received** | ledger rows with a `delivered` stamp | hand-offs the recipient consumed | none |

`answer-*` files (question answers) stay off this surface on purpose: they belong to the
Decisions page's record, and rendering them as messages would fork that vocabulary.

## The write surface (the D74 decision record)

**Inbox — full write access.** The inbox is the user's queue: what a routine's next run
gets told is the user's call right up until a run drains it. That covers every `msg-*`
file — user-filed, engine-filed (trigger events, schedule-once notices, background
results) and delivered reports alike, because the inbox file IS the delivery vehicle.

- `POST /api/routines/{slug}/messages` `{text}` — queue a message for the next run (the
  routine-bound home of the old "note for the next run", F233). Returns the message id and
  a `delivery` hint (`mid-run` when a live run will drain it at its next turn boundary).
- `PUT /api/routines/{slug}/messages/{msg_id}` `{text}` — rewrite the text in place: the
  SAME file, so the queue position holds; the original `ts` is kept, `edited` stamped.
  Engine keys (`report`, `from` — delivery stamping matches on them) survive; structured
  reviewer-feedback fields (`kind`/`target`/`choice`/`raw`, api_audit's) are dropped —
  they describe the text that was replaced.
- `DELETE /api/routines/{slug}/messages/{msg_id}` — withdraw; the run never sees it.
  Gone from the inbox = consumed = immutable: both mutations answer 404 then.

**Outbox — retraction, nothing else.** Outbox rows are derived from the append-only report
ledger (`.control/reports.jsonl`, docs/items.md), and a report is the RUN's utterance: the
user neither authors one (user→routine text is an inbox message on the target) nor rewrites
one (that would put words in the run's mouth and fork the ledger's record). The one write:

- `DELETE /api/routines/{slug}/outbox/{report_id}` — retract a not-yet-consumed addressed
  report (`reports.retract_report`). The pending `msg-rep-*.json` is unlinked from the
  target's inbox (the recipient never sees it) and a `retracted` event row is appended
  under the ledger lock; the report row itself is never touched. A correction is a NEW
  message the user writes into the target's inbox, in their own voice.

Retraction refuses a consumed delivery (a `delivered` stamp, or the file already drained),
an unaddressed report (nothing is pending anywhere), and a second retraction. A target
whose inbox no longer exists can never consume the message, so its absence does not block
the retraction there.

**Read and received are history.** No write endpoint exists for either — a consumed
message belongs to the transcript record.

## Lifecycle effects of a retraction

- The row leaves the **outbox** (it is neither waiting nor consumed; it never enters
  `received`).
- On the Messages page the item reads **`dropped`** — retraction outranks every other
  report status (docs/items.md § Reports).
- A retracted reply (`answers: "R<n>"`) settles NOTHING: the answer never arrived, so its
  target reverts to its own delivery state.
- The target's `report` trigger cannot fire on it any more (the inbox file is gone), and
  `read_reports` folds the event as `retracted: {ts}` for every consumer.

## The page surfaces

- **Routine page → Messages** (`static/views/routine-messages.js`): the four folders as
  tabs with counts, the inbox composer ("queue for the next run"), per-message edit /
  withdraw, outbox retract (confirm dialog), run links on read/received rows. Live-refreshed
  on the routine's own run lifecycle events (a run drains the inbox at boot).
- **Messages page** (`static/views/messages.js`, docs/items.md): the maintenance index,
  plus self-audit's whole inbox queue ("waiting for the next run" — every queued message,
  editable and withdrawable until a run consumes it) and the note composer. The note is a
  PLAIN user message through the generic POST above (D74 phase 4) — no `[AUDIT note]` tag;
  only the structured feedback kinds (finding comments, decision answers) keep the tagged
  `api_audit` channel, because their text is re-formatted from fields.

## API summary

```
GET    /api/routines/{slug}/messages              the four folders (rsched/web/api_messages.py)
POST   /api/routines/{slug}/messages              create an inbox message
PUT    /api/routines/{slug}/messages/{msg_id}     edit a queued inbox message
DELETE /api/routines/{slug}/messages/{msg_id}     withdraw a queued inbox message
DELETE /api/routines/{slug}/outbox/{report_id}    retract an undelivered addressed report
```

All writes take the operator's primary token (mutating routes are primary-only by default,
R94). Message ids are the inbox filename stem (`msg-…`); the id pattern keeps `answer-*`
files and path tricks out of reach.
