# Items — the maintenance-item model behind the Messages page

D74 (operator order 2026-08-05) landed in full: every text is for or from an individual
routine, each routine page carries the four message folders (docs/messages.md — the folder
model, the write surface and the retraction decision live there), and the page this model
feeds is named **Messages** (`#/messages`). The ITEM vocabulary below — ids, statuses,
`GET /api/items` — is unchanged by the rename: an item is not a message, it is the
maintenance record a message may carry or answer.

An **item** is one unit of maintenance work on the scheduler itself: a self-audit
**finding** (`F<n>`), a self-audit **decision** (`D<n>`), or a **report** (`R<n>`) filed by
any run through the ungated `report` action — addressed to the routine that owns the problem,
or left unaddressed for triage. The Messages page (`#/messages`) is the one
place where every item is listed with its status, what it is for, where it came from, and
when it was addressed. It replaced the old Log and Audit pages in 0.106.0 (as Items, renamed
in D74).

Items are a READ MODEL (`rsched/readmodels/items.py`): four files on disk are merged into
one shape on demand. Nothing in this path writes an item — the self-audit routine owns
`report.json`, the engine owns `reports.jsonl`, the web layer owns the answered-decision
markers, and the changelog is written by self-audit's own runs.

## Sources

| file | owner | contributes |
| --- | --- | --- |
| `<self-audit>/audit/report.json` | the self-audit routine | findings + decisions, and their CURRENT status |
| `<self-audit>/audit/changelog.jsonl` | the self-audit routine | the archive: which commit addressed which item, when |
| `<self-audit>/audit/decisions-answered.json` | the web layer (`api_audit`) | durable "the user answered this decision" markers |
| `<routines>/.control/reports.jsonl` | the engine (`rsched/reports.py`) | every report a run filed, and how far an addressed one got |

`report.json` carries only the CURRENT window (schema 1: `run_id`, `generated`,
`since{commit,window}`, `summary`, `findings[]`, `decisions[]`) — a report lists the items
that run considered, not every item that ever existed. The changelog and the answered
markers are what keep older items visible; an item known only from those is *archive-only*
and says so.

`findings.json` / `decisions.json` sit in the same directory and are NOT item stores despite
their names — they are per-run scratch from the routine's first runs (keyed by run id, with
arbitrary payload keys). Nothing reads them.

## The item shape

```json
{
  "id": "F202",
  "type": "finding",
  "status": "addressed",
  "title": "Run messages could not carry file attachments",
  "detail": "…the item's own prose…",
  "origin":   {"routine": "self-audit", "run_id": "self-audit:20260725-000002",
               "ts": "2026-07-25T00:38:00+00:00", "commit": "5854843551"},
  "addressed": [{"ts": "…", "commit": "d0ab60d…", "run_id": "self-audit:20260725-000002",
                 "summary": "0.103.0 — F202 run messages carry file attachments…",
                 "title": "", "link": "explicit"}],
  "evidence": ["src/rsched/web/api_runs.py"],
  "refs": ["F200"],
  "archive_only": false,
  "severity": "improvement"
}
```

- **`id`** — `F<n>` finding, `D<n>` decision, `R<n>` report. The prefix is the type, and the
  three namespaces never collide. `R` was chosen over `B` because the user's own
  reviewer-backlog items are written `B<n>` in prose and would mislink.
- **`type`** — `finding` | `decision` | `report`.
- **`status`** — see below. The key is `status`, never `state`: decisions already carried
  `status` on disk and a synonym would fork the vocabulary.
- **`title`**, **`detail`** — the item's own prose, verbatim from its source. An archive-only
  item has neither (nothing on disk holds them); the UI shows its newest `addressed` entry
  instead, labelled as coming from the changelog.
- **`origin`** — where the item entered the system: `routine`, `run_id`, `ts`, `commit`.
  For findings and decisions that is the audit report that raised them; for reports the run
  that filed it. An archive-only item takes its origin from the EARLIEST changelog row
  linked to it — that is the first trace of it, not necessarily the moment it was raised.
- **`addressed[]`** — every changelog row linked to this item, newest first: `ts`, `commit`,
  `run_id`, `summary`, `title`, and `link`. This is the "when was it addressed" history.
- **`evidence[]`** — the finding's own evidence list (findings only, else empty).
- **`refs[]`** — other item ids (`F`/`D`/`R`) named in this item's own prose, so the graph is
  navigable. This scan includes `R` — unlike the changelog fallback below, the prose is
  current rather than historical.
- **`archive_only`** — true when no source holds the item's own record any more and it
  survives solely through the changelog / answered markers.
- Type extras: **`severity`** (findings), **`options[]`** + **`resolution`** (decisions),
  **`to`** + **`delivered{ts,run_id}`** + **`answers`** + **`closes`** + **`answered_by`**
  (reports; `to` is empty on an unaddressed one, which has no routing to show; `closes` is
  true on a terminal acknowledgment — see the status rules below).

## Status vocabulary

`open` · `in_progress` · `addressed` · `settled` · `dropped` · `unknown`

`unknown` is not a state an item is *put* into — it is the absence of a recorded status.
The self-audit routine emits a `status` on every finding it writes, from this vocabulary
(its `write-report` stage carries the table). A finding still reading `unknown` is one written
before that landed, and that is the correct rendering for it — `report.json` is rewritten whole
every run, so the statuses converge without a backfill. Status is NEVER recovered by parsing the
title or detail prose.

Precedence, in order — the first rule that applies wins:

1. **The report's own `status` field**, when the item has a report entry carrying one and
   the value is in the vocabulary. `report.json` is always the authority on current status;
   the changelog is an archive and can never override it.
2. **A durable answered marker** (`decisions-answered.json`) on a decision with no report
   status → `settled`. The user answering a decision is a recorded fact, not an inference.
   When the report DOES still say `open`, rule 1 wins and the status stays `open` — the
   marker rides along in `answered_decisions` and the card overlays an "answered" badge,
   the same overlay the Decisions page uses. A newer report re-raising the decision is the
   authority on it being open again.
3. **Archive-only with at least one linked changelog row** → `addressed`. The item has
   dropped out of the current report and a recorded code change names it.
4. Otherwise → `unknown`.

A status value outside the vocabulary is a data error and reads as `unknown`; the read model
does not translate synonyms.

### Reports

An `R<n>` derives its status from its OWN ledger, which is the authority for it the way
`report.json` is for an `F<n>`. In precedence order: **`dropped`** when the user RETRACTED
it before the target consumed it (`reports.retract_report`, docs/messages.md — the
recipient never saw it, so no other state can apply, and a retracted reply settles
nothing); **`settled`** when the row itself carries
`closes: true` (see below) or when a later report carries `answers: "<this id>"` — the target
replied, having acted or having said why not; **`addressed`** when a changelog row names the
id; **`in_progress`** once an ADDRESSED report's target drained the message from its inbox and
the engine stamped a `delivered` event onto the row; otherwise **`open`**.

That progression is the whole reason the ledger exists: it distinguishes a hand-off that
carried from one that silently never arrived. An addressed report's card shows the routing line
(`sender → target`, whether it was picked up, and which reply closed it); an unaddressed one
has no routing and simply waits in triage. Delivery itself never starts a run; a target that
wants to be WOKEN by deliveries declares a `report` trigger on its own Triggers card
(docs/triggers.md) — bursts coalesce into one run per cooldown window.

**The terminal acknowledgment.** A reply row may set `closes: true` beside `answers` (the
action layer rejects a bare `closes`): it settles its target as any answer does AND is itself
born settled — it asks nothing back, so the exchange ENDS there instead of ratcheting (every
answer otherwise being a new open report waiting for one more reply). A closure is still
delivered when addressed, with the message marked "no reply needed"; answering a closure
anyway is harmless — it is already settled — and only a NEW report that names the closure
reopens the discussion, as its own open item.

**Deferrals whose carrier closed without delivering them.** An item routinely defers part of its
scope into another ("the sidebar panel ships with F324's shared component"). The carrier then
ships its OWN scope and closes, the changelog `items` join records the carrier as addressed, and
nothing checks that what was deferred INTO it actually shipped — the deferral existed only as
prose. The deferred piece becomes an open item nowhere and is simply gone. That is not
hypothetical: D98's stopping-conditions panel was deferred into F324 on 2026-08-21, F324 closed
`addressed` on 2026-08-26 naming R339/R340/R341/F336 as delivered, and the panel was never built.
`readmodels/orphans.py` reads the ledger for deferral phrasings naming a carrier and flags any
whose carrier is CLOSED and whose closure evidence — the carrier's own `detail` plus the
changelog rows whose `items` name it — never mentions the deferring ids. (Prose mentions are
deliberately not evidence: the deferring sentence names both the source and the carrier, so
counting them would make every deferral its own proof of delivery.)

**An addressed report whose message was never written.** The same module carries the second loss
mechanism, because it is lost the same way — off every filter, counted in every backlog figure,
and owned by nobody. `file_report` writes the ledger row and the target's `inbox/msg-rep-<id>.json`
in ONE call, so an addressed report always has a message waiting for its target's next run. A row
appended any other way — an operator batch written straight to the stream — has a `target` and no
message, so the target can never drain it, never stamp it `delivered`, and it reads `open`
forever. Twelve rows from the 2026-08-29 web-UI migration are exactly that (D114). The check is
the FILE, not the stamp: a stamp says a run has read the message, while the file's absence says no
run ever can. Retracted rows are excluded — retraction unlinks the message on purpose — and a
message still sitting in the inbox is the normal waiting state, not this.

`GET /api/items/orphans` serves both, tagged `kind: "deferral"` / `kind: "undelivered"`, and the
Messages page banners them above the list in two groups, because they are invisible to every
filter below it. It SURFACES rather than gates — a human judges the promise, and the fix for a
false-positive deferral is to write the closure note so it names what it delivered.

The stream is append-only. The report row is written by the `report` action; the `delivered`
and `retracted` events are further rows, folded onto it by `reports.read_reports`.

A closure (`closes: true`) is delivered like any other addressed report but never WAKES its
target: the receiving routine's `report` trigger skips a closure-only inbox, so an
acknowledgment costs the recipient nothing and is read by the next run it holds anyway
(docs/triggers.md § Firing semantics).

## Joining the changelog

Each changelog row carries an explicit `items: ["F202", "R7"]` field naming the items it
touched — that is the only join the read model trusts (`link: "explicit"`), and self-audit's
`act-apply-fixes` stage requires it on every row it appends (`[]` when a commit genuinely
addresses no item; never omitted). Rows written before that field existed are matched by
scanning their `title`/`summary`/`detail` prose for
`F<n>`/`D<n>` tokens, and those links are flagged `link: "best-effort"` in the API and shown
as best-effort in the UI. The prose fallback never matches `R<n>`: bug ids postdate every
historical row, so any `R` in old prose is a false positive.

The `items:` field is written by the self-audit routine, which is the changelog's author.
The read model consumes it and never authors it.

The changelog file mixes pretty-printed and compact JSON objects, so it is parsed with a
streaming `json.JSONDecoder().raw_decode` loop rather than line by line. A line-oriented
parser silently drops every multi-line row.

## The API

`GET /api/items` returns the merged index plus the header the page needs:

```
items[]              the matching items, newest origin first (`total` = matches before `limit`)
counts{type,status}  totals across the UNFILTERED set, for the filter chips
report{…}            the current report's meta: run_id, generated, since{commit,window}, summary
changelog[]          the 60 newest changelog rows, including ones that name no item
last_run{…}          the self-audit routine's most recent run
queued[]             EVERY message waiting in the routine's inbox (editable/withdrawable);
                     tagged reviewer feedback carries its structured fields (kind/target/…)
answered_decisions[] decision ids answered at-or-after the report's `generated`
exists               false when the self-audit routine is not set up yet
```

The report's `findings`/`decisions` arrays are NOT echoed — they ARE the items. The whole
changelog rides along because an item's own history sits on its card, and a row naming no
item would otherwise be unreachable.

Filters (all optional, combinable): `type`, `status`, `routine` (matches `origin.routine`),
`search` (substring over id, title, detail, and the addressed summaries), `limit`.

The STRUCTURED reviewer feedback (finding comments, decision answers) keeps its tagged
channel: `POST`/`PUT /api/audit/feedback` (`rsched/web/api_audit.py`) write and edit tagged
messages in the self-audit routine's inbox, where the next run drains them — their text is
re-formatted from fields, which is why they cannot ride the generic endpoint. Everything
else — the free note for the next run, and withdrawing ANY queued message — goes through
the generic per-routine message endpoints (docs/messages.md, D74). Answering a *decision*
still happens on the Decisions page, through the same inbox.

## Report ids

The `report` action stamps a monotonic `R<n>` on every report as it is appended
(`rsched/reports.py`), under the same advisory file lock the append takes, so two runs filing
at once cannot collide. The id comes back in the action's observation, so the filing run can
name it in its own summary, and it is how a later report CLOSES this one (`answers`). Every
row in `reports.jsonl` carries an `id`; there is no id-less form to handle.
