# Items — the system-maintenance index

An **item** is one unit of maintenance work on the scheduler itself: a self-audit
**finding** (`F<n>`), a self-audit **decision** (`D<n>`), or a **bug report** (`R<n>`) filed
by any run through the ungated `report_bug` action. The Items page (`#/items`) is the one
place where every item is listed with its status, what it is for, where it came from, and
when it was addressed. It replaced the old Log and Audit pages in 0.106.0.

Items are a READ MODEL (`rsched/readmodels/items.py`): four files on disk are merged into
one shape on demand. Nothing in this path writes an item — the self-audit routine owns
`report.json`, the engine owns `bug-reports.jsonl`, the web layer owns the answered-decision
markers, and the changelog is written by self-audit's own runs.

## Sources

| file | owner | contributes |
| --- | --- | --- |
| `<self-audit>/audit/report.json` | the self-audit routine | findings + decisions, and their CURRENT status |
| `<self-audit>/audit/changelog.jsonl` | the self-audit routine | the archive: which commit addressed which item, when |
| `<self-audit>/audit/decisions-answered.json` | the web layer (`api_audit`) | durable "the user answered this decision" markers |
| `<routines>/.control/bug-reports.jsonl` | the engine (`rsched/bug_reports.py`) | bug reports from every routine |

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

- **`id`** — `F<n>` finding, `D<n>` decision, `R<n>` bug report. The prefix is the type, and
  the three namespaces never collide. `R` was chosen over `B` because the user's own
  reviewer-backlog items are written `B<n>` in prose and would mislink.
- **`type`** — `finding` | `decision` | `bug`.
- **`status`** — see below. The key is `status`, never `state`: decisions already carried
  `status` on disk and a synonym would fork the vocabulary.
- **`title`**, **`detail`** — the item's own prose, verbatim from its source. An archive-only
  item has neither (nothing on disk holds them); the UI shows its newest `addressed` entry
  instead, labelled as coming from the changelog.
- **`origin`** — where the item entered the system: `routine`, `run_id`, `ts`, `commit`.
  For findings and decisions that is the report that raised them; for bug reports the run
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
- Type extras: **`severity`** (findings), **`options[]`** + **`resolution`** (decisions).

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
pending_feedback[]   reviewer feedback queued in the routine's inbox (editable/withdrawable)
answered_decisions[] decision ids answered at-or-after the report's `generated`
exists               false when the self-audit routine is not set up yet
```

The report's `findings`/`decisions` arrays are NOT echoed — they ARE the items. The whole
changelog rides along because an item's own history sits on its card, and a row naming no
item would otherwise be unreachable.

Filters (all optional, combinable): `type`, `status`, `routine` (matches `origin.routine`),
`search` (substring over id, title, detail, and the addressed summaries), `limit`.

The reviewer-feedback composer keeps its own channel: `POST`/`PUT`/`DELETE
/api/audit/feedback` (`rsched/web/api_audit.py`) write, edit and withdraw tagged messages in
the self-audit routine's inbox, where the next run drains them. Items is a read surface; the
feedback endpoints are the only write path on the page, and answering a *decision* still
happens on the Decisions page, through the same inbox.

## Bug-report ids

`report_bug` stamps a monotonic `R<n>` on every report as it is appended
(`rsched/bug_reports.py`), under the same advisory file lock the append takes, so two runs
filing at once cannot collide. The id comes back in the action's observation, so the filing
run can name it in its own summary. Every row in `bug-reports.jsonl` carries an `id` — the
existing rows were stamped in place once, in `ts` order, when the field was introduced; there
is no id-less form to handle.
