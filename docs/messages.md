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

A row a later report has TAKEN OVER (`supersedes` — docs/items.md) carries its `superseded`
stamp into whichever folder it is in, so a hand-off whose thread moved elsewhere says where to
read it instead of sitting there looking unanswered. It does not LEAVE the outbox: the fold
changes which thread answers for the problem, not whether the message this row already put in
the recipient's inbox is still waiting to be drained.

`answer-*` files (question answers) stay off this surface on purpose: they belong to the
Decisions page's record, and rendering them as messages would fork that vocabulary. (They do
wake a routine's report trigger like any inbox work — docs/triggers.md.)

## One writer, one stem

Every `msg-*.json` file is written by `engine/inbox.file_message` and by nothing else. The
channels differ only in what they pass it: a `via` (which decides WHEN the message is
consumed — see below), the extra keys that channel adds (`attachments` + `command` for a
conversation message, `report`/`from`/`closes` for a delivered report, the audit editor's
structured feedback fields), and — for the channels whose filename is a KEY rather than a
timestamp — a deterministic `name`.

That last one is why the rule needed restating: a report delivery is `msg-rep-<id>` so the
sender can see whether its own delivery is still queued and retract it, and a background
result is `msg-bg-<task>` so a re-delivery replaces the pending message instead of queuing a
second. Seven modules used to write the filename themselves to get that, each with its own
`ts` spelling and its own uniqueness rule, and one of them was a character-for-character copy
of the writer's own line.

Every scanner selects `msg-*.json` — the stem the one writer produces — never "every file
that is not `answer-*`". The old filter also matched `paths.atomic_write`'s in-flight
`.msg-….json.XXXX.tmp`, and each reader lost something different to it. The drain reached
that temp file on a fresh boot, could not parse it, logged "not a message file" and renamed
it into `consumed/`, so the writer's `replace()` raised and the message was lost. The report
trigger's watch (`daemon/triggers`) is documented FAIL-OPEN — anything unreadable WAKES —
so a race with any inbox write bought a whole run of the recipe. And the run gate
(`daemon/run_gate.pending_inbox`) counted ANY file, so a queued question ANSWER — the one
thing that must never start a run — read as freight the gate had to admit a run for.

## What a live leg is TOLD versus HANDED

A FRESH run's boot drains the whole inbox. A leg that is not a fresh boot — a mid-run turn
boundary, or a continuation leg on a finished run — drains only `LIVE_MESSAGE_VIAS`: the user
talking to THIS run, plus a detached background task's result. Everything else (an audit
decision answer, a sibling's report delivery, a routine-page queued message) is addressed to
the routine's NEXT FRESH run, and a follow-up leg draining it wholesale silently ate answers
meant for that night's run (D92/D93).

That exclusion stays. What such a leg now gets is a NAME for it: `inbox.queued_freight`
lists the waiting files — via, timestamp, report id, first line — and the composer renders
them as one digest section, consuming nothing. The run is told to go and read the file, never
handed its contents.

The silence was the defect. On 2026-09-21 a decision was answered at 16:01 through the
Decisions page, the run finished at 16:02, and three continuation legs later the operator
asked what that decision meant. The run explained it as still OPEN, with a recommendation —
because nothing in its context said an answer was sitting in `inbox/` that it was not allowed
to consume. "did you lose my answer again?!"

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

A routine's inbox is also not readable through the API by a RUN: the read-only routine token
is refused on `/api/fs`, `/api/settings` and `/api/debug` (R94 — "read-only" is not "may read
anything"; a util subprocess is handed that token inside a Landlock jail). A run reads what
it may read with `read_file`.
