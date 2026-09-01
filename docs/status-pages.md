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
/index.php                 hub — one card per project, sorted by what is waiting on the user
/gate.php                  the front door: a session cookie for people, HTTP Basic for routines
/cgi-bin/gate.json.php     the one secret, self-guarding like the store
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
/_shared/modules/board.js  the collection body — views come from the model (+ board.css)
/<project>/index.php       a ~25-line shell naming the project, language, title and module
```

The master of all of it lives in the library repo at `<libraries_home>/web/steward/` — version
controlled there because it is shared across routines and the library is the one repo that
already is. `pages/generate.py` emits every project shell from one template.

**What makes that true is a mechanism, not a sentence.** For a while it was only the sentence, and
the two copies drifted: the feedback-cursor default in `api.php` and the 16 MiB `MAX_BODY` in
`store.php` were fixed straight on the host and existed nowhere else, one re-bootstrap away from
being silently reverted — because a routine uploads a shared asset only when the path is ABSENT,
so nothing propagates in either direction on its own. `steward-hub-maintainer` now holds a write
root on `web/steward/` and reconciles BOTH directions every run: master ahead of host is deployed,
host ahead of master is committed back, and "byte-identical" is one of its stopping conditions.

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

The hub groups its cards into **expandable lists**, not tabs. Tabs showed one group and hid the
rest, so "what needs me today" cost seven clicks to answer and a second group with something
waiting stayed invisible behind the one that opened. Every group is on the page; one with nothing
waiting starts collapsed, the summary carries the counts so a collapsed group still tells you
whether to open it, and the choice is remembered.

`what=hub` is DERIVED from every project's own state document. There is no shared registry file,
which removes a class of bug rather than documenting it: the old `projects.json` was one file
every routine rewrote daily, so editing a stale copy silently clobbered a sibling's card and every
routine had to be told to re-fetch first. And `needs_you` is *counted* server-side — an open gate
plus an open question — so a routine cannot understate what is waiting on him.

`gate.php` runs before anything else. The token in the client JS is a namespace marker, not a
credential. The reader's operations are not separately authenticated because they cannot be: the
page runs as him. What protects the data is not an ACL but the shape of the operations — nothing
can destroy a record, and the one destructive operation floors and snapshots first.

## The gate

nginx-level HTTP Basic Auth was the host's front door until 2026-08-29 and it broke three things,
all the same way: an installed Firefox-Android PWA opens in a fresh context with no credential and
no way to prompt (which is why the weight-loss app grew its own passphrase gate); the Withings
OAuth callback 401'd on the post-consent redirect; and a Plesk http→https 301 dropped the
credential, so a routine parsed an HTML error page as data and lost a run's intake.

Basic Auth is a credential the *browser* holds for a session. A cookie is one an *application*
carries — into an installed PWA, through a service worker's own fetches, and back from a
cross-site redirect (`SameSite=Lax` is sent on top-level navigations).

So `gate.php` takes either form and checks both against the same secret:

- **a person** signs in once per device and gets an HttpOnly, Secure, `SameSite=Lax` cookie whose
  value is `hmac(secret)`, so the secret itself is never stored client-side.
- **a routine** sends HTTP Basic exactly as it always did. Set the gate secret to the password
  already in `WEB_AUTH_SOURCES.steward` and the machine side needs no migration at all — same
  util, same source name, same credential.

`/_shared/*` is deliberately public: a stylesheet, the shell and two body modules, no data. Every
page is `index.php` and opens with `gate_require()`. The secret lives in `cgi-bin/gate.json.php`,
self-guarding like the store — `cgi-bin` being unserved is a property of one host, not a fact.

Retiring it is a two-step with no exposed window: deploy the gate *behind* the existing Plesk
protection, verify, then turn Plesk's protection off. If the gate were wrong you would never be
open, and turning Plesk back on is the rollback.

### What arming it actually cost, twice

**The blanket came off everything, and the gate only covers what calls it.** Plesk's Basic Auth
protected every byte on the domain; `gate_require()` protects one file at a time. Every legacy
path that had never needed to protect itself went world-readable the moment Plesk was turned off,
silently and with no error anywhere: `/freelance-radar/api.php` (4.6 MB — the whole opportunity
store, with which ones Mark pursued and what he drafted), `/freelance-radar/`, `/nanogeofeld/`
plus its `data.json`, and an orphan `/suedlink-wlf/data.json`. The `_store/` guard lines held
perfectly; nothing that had been designed for this era leaked. So the invariant is now stated and
swept every run by the maintainer (`stages/close-ungated-surfaces.md`):

> Outside `/_shared/`, nothing on this host is served to an unauthenticated request.

Closing it turned out to be a lesson in ownership. The obvious repair — convert or delete each
loose file — was not available to the hub: every exposed path belonged to a sibling, and a routine
editing another project's data is exactly what the ownership boundary forbids.
`steward-hub-maintainer` swept, found all twelve, closed none, and was right. A stage that tells a
routine to do something its rules forbid produces a correct refusal, not a fix.

So the closure is host-level and hub-owned, touches no sibling file, and — the operator's
constraint — requires no server configuration at all:

- **`/.htaccess`** denies every data-bearing extension to HTTP, inherited by every project
  directory. **`.htaccess` works on this host.** Plesk fronts Apache with nginx and Apache honours
  it; measured 2026-09-01 against a probe directory, after the opposite had been written down and
  believed for months. `.html` is deliberately not denied — a page protects itself by being `.php`
  and calling the gate, and denying `.html` would take out pages published as deliverables.
  `manifest.json` and `*.webmanifest` are explicitly granted: a browser fetches a web app manifest
  before any credential exists, which is the whole reason this host has a cookie gate, and denying
  it breaks installing the PWA.
- **`gate-file.php`** reads those same files off disk and serves them to a caller the gate
  accepts, so a document a stranger cannot fetch is still one click away for the reader. A session
  cookie satisfies it. It resolves inside the document root only, allowlists extensions rather
  than guessing a content type, and refuses `_store/` and `cgi-bin/` whatever it is handed. Pages
  link a document as `/gate-file.php?p=<path>`.

**One trap, and it took every gated page down for a minute.** Adding any `Require` directive makes
Apache process authorization, and Apache does not hand `Authorization` to a FastCGI script unless
told to — it only looked like it did because nothing here had made Apache process authorization
before. The instant the deny block landed, every page 401'd a *valid* credential. `CGIPassAuth On`
plus a `SetEnvIf` fallback lives in the same `.htaccess` and must stay; `gate.php?diag` is what
tells "the credential was wrong" apart from "the credential never arrived".

That is the belt. The braces are that a project's data belongs in `_store/`, where each file
carries its own guard line and none of this is needed; the five owners hold reports asking them to
move theirs (R1146–R1150). The deny is what covers the loose file nobody has thought of yet.

**And a refusal could not say which refusal it was.** Arming the gate without updating
`WEB_AUTH_SOURCES.steward` to the same passphrase locked out all ten publishing routines at once
(R1143). The rollout note above says to set them to the same value; it was skipped, and the
resulting 401 is indistinguishable from a header the server ate — `PHP_AUTH_PW` is filled only
under mod_php, and a FastCGI bridge drops `Authorization` unless the vhost forwards it. Both
causes now have one answer: `gate.php?diag` reports which credential channels carried anything on
the caller's own request and whether each was accepted. It reveals nothing — armed state is
already visible from any 401, and the rest is a fact about the caller's own request — and it is
reachable without a credential on purpose, because it is needed exactly when you cannot get one
in.

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

One canonical shape for every status page, and since 2026-09-01 the write path ENFORCES it.

It had to. The renderer reads the keys it knows and ignores the rest, so a misspelled key was
never an error — it was an invisible section, and six of nine publishers had drifted apart that
way without one of them finding out. `nanogeofeld` published `signoff` and `mail` where the shell
reads `gate` and `mails`, so the day it had an approval to show, the panel would not have rendered
and the hub would have counted zero things waiting on him. `sprind` published `feedback_seen`,
hardcoded to `0`, so its rail could never hide what had been acted on. `freelance-radar` and
`birthday-admin` published no cursor at all. Prose in a rule could not stop any of it; a refusal
naming the key can.

`put-state` now refuses an unknown top-level key, listing it and the accepted set, and refuses a
document missing `generated`, `feedback_cursor` (a whole number) or `card` — the three with no
safe default. `store.php`'s `STATE_KEYS` is the one list, and the `status-page` rule states it in
the routine's own terms.

**The extension point is the model, not the data.** A project that needs a section of its own
declares a journal view whose `source` names the key; `board.js` already renders exactly that, so
the new section is visible in the model rather than guessed at from the payload. `sprind` uses it
(`review`), and `birthday-admin` (`planning`).

Two dual conventions went with it: the shell read `question || open_question` and
`mails || correspondence`. Two spellings for one thing is how two publishers can be differently
wrong and both look fine.

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

## Who is reading, and what a visit means

Until 2026-09-01 this host had exactly one identity: everyone who knew the passphrase got a cookie
whose value was the same digest for everybody, and could read and write every project. There was
no way to show one page to one person, and no way to tell afterwards whose "looks right" was on
the record.

**An invitation is a link and a label.** `invites.php` folds an append-only log into the current
set; the link carries the invite id plus a digest of it keyed by the host secret, so the server
looks the invitation up directly and cannot be handed a forged one. Following `/i.php?t=…` swaps
the token for a cookie and redirects — a token in a URL survives in history, in a chat log and in
a screenshot of the address bar, and every request after the first carries a cookie instead.

| role | may |
|---|---|
| `viewer` | read |
| `commenter` | read, say things, revise or retract their own (the default) |
| `decider` | also answer a gate and advance an item |

Nobody but the owner writes a routine's own documents. A guest with `put-state` could rewrite a
project's whole record, which is not something a link should ever carry — and only the owner mints
an invitation, or the first guest could mint a second for the rest of the host. **Scope is checked
twice**, in the page and again on every read and write, because a control the page did not draw is
not a control the caller cannot reach. Revoking is one appended row and takes effect on the next
request; the rows stay, so what a withdrawn guest said keeps its author.

Every feedback row now carries `who` and `role`, and the rule tells routines what to do with that:
a guest's approval is not his, and reading one as the go-ahead is how a mail goes out on the wrong
person's say-so.

**Two marks, two meanings.** `needs_you` is an open decision and only answering clears it; `unseen`
is "changed since you last looked" and opening the page clears that. Collapsing them into one mark
was tempting and would have been wrong — a hub that forgets what is waiting the moment you glance
at it has stopped doing its one job. The revision counter behind `unseen` is the API's, bumped on
every `put-state`, and it is deliberately NOT in the state document: that document has a closed key
set, so a routine amending its own state and writing it back would hand the counter straight back
and be refused for a key it never wrote.

`needs_you` also counts an unanswered document now. Counting only the gate and the question meant
the two radars — the pages with the most sitting on him — reported the quietest cards.

## Where a run is, while it is running

Between runs the page showed a date; during a run it showed nothing, so two routines could work
for half an hour while every card claimed yesterday. `put-progress` fixes that, and the run
publishes it itself at each stage boundary — the engine sends nothing outward on a routine's
behalf, and one turn per stage is what stage-level honesty costs.

It is a **heartbeat, not a flag**: the store stamps each update and computes `live` from its age
against a 25-minute TTL, so a run that dies mid-stage decays to "last seen" rather than leaving a
spinner that outlives it. The page renders a chip, a step count and a rule that fills; the pip's
pulse is the only animation on the host that is not the load reveal, and it is there because it
encodes the one thing on the page that is changing while you look at it.

## Submitting, and what it is for

Every control writes immediately and stays revisable until the routine consumes it. What used to
also happen was a webhook ping per control, so one sitting could start a run per click.

Now starting a run is a separate, deliberate act: a checkbox — off by default, remembered per
project — and a button that is dead without it and says why. The panel offers to run on anything
the routine has *not consumed*, not merely what you changed in this sitting, so coming back
tomorrow to an unsent note still offers to wake the routine. Where a project has no webhook, the
panel is a sentence rather than a dead control.

The reach is the remaining half: the hook URLs point at the console's Tailscale name, so a
submission only arrives from the tailnet until `/api/hooks/*` is published.

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
with their own palette, their own type and their own copy of the feedback plumbing. The unified
replacement is the shared shell plus `_shared/modules/board.js` (+`board.css`) and a ~25-line
page, exactly like a status page.

**`freelance-radar` has not finished moving.** Its working dashboard is still the old static app
reading its own per-project store, which `build.py` writes every run; the unified board is a
second copy of the same project.

The blocker that stood — `board.js` dropping the engagement pipeline detail (R1073/R1089) — is
gone: the card now shows what blocks it, what the routine plans next, the flags it raised, a
reply that came back, and the channel an application would go out on. What remains is data
freshness, not features. The unified store was last written on 2026-08-31 and holds 990 items
against the old store's 1018, because `publish_unified` was blocked by the gate lockout for a
day. **Swap the page only after the routine has published to the unified store again** — a page
that is complete and stale is worse than the old one, which is at least current.

The funnel is the other half of why the unified board is now the better surface. A triage board
narrowed by three defensible filters reads as broken: 748 undecided become 411 past the score
floor and 20 past the fit floor, and the tiles say only "748" and "20". Each bar names the
control that cut it, so the narrowness is legible and undoable rather than mysterious.

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

## A project can own its body

The shell used to offer two bodies, `status` and `board`, and nothing else. A project whose data
did not fit either had no move except to report it. `module: "own"` loads a project's own
`page.js` and `page.css` from its directory; it still gets the whole shell, and colour, type and
spacing still come from the shared tokens. What it owns is the shape of its own data — the part
that genuinely differs between a grant deadline, a guest list and a scored pipeline.

**What made that safe to offer** was moving the approval gate and the open question out of
`status.js` and into the shell, above every body. They had lived in one module, so every other
module had to remember to render them, and `board.js` never did: a radar, a review site and a
party's admin page could each report a waiting decision on the hub and then show nothing when
opened. Four of nine pages. No body can forget now, because no body renders them.

`_shared/ui.js` + `ui.css` are the vocabulary bodies build from — figures that count up on
arrival, sparklines, meters, timelines, things that open, view switches — so a reader who learns a
gesture on one page finds it means the same on the next. Ten bespoke bodies would have been ten
copies of this, drifting apart, which is exactly the failure the payload contract exists to
prevent. It is dependency-free on purpose.

**Three bugs came out of building it, and all three had the same shape: an effect that was also
load-bearing for the content.**

- `IntersectionObserver` silently ignores a node that is still inside a `DocumentFragment`, and
  bodies are built in one. Every reveal, counter and meter sat at its starting value. Nothing
  errored — figures just rendered empty. Work is queued at build time and armed once attached.
- An observer only fires on a **rendering step**, so a document that is never composited — a
  background tab, a PWA launched behind, a scraper — gets no callback at all. Reveals start at
  `opacity: 0`, so such a page was not un-animated, it was blank. Every deferred job now carries a
  deadline; whichever comes first runs it exactly once.
- `requestAnimationFrame` does not run there either, so a counter that produced its value through
  rAF produced nothing. The true value is written first and the count animates over it.

The rule states the principle those share: an animation is a nicety, the content underneath it is
not, and motion marks arrival or change rather than idleness.

## The collection module takes its views from the model

`board.js` started as the radar body and is now the body for anything with a collection. A
project's `model.json` says what its tabs are and what each one shows, so no project is a special
case in the module:

| `type` | shows |
|---|---|
| `triage` | the undecided items, filtered, with the adaptive floors |
| `board` | stage columns — every `stages` entry whose `tab` is this view's key |
| `journal` | the state document's own prose and entry lists (`source` picks which) |

A model with no `views` falls back to the radar's four, which is the shape that existed when the
module was written.

That generalisation is what let `birthday-admin` — a journal plus **two** collections, guests and
venues — render on the same module as a radar, and `sprind` on it too. Venue states there are
prefixed `venue_` because `transitions` is a flat map keyed by the from-state, and a guest and a
venue can both be "declined".

**"Waiting on you" is counted from `next_actor`, not `actor`.** They are different fields and the
radar's own config documents why: `actor` decides whether a stage renders buttons, `next_actor`
says whose move is next. Counting `actor` made a party's 63 invited guests read as 63 things
waiting on Mark, when what they wait on is their own reply.

## What is deliberately not here

- **`grantsforbina.markwernsdorfer.com`** stays on its own host (operator decision, 2026-08-29).
- **The guest half of the birthday site** stays on `44.markwernsdorfer.com`, untouched — and so
  does the roster's master, which is a harder constraint than it first looks. That site keeps two
  rosters: an off-web authoritative one the guest invite gate reads, and a git-tracked seed the
  routine regenerates. The authoritative one has **two** writers — the admin roster-board save and
  **guests themselves**, who can drop a stray member from their own party through a token-gated
  endpoint. Since a removal has to kill an invite link at once, and `birthday` has no webhook
  trigger and an empty cron, this host cannot hold that guarantee. So membership stays on 44 and
  the steward guest list is a published mirror; only *attendance* state (invited / coming / maybe
  / declined) is set here and reconciled on the routine's next run. Its `model.json` carries no
  membership action and records why.
- **The weight-loss PWA** was blocked on the host's Basic Auth, which an installed PWA cannot
  replay. That is what `gate.php` fixed: a cookie is carried by a PWA, so the app moves under the
  hub with no exception and its own passphrase gate is retired.
- **`sprind`** and **`birthday-admin`** are built on the collection module, not adapted: their
  own markup, stylesheets and rendering are superseded. R444, which reported sprind's publisher as
  blocked, was stale — R445 had diagnosed it (engine keys in the PEP 723 block, not the
  docstring) and the fix was applied on 2026-08-29.

## Verification is the rule's, not the operator's

Every check that found something today was run by hand: reading a stored document back, fetching a
path with no credential, opening a listed deliverable. Each of them had been passing for weeks in
the routines' own words, because a routine verifies while signed in and therefore never sees what a
stranger sees.

So the `status-page` rule now states three proofs, each named after the failure it catches — a
write refused for a misspelled key, twelve loose files world-readable behind a removed blanket, and
a deliverable advertised that had never been uploaded. `steward-hub-maintainer` carries a fourth,
because it owns the kit in two places: host and master compared both ways, with both hashes
recorded, since one hash cannot show a drift.

## The rule

`status-page` in the library is what actually binds a routine to any of this, held by slug in
`routine.yaml` — so publishing a web UI is opt-in per routine, and a routine that does not
publish never reads a word of it. The rule states the payload invariants, the publish order, the
append-only discipline and what a run owes the user back when he edits one of its drafts.

**A permission was considered and rejected.** Gating this behind a `web-publishing` permission
doc would mean `requires: {utils: [ftp]}`, which moves `ftp` into the engine's gated-util set for
*every* routine — silently breaking the four that publish to other hosts. The access that
actually matters is already a per-routine decision: the four-state `secret:FTP_SOURCES` grant.
