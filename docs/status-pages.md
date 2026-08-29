# Status pages — the shared web UI routines publish to

A routine that works on something over weeks needs a surface the user can look at between runs:
what happened, what is waiting, and one control per decision. That surface is a page on
`steward.markwernsdorfer.com`, and since 0.255.0 every routine that publishes one publishes into
the **same** shell.

This is not engine code. The engine neither serves these pages nor knows they exist — a routine
uploads them with the file-transfer capability like any other artifact. What the engine owns is
the general rule that governs how, and the doc you are reading is the map.

## Why one shell

Seven pages grew independently and each re-implemented the same four things: a masthead, a
feedback channel, a list of the user's unconsumed input, and a webhook ping. They therefore had
the same bugs at different times and got them fixed at different times, or not at all. The state
before unification:

- **two** of seven pages could show the user the feedback he had already sent. The read endpoint's
  project allowlist named `ards` and `nanogeofeld`; the other five accepted feedback through a
  form that could never display it back.
- **one** page refreshed its pending list after a submission; on the rest an entry appeared to
  vanish the moment it was sent (R129/R134, fixed on ards only).
- **one** page rendered an approval draft in an editable box. Everywhere else a correction had to
  be described in prose — the complaint that produced this is store row 5 on `ards`.
- **three** different storage designs were live at once, and therefore three different answers to
  the same question: per-project JSONL behind `feedback.php` for the status pages, a directory of
  whole-file JSON blobs behind each radar's own `api.php`, and a third scheme on the bina host.

The unification is not a redesign that happens to share CSS. It is one shell, one interface and
one storage layout, so a fix lands on every page at once.

## The layout on the host

```
/                          hub — one card per project, sorted by what is waiting on the user
/api.php                   THE interface. every read and every write, for every page
/store.php                 what a project's data IS — layout, row shape, folds, floors
/migrate.php               the one-shot converter; idempotent, additive, deletes nothing
/_store/<project>/         reached only through the API; every file refuses a direct GET
    state.json.php           the state document: phase, prose, deliverables, gate, question
    items.json.php           {generated_at, items:[{id, state, ...}]} — the collection
    model.json.php           what the item states mean: labels, help, tabs, legal transitions
    feedback.jsonl.php       append-only, everything the reader has said
    log.jsonl.php            append-only, every item write and every refusal
    snapshots/               the collection as it was before each replace
/_shared/steward.css       the design system, and the type: it loads its own webfonts
/_shared/steward.js        the shell: masthead, feedback rail, run trigger, the API client
/_shared/modules/status.js the status body — gate, question, state, deliverables, documents
/_shared/modules/board.js  the radar body — Radar, Pipeline, Done, Self-audit (+ board.css)
/<project>/index.html      a ~25-line shell naming the project, language, title and module
```

The master of all of it lives in the library repo at `<libraries_home>/web/steward/` — version
controlled there because it is shared across routines and the library is the one repo that
already is. `pages/generate.py` emits every project shell from one template.

**Why every stored file ends in `.php` and opens with a guard line.** The store holds the
reader's own words and the only copy of his decisions, and "the directory is denied to HTTP" was
a claim resting on a `.htaccess` — which does nothing on nginx, and this hosting is nginx. So the
denial lives in the file: the server executes it, the guard sends 403 and stops, and a direct GET
returns nothing whatever the server config says. Our own reads skip that first line. Basic Auth
still covers the whole host; this is what holds if it ever comes off, which has happened on a
sibling host before.

## The interface

```
GET  /api.php?what=hub&token=<t>                        one card per project, derived
GET  /api.php?project=<p>&what=state|items|model|feedback|log|all&token=<t>
POST /api.php  {token, project, op, …}
       op=say | revise | retract | advance          the reader's, from the page
       op=put-state | put-items | put-model          the routine's
```

`what=all` is what a project page calls: state, items, model and unconsumed feedback in one round
trip. A page never fetches a file.

`what=hub` is DERIVED from every project's own state document. There is no shared registry file,
which removes a class of bug rather than documenting it: the old `projects.json` was one file
every routine rewrote daily, so editing a stale copy silently clobbered a sibling's card and every
routine had to be told to re-fetch first. And `needs_you` is *counted* server-side — an open gate
plus an open question — so a routine cannot understate what is waiting on him.

The whole host is behind HTTP Basic Auth, which is the real gate — a browser carries it and a
routine sends it. The token in the client JS is a namespace marker, not a credential. The
reader's operations are not separately authenticated because they cannot be: the page runs as
him. What protects the data is not an ACL but the shape of the operations — nothing can destroy a
record, and the one destructive operation floors and snapshots first.

**`put-items` floors**, because the collection is the only copy of his decisions: an empty set is
refused, a shrink past half of what is stored is refused, an item without an id is refused, and
the previous set is snapshotted (20 kept). Generalised from the floors `freelance-radar` had
already earned the hard way.

**`advance` is gated** by `model.json`'s `transitions`, and a refused move is *recorded* in
`log.jsonl` — "why did nothing happen when I clicked" should have an answer. A model with no
transitions permits everything: a project that has not described its states should not have its
writes refused for it.

## The migration

`migrate.php` converts every pre-unification store in place. It is idempotent and additive: it
never deletes a source and never overwrites a destination that already exists, so running it
twice changes nothing and a half-finished run is simply repeated. Fetch it once without `apply`
for a dry run, once with `&apply=1` to write, then verify through the API before deleting
anything.

The only genuine shape change is each radar's feedback: `{id, opp_id, verdict, reason, ts}`
becomes the shared row, with `id` (a row number) becoming `seq` and `id` becoming what the
feedback is *about*. Items keep every field they had; `status` is copied to `state`, and an
application draft nested under `engagement.draft.body` is lifted to `artifact` with the original
left in place.

## Who may write what

A routine owns its own project's data and nothing else — there is no shared file left for it to
edit by hand. It does **not** own the shared assets: it carries a byte-identical copy and uploads one only if that path is ABSENT on
the host. First routine to run bootstraps them, every other one no-ops, and no ordering between
routines is needed. Overwriting an existing shared file is forbidden — a stale copy out of one
repo would silently downgrade every sibling's page.

That boundary is the whole reason the fragmentation cannot come back. A routine that needs the
shell to do something new reports it; the change then lands for every page or for none.

## The feedback contract

One row per control click, appended:

```json
{"seq": 25, "project": "ards", "id": "ards · direction", "kind": "steer",
 "value": "...", "client_ts": "...", "server_ts": "...", "ip_hash": "..."}
```

**Nothing is ever rewritten or removed.** An edit is a new row whose `kind` is `edit@<seq>`, a
delete is `del@<seq>`, and the API folds the chain when it reads — so the user's
original wording is always still on disk for the routine, and "change it" and "take it back"
cost a row rather than a record. Encoding both in `kind` is deliberate: it needs no schema change
anywhere that writes, and every historical line stays valid.

`seq` is the **highest seq already in the store, plus one** — not the line count. Counting lines
is only correct while the file has never been touched: a store that is truncated, rotated or
hand-repaired would re-issue numbers below every routine's consumed-cursor, and everything it
held would be filtered out as "already read" and never surface again. (Found by reproducing it
locally against the real endpoint, 2026-08-29.)

`state.feedback_cursor` is the highest seq the routine has consumed. The page lists only rows
above it, which is what makes feedback disappear once acted on and not before. A payload missing
the field is a regression, not a default — the shell says so on the page rather than quietly
re-listing months of already-answered notes.

## The state document

One canonical shape for every status page; the four steward pages previously disagreed on it.
`generated` and `feedback_cursor` are required, everything else is optional and a section with
no data does not render. A routine publishes it with `op=put-state`.

| key | renders as |
|---|---|
| `phase`, `health{text,key}` | the chips under the masthead |
| `phases[]`, `metrics[]` | the phase rail and the figure strip |
| `gate{present,id,text,action,draft,attachment_*}` | the approval panel — **first on the page** |
| `question{id,text,controls,options[]}` | the question panel, always with a free-text box |
| `state{summary,in_flight[]}` | the routine's own prose, on ruled paper |
| `deliverables[]`, `decisions[]`, `mails[]` | margin-marked entry lists |
| `documents[]` | the same, plus a "not ready" / "looks right" control per document |
| `charts[]` | routine-rendered SVG, wrapped so it inherits the page's type and colours |
| `direction_field{id}` | the id the free-text steer box posts to |
| `hook_url` | the webhook a submission pings, so a run fires on real input |

## The design

"Field notebook", chosen against `interface-design`'s named default clusters rather than into
them. Warm paper with a real grain, a red margin rule down the sheet that means one thing only —
something here is waiting for you — and markers hanging in the margin that encode state rather
than decorate it. Three voices, and the type tells them apart: **Fraunces** for the page's own
headings, **Newsreader** for anything written by or to a person, **Spline Sans Mono** for
anything the machine emits. If you can edit it, it is Newsreader; if the system produced it, it
is mono.

Light and dark are both defined on tokens, with an explicit three-state toggle (auto / light /
dark) that persists. The page load is one orchestrated staggered reveal and there are no other
animations; `prefers-reduced-motion` removes it.

## The radars

`freelance-radar` and `grants-radar` were ~150 KB of hand-rolled markup, rendering and CSS each,
with their own palette, their own type and their own copy of the feedback plumbing. None of it
survives: `app.js`, `radar.js`, `pipeline.js`, `pipeline-channels.js`, `filters.js`,
`healthcheck.js`, `urlstate.js`, `util.js`, `timeline.js`, `styles.css` and `mobile.css` are
deleted from both repos. What runs now is the shared shell plus `_shared/modules/board.js`
(+`board.css`) and a ~25-line page, exactly like a status page.

An intermediate approach — a *token bridge* that re-pointed the old stylesheets' twenty custom
properties at the shared tokens — was built, verified and then rejected: it recoloured the old
design rather than replacing it, which is the opposite of unifying.

Two things were carried over on purpose, because neither is design:

- **`config/pipeline.json` is now the entire pipeline UI.** The module hard-codes no stage, no
  label, no button and no help text — it renders that file. Which makes the config more
  load-bearing than it was: a stage's `help` is what the reader gets when he asks what happens
  with a card.
- **The adaptive filter floors**, with the three measurements that produced them. "The first
  screen is the few best matches, and it is never empty" is a tuned property of the data: the
  precision floor reads a robust low quantile of the fits actually pursued (a `Math.min` never
  forgets, and one fit-22 pick once pinned the floor at 20 forever), and the floors relax against
  what ACTUALLY RENDERS rather than the fit distribution alone (910 undecided once became 609
  past recall, 3 past precision and ONE past the remote-only toggle, under a tile still reading
  "910 to triage").

Their `api.php`, `lib.php`, `stage_rules.php` and `stage_log.php` are gone too: the store is the
shared one now, and `config/pipeline.json` became `model.json`. A radar's self-audit rides on
its state document as `state.self_audit`, like everything else a routine says about itself.

## What is deliberately not here

- **`grantsforbina.markwernsdorfer.com`** stays on its own host (operator decision, 2026-08-29).
- **The guest half of the birthday site** stays on `44.markwernsdorfer.com`. It is for party
  guests, and this host is Basic-Auth private; only the admin half moves.
- **The weight-loss PWA** is scheduled to move but is BLOCKED on a host-config change only the
  operator can make: the hub is Basic-Auth-wide, and Basic Auth is exactly what the app's
  passphrase gate was built to replace after a Firefox-Android PWA could not replay it. The path
  needs a Basic-Auth exception first; the app's own gate is the real protection either way.
- **`sprind`** moves under the hub and adopts the stylesheet but keeps its own generator and
  markup — it is a document-review site, not a status page. Its publish is separately blocked by
  R444 (routine scripts do not receive declared secrets).

## The rule

`status-page` in the library is what actually binds a routine to any of this, held by slug in
`routine.yaml` — so publishing a web UI is opt-in per routine, and a routine that does not
publish never reads a word of it. The rule states the payload invariants, the publish order, the
append-only discipline and what a run owes the user back when he edits one of its drafts.

**A permission was considered and rejected.** Gating this behind a `web-publishing` permission
doc would mean `requires: {utils: [ftp]}`, which moves `ftp` into the engine's gated-util set for
*every* routine — silently breaking the four that publish to other hosts. The access that
actually matters is already a per-routine decision: the four-state `secret:FTP_SOURCES` grant.
