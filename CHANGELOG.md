# Changelog

## [0.609.0] — 2026-11-24

**D306 point 2 — branches in the conversation rail, live. The order is now complete and its
design entry is deleted in this commit.**

The operator kept conversation branches and diagnosed their non-use as the console's fault
(*"unused only because the UI is bad"*, 2026-11-21 code review). Point 2 named the remaining
gap exactly: branches were "a lineage LINE in the header: names joined by commas, no state, no
way to tell a branch working from one long finished".

- **A `branches` rail section** (`static/components/branch-rail.js`): one row per relative — the
  parent it was forked from and each branch forked off it — carrying the name as a link, the
  state as the same `chip(state)` every other surface uses, the fork turn, when it last moved,
  and "handed back" once it has. Both directions, because a branch needs to offer the way back
  as readily as the way down. Hidden when there is no family, which is the overwhelming
  majority, and refreshed on `run_started` / `run_finished` for a slug of this family — never
  polled.
- **`GET /conversations/{slug}/lineage` gains the live fields** (`src/rsched/web/branch_lineage.py`,
  new): `state` and `updated` per relative, out of the conversations catalog the page has already
  read (memoized and single-flighted, so the rail costs no walk of its own), and `handed_back`
  read from the parent's inbox — still queued, or archived under a run's `consumed/`. The flag is
  READ, never stored: a copy in the branch's config would be a third place one fact lives.
- **The section makes no request of its own.** `branchControls` already reads the lineage once
  per real family change, behind a signature guard, and point 5 made that read conditional; the
  section is fed by it. A browser test asserts exactly one `/lineage` request per view.
- `static/views/conversations.js` **423 lines (was 457, at its ceiling)** — the rail's whole
  construction moved to `static/views/conversation-rail.js`, so the feature was paid for by
  extracting a responsibility rather than by shaving the frozen file; ceiling tightened.

14 guards proven red-first (8 server, 6 browser): the queued and the archived hand-back halves
separately, another `via` not reading as one, an unreadable message skipped rather than raised,
both the branch and the parent row's live fields, the state chip, the fork turn, the hidden
section and the single-read feed. One of the eight was `unproven` first time round and the
finding was about the test: the route guard asserted `state` was PRESENT, which a route
answering a constant `new` for everything would satisfy — it now asserts the real value.

## [0.608.0] — 2026-11-24

**D310 / goal b8: decisions render through A2UI, with element types this console did not have.**
The operator's order was *"use https://a2ui.org/ for all decision / ask elements"*, and his
correction set the bar: *"We shouldn't be fixated on the controls we already use. We need to
improve to UI/UX with new elements!"* — so this is the full Basic Catalog (18 components), not a
protocol wrapper around the five cards that exist. Five commits, one campaign, released together.

**The protocol, in vanilla ES modules with no bundler and no `node_modules`** — which is what
keeps the browser gate driving files served straight from `static/`. Six modules by layer:
`surface.js` the state (all six envelope types, JSON-Pointer binding, collection scopes, two-way
binding, the protocol's refusals), `expr.js` the two little languages (the A2UI regex dialect
with the spec's static safety check — a pattern is rejected on its SOURCE text, because a
backreference compiles perfectly well in V8 and that is exactly the problem — and
`formatString`'s `${…}` grammar, where brace depth is counted so a nested expression is one),
`functions.js` the catalog's fourteen functions, `render.js` the core walk with `@checks`,
`@accessibility` as ARIA, `weight` as flex-grow and CATALOG RESOLUTION (a component resolving to
no catalog is refused rather than falling back to the only one registered, which the spec forbids
in those words), and `widgets-layout.js` + `widgets-input.js` for the eighteen components.

**What is new on the page**, which is the claim worth checking: a decision's options are a
mutually-exclusive **ChoicePicker** with chips, filterable past six; the typed answer is a
**longText TextField** with a renderer-evaluated `@checks` rule; what happens without an answer
is a **Modal**, an element this console has none of. `GET /api/questions/{qid}/a2ui` serves the
same record every other decision surface reads as a v1.0 `createSurface` envelope, so the
console's own surface and one a routine streams are indistinguishable to the renderer. The
Decisions page offers it as a fold under each card — closed by default, because the card is what
the operator answers real work on and replacing it with a rendering he has not seen would be the
wrong order.

**The answer still travels one route**: the surface's submit is an A2UI agent event, routed to
`POST /api/questions/{qid}/answer`, so `answerfile.write` stays the only writer of the inbox's
answer shape, and an unhandled event is reported with its name rather than swallowed.

The fold's room came from an extraction along a responsibility: `questions.js` was at 446/446, so
the settled RECEIPT moved to `static/components/settled-card.js` — **403 lines with the feature**.

**41 guards proven red-first** across the five steps, each neuter a named red and each file
restored byte-for-byte. Two were `unproven` first and both are findings about the TESTS, left in
their own docstrings rather than quietly fixed: a chip-picker test clicking one chip never
exercised "mutually exclusive", and the fold's `onAnswered` filtering is not what removes an
answered card — the store's bus snapshot does, so that callback buys latency, not the outcome.

## [0.607.0] — 2026-11-24

**D299/F931: the backup outcome that actually HAPPENS is now the one that gets recorded, and the
packing step that was supposed to make the off-box tier fast was doing nothing at all.** 0.603.0
shipped the record D299 asked for, and this run read it for the first time. It held exactly one
row for the night of 2026-11-24 — `local complete`, 86 s — and nothing about the off-box tier.
That silence was the defect, in two independent places.

**The record was blind to the ending that happens nightly.** Its three record sites are all
reached only by a script that runs to its own end; on this host the off-box tier ends at the
unit's `TimeoutStartSec`, where systemd sends SIGTERM and bash dies inside rsync without reaching
any of them. Measured from the unit itself: `Result=timeout`, `ExecMainStatus=15`, started
01:13:20, killed 07:13:20. So `deploy/backup.sh` now carries a TERM/INT trap over an in-flight
marker (which tier is inside its rsync, and since when), and a cut-short night records
`outcome: "killed"` with its elapsed seconds and its cause. The handler re-raises the signal it
handled, so systemd still sees a failed unit and its `OnFailure=` push still fires — a trap that
returned normally would turn a killed night into a reported success. A REFUSED off-box target
(an unmounted share) records `outcome: "refused"`, which previously also produced nothing. SIGKILL
cannot be trapped and is not claimed: the share's stamps remain the evidence for that case.

**And the packing step packed nothing — for two separate reasons, both measured.** It exists to
remove loose git objects before the mirror, because on sshfs each one is a file and so one round
trip, and F931 measured them at 38% of the transferred set. Last night's own journal line was
`git objects: 44687 loose before, 44687 after (0 repo(s) packed)`. First, git's `gc.auto`
default is 6,700 loose objects in ONE repository, while this host's 45,277 are spread over ~30
repos whose largest holds 4,344 — so no repo ever crossed it. Second, and worse, `gc --auto`
does not count loose objects at all: it samples one fanout directory (`objects/17`) and
multiplies by 256, so a repo with 36 loose objects estimates 0 and is never packed at ANY
threshold, `gc.auto=1` included. The script already counts them itself, so the decision is now
its own (`GC_LOOSE_FLOOR`, default 200) and the gc that follows is unconditional —
`gc.autoDetach=false` because git's default is to FORK, which would leave a gc rewriting the
object directories while rsync walks them, and `--no-prune` because a backup may pack history
but must never decide an object a run just wrote is garbage. Measured: 4,344 → 0 loose in 10 s
on the largest repo, 1,653 → 0 in 5.5 s on a median one, history intact.

The cost this is aimed at, from the same journal: off-box mirrors that COMPLETE take
5,926–15,897 s (1.6–4.4 h), and 6 of the last 15 attempts have a start line and no completion
line. Nine guards proven red-first; the tenth is named in its own docstring as unprovable by
that test and proven by the structural guard beside it.

## [0.606.0] — 2026-11-24

**The subscription surface leaves routine-scheduler; the Routines page gains a token pace.** The
operator, after the CLIProxyAPI management panel was repaired: *"the health read is on
[the proxy's panel] so i don't see why we need anything in routine-scheduler besides the
endpoints. we don't need any mention of subscriptions in routine-scheduler. what would be helpful
would be a token count on the routine overview page so we can anticipate our token budget running
out."* Asked whether to keep a minimal login-dead signal, he chose the full removal including the
credential watch; asked which ceiling the new readout should count against, he chose *"tokens/month
per routine + the trend (this month vs last, and a projection to month end) — no ceiling claimed"*.

An endpoint in this product is now a TRANSPORT and nothing else. Removed:
`endpoints/{cliproxy_login,cliproxy_mgmt,cliproxy_quota,subscription_quota,endpoint_health}.py`,
`daemon/endpoint_watch.py`, the six proxy/quota/credits-adjacent routes on
`settings/endpoint_probe.py` (the pay-per-use CREDITS read stays — a balance is not a
subscription), the `endpoint_needs_reauth` blocked-work condition across `health_vocab`,
`blocked_kinds`, `operator_blocks`, `health_stream` and `operator_conditions`, the
`quota_source` / `quota_key_var` / `quota_auth_index` endpoint fields, the dashboard's Claude-quota
chip, `static/components/{proxy-accounts,quota}.js`, and the `.warn-line` rule they were the only
users of. The `cliproxy-mgmt` endpoint is gone from `config.yaml`; `CLIPROXY_MANAGEMENT_KEY` no
longer reaches this product. `gateways.<name>.plans` STAYS — `endpoints/routes.py` reads it to size
a model's window by its route, which is not a subscription fact.

**What this gives up, on the record.** The credential watch read the proxy's account list every ten
minutes and filed a standing blocked-work row when a subscription login died, because one expiring
claude-proxy token failed eight routines at turn 0 within two minutes and nothing detected it.
That failure mode is now visible as those runs failing, not as a warning beforehand;
`docs/contracts.md` retires the guarantee in those terms rather than deleting it silently.

In its place, the question the operator actually wanted answered: `readmodels/token_pace.py` adds
the token COUNT and a month-end PROJECTION over the series `monthly_spend` already parses — per
routine on its card, and for the whole instance as a header chip
(`GET /api/routines-token-pace`), since a subscription window is spent by every routine at once.
No ceiling is claimed: per-routine `max_total_tokens` defaults to -1 and nothing sets it, so a
"% of budget" would divide by a number that does not exist. A projection is withheld until the
month is 15% gone and `elapsed_frac` always rides beside the figure. Proven on the live usage
stream: November read 153,563,448 tokens at 77.9% of the month, projecting ~197,043,069 by month
end ($8,079.52 notional → ~$10,367).

## [0.605.0] — 2026-11-24

**Hook activity moved off the Routines page onto Messages, on the operator's order.** He wrote:
*"'hook activity' needs to move away from the routines page. to me it seems like there's
considerable overlap with the 'messages' subpage up to the point where hooks actually completely
replace messages. confirm"*.

**Confirmed, and the census is why the panel moves rather than being deleted.**
`.control/hooks.jsonl` holds 8,043 rows: `report` **6,064**, `injection` 1,421, `reminder` 484,
`runtree` 72, `schedule_once` 2. The report count is exactly `reports.jsonl`'s line count,
because the hook is the SOURCE of every report and the ledger is a projection of it
(`hooks/reportproj.rebuild` reconstructs the whole ledger from the stream alone). So the Messages
page and the hook stream are not two overlapping things: the ledger Messages reads IS the
stream's majority, and the panel showed the 1,493-row minority — pending one-shots and a day of
injections and run-tree hand-backs — on a different page entirely.

The panel now sits above the ledger list, beside F793's orphan groups, which is the same class of
thing: what reached this page without becoming a filterable row. It keeps its own 60 s refresh and
still renders zero as a reading. `static/views/dashboard.js` keeps the week strip and the live
feed.

The panel's subtitle moved from the page-level `sub` class to its own `hookact-sub`. Two reasons,
one of which the first gate found: `base.css` styles that class only as `.page-head .sub`, so
inside the panel it drew nothing, and a second `.sub` in the Messages DOM turned
`test_messages_page`'s `locator(".sub")` into a Playwright strict-mode violation — the same
collision this panel's `quiet()` already avoids for `.empty`. Its styling is now explicit in
`hookact.css`, in tokens.

`tests/ui/test_hook_activity_panel.py` loads `#/messages` and gains
`test_the_routines_page_no_longer_carries_the_panel` — the move has two halves, and a test that
only checked the page it moved TO would pass while the panel the operator asked to be gone was
still there. `static/views/messages.js` stays at its 363-line ratchet ceiling (the mount is four
lines and the comment above it paid for them); `dashboard.js` shrank 397 → 394 and its ceiling was
tightened to match.

## [0.604.0] — 2026-11-24

**F944: `cd <own dir> &&` no longer flips a shell call from process to task.** The overhead
classifier's `_relative` compared a path against `routine_dir + "/"`, so a token EQUAL to the
routine's own directory — which is exactly how `cd /home/mark/routines/<slug> && …` names it —
did not match, returned `None`, and was booked to the task. The filter at `_call_is_process`
was already written to drop such tokens (it tests for `"."` or `""`); it simply never fired.
One token of shell STYLE therefore decided a run's whole classification: `cd <dir> && grep -n
… state/shows.json` read as task while the same command bare, or with a trailing slash on the
`cd` target, read as process.

Reported by `routine-improver` and narrowed by `scheduler-builder` (the trailing-slash
asymmetry is theirs). The consequences were both real: it HID overhead, and it MANUFACTURED
regressions between runs whose only difference was whether their shells carried the prefix —
one such comparison read a measured improvement (≈0.40 → ≈0.33) as a 34% → 44% rise and was
one step from reverting the change that worked.

`_relative` now returns `"."` for the own directory named absolutely, with or without a
trailing slash, so every caller (`is_process_path`, `_bucket`, `_in_state`,
`_call_is_process`) sees what they were written to expect.

**The calibration table was re-run, as the module's docstring requires, and `THRESHOLD` does
not move.** Over all 28 routines' newest five measured runs, the corrected classifier raises
eight medians — self-audit 0.145 → 0.196, rules-review 0.344 → 0.394, tv-show-tracker 0.403 →
0.424, drawerrr 0.112 → 0.175, scheduler-builder 0.173 → 0.182, routine-improver 0.142 →
0.147, fau 0.196 → 0.215, feeld-android 0.120 → 0.121 — and leaves every other median
unchanged. No routine crosses the bar in either direction, so the 0.27 midpoint still separates
11 of 12 flagged from 0 of 8 healthy. Shares RECORDED before this fix for a routine that used
the prefix were measured on the old classifier and are not comparable with ones taken after it.

## [0.603.0] — 2026-11-23

**D299: what a backup ATTEMPT cost, recorded where a run can read it.** The operator answered
D299 with (b) — grant a run the means to diagnose the off-box backup instead of only reporting
its staleness. The stamps say a night started and did not finish; they could not say whether it
missed by a minute or by three hours, nor how much it moved, and the remedy the stale health
event printed (the backup unit's journal) does not exist inside a run's sandbox.

`deploy/backup.sh` now appends one JSON line per attempt per tier to
`~/routines/.control/backup-attempts.jsonl` — the directory every run already reads — at three
sites: the local tier completing, the off-box tier completing with rsync's own transferred
figure, and the off-box tier FAILING, which is the case that happens nightly on this instance.
The off-box function already computed its elapsed seconds and that figure for its stdout; this
routes them somewhere durable. It is append-only, overridable by `BACKUP_ATTEMPTS_FILE`, and
never fatal: a backup must not fail over its own bookkeeping.

A new `daemon/backup_attempts.py` is its only reader, per TIER (the off-box tier fails while the
local one completes nightly, so the newest row of the file would answer about the wrong tier
almost every time). `daemon/backup_watch.py` appends the clause to a STALE verdict only, and its
remedy now names the readable record before the journal.

The reader is a module of its own rather than more of `backup_watch.py`: the stamp arithmetic
asks *is the backup current?* from two timestamps on a share, this asks *what happened last
night?* from a log on local disk, and they fail independently.

11 guards, all proven red-first — including `bash -n deploy/backup.sh`, because this is a
758-line script that runs unattended as the only thing between this instance and a disk loss.
Two of the eleven were reported unproven first and were fixed: one passed vacuously, one sliced
the source at prose that repeated the words it looked for.

## [0.602.0] — 2026-11-23

**The playbook offer where a conversation ENDS, and the conversations view split.**

**D307 (2/2), the operator's playbooks order.** A finished conversation now offers to be saved
as a playbook at its ENDING, not only at the composer — "playbooks are where a conversation
starts and ends". The offer reads as one sentence, a conversation still working is never called
done, a short one is not asked at all, and a conversation seeded FROM a playbook offers to
update that playbook instead of making a second one. `static/components/playbook-row.js` grew
`finalOffer`; `static/playbook.css` carries its rules.

A send used to spend an extra `/api/conversations/{slug}` read: the offer refreshed itself on
every terminal state transition and a send is terminal for an instant. It is now asked only
where the finish event lands, carrying that event's own `final` flag. The 0.602.0 candidate's
first gate caught it (`test_a_send_resumes_the_tail_instead_of_remounting_the_view`) and the
candidate was restored before it reached `main`.

**`static/views/conversations.js` was SPLIT, not squeezed.** It sat at its size ceiling with zero
slack, so a four-line fix could not land; the answer is a seam, not shorter comments. The
sidebar — the conversation list with its out-of-order read guard, the search/tag filter, the rows
and the shared hover card — is now `static/views/conversations-list.js`, and the view is 538 →
457 lines with its ratchet ceiling tightened to match. Which conversations exist is read and
changed separately from the one being read.

Verified on the shipped bytes: 68 scanner tests (size ratchet, quality, four static scanners,
class contract, stylesheet tokens, policy, doc-claims, prose history) and 80 browser tests —
every conversation, chat, branch, rail, playbook and offer module, because an extraction of this
view answers for all of them, not only for the item that touched it.

## [0.601.0] — 2026-11-23

### Added
- **A visible "⑂ branch" on every reply, and the branch starts working from one click**
  (D306 points 1 and 4, his 2026-11-21 code-review order — he kept conversation branches and
  diagnosed their non-use as the UI's fault, *"unused only because the UI is bad"*). The fork
  control leaves the bubble's hover-only corner family and becomes a labelled control in the
  reply's footer row, at full opacity in the reading ink instead of `.45` on hover and invisible
  on a touch screen; ⧉ ⟲ ⚑ are untouched and stay mellowed. `ForkBody` gains
  `instruction: str = ""`, and when it carries text the route files it through the same path
  `POST /conversations/{slug}/message` uses, so the branch is working the moment it exists —
  the operator's own click, one of the three things allowed to start a run. An empty instruction
  keeps today's behaviour (the branch opens idle), and the route refuses 503 BEFORE forking
  while the runner drains, so a drain can no longer leave a branch created but never started.
  `static/components/chat.js` was AT its ratchet ceiling, so the per-reply controls were
  EXTRACTED to `static/components/reply-controls.js` first: it is now 322 lines, **under the
  350-line bar, and its ratchet entry is gone** rather than merely lowered.

### Fixed
- **Every item id the console renders is now a link to its item** (goal b9, his words today:
  *"ids like D306, D307, D307b, or D310 in messages should all be hyperlinks to the actual
  item"*). The cause was structural, not a missing surface: `linkifyRefs` was applied by twelve
  per-surface call sites, so a run's own narration — the surface he reads most — carried bare
  ids while the Messages page linked the same ones. New `static/components/prose.js` composes
  the markdown renderer with the id linker, and the transcript and chat import `md`/`mdInline`
  from there: narration, finish summaries, questions, observations and user messages are all
  covered by the one change, and neither file (both at its ceiling) gains a line.
- The id pattern accepts a **letter suffix** (`F557b`, `D307b` — the old one matched `F557` and
  left a stray `b`), and the new `linkifyKnownRefs` links only ids the item index actually
  CARRIES, which is what makes it safe on prose nobody wrote as a maintenance record: in a
  sentence about a grade or a key, `D1` and `F5` are ordinary text, and a link to a card that
  does not exist is worse than no link. Its first pass is synchronous, so a link never arrives a
  frame after the words it belongs to.
- `tests/test_static_imports.py`'s md-helper invariant was generalized rather than bypassed (a
  per-helper source list, plus a guard that `prose.js` really wraps what it stands in for), and
  a DUPLICATE `_imported_from` definition in that file — the second shadowing the first — is
  gone.

## [0.600.0] — 2026-11-23

### Added
- **A detached task is started from the composer, and it lives in the conversation rail**
  (D308's first increment, points 3 and 2 of his 2026-11-21 code-review order). A "⇥ background"
  control beside send takes the composer's text as the task prompt, offers its first line as the
  label in a one-field dialog, and posts both to the existing
  `POST /api/conversations/{slug}/background`; the box is cleared and **no reply is started** — a
  person delegating a job is not a person asking the assistant something. The rail's `background`
  section (`static/components/bg-tasks.js`) loses its 15 s content-armed poll and refreshes on
  the `run_started` / `run_finished` bus events whose run id is one of this conversation's task
  slugs; the only timer left ticks the elapsed number in the DOM, asks the server nothing and
  disarms when no row is live. Each row now carries the elapsed time while live or, once
  terminal, **"delivered" / "awaiting delivery"** from the `delivered` flag the API already
  served but nothing rendered, a link to the task's own run page, and each landed file under
  `artifacts/from-bg-<taskid>/` as a button into the artifacts panel. `started` is the run's
  existing `ts`, and `files` are listed only once `delivered.json` exists — linking files that
  have not reached the conversation would assert a delivery that did not happen. A task launched
  from the composer is served as a `queued` row from `.requests/` until the manager's next 5 s
  tick, so "it appears in the rail at once" holds exactly rather than on average. The chat side
  needed NO code: `handback-card.js` already carried `background` in `CHANNELS`, so the delivery
  already rendered as the hand-back card — what was missing was the test. Point 1 (the `detach`
  copy and the `conversation` prompt-budget measurement) is the second commit.

### Fixed
- **A routine created today is born with an uncensored role it can resolve** (R3680, reported by
  `config-optimizer`, the operator's answer: default it on scaffold). Every settings pattern
  ships `models: {}`, so a routine created outside a pattern had no uncensored role at all —
  `for_uncensored` returned `None`, and on a classifier refusal `degrade._handle_refusal` had no
  target to re-drive the turn on: it walked the catalog fallback chain (more censored models) and
  ended the run. `workflows/scaffold.py` is the one chokepoint all three create routes pass
  through, and it now fills the role when the new config names none. **No model name is written
  twice in the tree, not even as a constant**: `workflows/uncensored_default.py` derives it from
  this instance — the value the fleet already agrees on (kept only if the catalog still serves
  it, since a stale name reproduces F643) and, failing that, the catalog's sole guardrail-free
  model; neither answers and nothing is written, as before. The engine was deliberately NOT
  changed: `None` from `for_uncensored` means "referral deliberately off" and three surfaces
  reason from it, so defaulting there would switch referral on invisibly for every routine whose
  role is unset on purpose. A role already named by the pattern, the caller or the person is
  never touched.

## [0.599.0] — 2026-11-23

### Fixed
- **The console's unexplained multi-second waits were the credential-store guard, and it now
  resolves the stores once instead of once per question** (F906, second half). The phase
  instrument shipped in 0.588.0 answered the open question: every slow row recorded since the
  restart reads `mostly app` with `app ≈ elapsed` and `first_byte == app` — so `send` is ~0 and
  `pre` is 0.0-0.1 ms, several of the rows at `in_flight 0`. The wait is neither queueing before
  the handler nor the response path; it is real work inside the application span, and the
  self-samples named the frame: `registry.scan` → `load_config` → `load_routine` →
  `entities.guarded_roots` → `never_grantable_fs` → `_spellings` → `Path.resolve` → `realpath`,
  with the threadpool healthy (40 tokens, 4 borrowed). Measured on `main`: one `load_routine`
  pass over this instance's 30 routines cost **1,592 ms and 872 `Path.resolve()` calls**, 3.00 s
  of a 3.15 s profile under `guarded_roots` (1,127 `stat` + 4,006 `lstat`), because
  `_touches_store` rebuilt the resolved spellings of all 12 credential stores on EVERY call —
  13 resolves per guard question, asked once per folder-grant entry of every routine the
  registry scans, on every `/api/runs`, `/api/conversations` and `/api/questions` request. The
  resolved set is now cached behind a key of the stores themselves plus an `lstat` stamp of each
  store path: **546 ms cold, 66 ms warm, 80 resolves**. A patched `NEVER_GRANTABLE` still reaches
  the guard (the stores are in the key — the contract every enforcement-door test relies on); a
  store created, deleted, replaced or re-pointed invalidates it; `lstat` and not `stat`, so a
  store that is itself a symlink is read as the link it is; and the parents need no stamp,
  because `lstat` traverses every component above the last — established by proof, since the
  guard written for a parents stamp could not be made to fail and the loop was dead code.
- Four guards in `tests/test_guard_cache.py`, all proven red-first. A fifth was written and
  DELETED rather than kept: "a store created after the first question is guarded" cannot fail,
  because the written spelling matches whether or not the cache is stale, and a test that cannot
  go red is not evidence.

## [0.598.0] — 2026-11-23

### Changed
- **Playbooks are chosen from a list you can read, not a `<select>`** (D307's first increment,
  his 2026-11-21 code-review order: playbooks should be reachable where a conversation starts and
  where it ends). The new-conversation screen mounts one card per playbook — title, its one-line
  `when`, its `varies: …` axis and its tags, all visible without a click, in the library's own
  order. Each card is a `<button>` with `aria-pressed`, so it is keyboard-reachable without a
  handler of ours; clicking selects it (the same value and the same `playbook` field the submit
  path already used), and a selected card offers "✕ start fresh instead" to undo. **With no
  playbooks in the library nothing is mounted at all** — not an empty box. New
  `static/components/playbook-picker.js` and `static/playbook.css` (the eighth stylesheet:
  `base.css` and `views.css` are frozen at their ratchet ceilings, and `static/index.html`
  states a new file as the house answer).
- Every render stamps `data-pb-count`, so a test for the ABSENCE of cards waits for the render
  that denies them instead of racing the `/api/playbooks` fetch — without it the empty-library
  case passed while never running the branch it was written for.
- `docs/playbooks.md`'s Reuse row and walkthrough name the new place.

## [0.597.0] — 2026-11-23

### Added
- **A run holding a root login has its calls risk-rated before they run.** Each routine and
  conversation has a **root command check** (`root_check`, *Limits & reach → Root command
  check*, the conversation header): `remote` — the `remote` util's exec/submit/push/pull to a
  root machine, the **default** — `any` — every tool call but the run's own steering kinds — or
  `off`. While a run holds a machine whose login has root rights, the engine asks the routine's
  decision model (its `decision` role, else the instance default) one yes/no question over the
  call and **the five calls before it** (in `remote` mode, the commands sent to that machine).
  Under P(risky) 0.5 the call runs untouched. At or over it, or with no verdict at all, a
  top-level run files a blocking **root command** approval (`command-approval`) and waits; a
  child or detached run cannot ask, so its call is refused (`engine/rootcheck.py`, called from
  `actionroute.dispatch_checked` for every action — background calls on their starting turn;
  an operator's slash command is not checked).
- **Every warning can change the setting.** Besides approve and decline it offers, for each
  other mode, *approve, then check … for this run* and *… from now on*. The engine applies the
  switch to the run (read back from the transcript, so a resumed leg keeps it); "from now on"
  is written to routine.yaml by the web at click time, like a forever access decision
  (`rsched/rootmodes.py`, `web/root_check_answer.py`). A routine-page edit is adopted live.
- **Who is root is asked of the box** (`rsched/machine_privilege.py`): `id -u; id -Gn` through
  the `remote` util — uid 0, or membership in sudo/admin/wheel/docker/lxd/root. The Settings
  **test** records it and a run probes a never-probed machine it holds once; the record is
  keyed to host+user+port. Never probed counts as root. Settings → Machines shows the answer,
  machine pickers mark root logins, and `ubuntuserver-root` (user `mark`) probes as root.
- **While the check is on, a root machine's private key reaches only a direct `remote` call**
  (`engine/exec_env._machine_env`); a script or a util that `calls: remote` gets the metadata
  only, so restricted `agent` logins (probed unprivileged) work as before.
- `root_check` is the operator's own switch: settings patterns do not govern it.

## [0.596.0] — 2026-11-23

### Fixed
- **The `report` bullet now names the one pairing `closes` requires** (F942). It said a reply
  without `closes` is "a new open report waiting for one more reply" and never that `closes` is
  valid only BESIDE `answers` or `settles` — so a run filing an originating notice set it and
  lost the turn to a schema refusal. That refusal is the fleet's most repeated live one: **8
  rejections across 5 routines on 4 separate days** (`deficit-coach`, `fau`, `feeld-android`,
  `scheduler-builder`, `self-audit` — twice in one run), every one a wasted turn on a contract
  the run had read. The refusal itself was already exemplary and tested; what was missing is
  that the always-on prose teaches the rule BEFORE a run earns it. The clause now names
  `` `answers`/`settles` `` and says a bare closure is refused, and a new guard in
  `tests/test_report_closes_refusal.py` pins it — red on the old wording, proven on a scratch
  checkout. Two wordier sentences in the same bullet paid for the addition, so the always-on
  contract **shrank by 2 characters** (`tests/prompt_budget.json` 58132 → 58130) instead of
  growing; `docs/prompt-anatomy.md` and its string pin follow the new text.

## [0.595.0] — 2026-11-23

### Added
- **A child run that silently got the builtin harness now files a health event** (F939,
  reported by `scheduler-builder` as R3665). `engine/childrun.materialize_to_disk` catches every
  failure from the library materializer, writes the builtin fallback body and returns a NOTE
  string — which reached the parent's observation line and nothing else: no health event, no
  ledger row, no counter. The child still runs, so a routine that asked for a specialised
  pattern gets the generic one and nothing fails. The builder's 15:08 child asked for
  `maintain-a-codebase`, ran on the fallback, and that was discoverable only by re-reading a
  one-line aside. Since three library patterns do not parse right now (the D241 migration's
  prose-in-import-list damage: `converse.py:25`, `maintain-a-codebase.py:51`,
  `steward-a-project.py:55`), every spawn naming one degrades fleet-wide and silently for as
  long as the library stays broken. `child_recipe_unavailable` now records who asked, which
  pattern, and the exception — the same shape as `wizard_build_degraded`. The materializer
  takes the parent's slug and run id as optional keywords, so the detached task builder's call
  is unchanged.

## [0.594.0] — 2026-11-23

### Fixed
- **A slow request's phase attribution named the one span that could be proven not to hold the
  wait** (F938, a follow-on defect in F906's own instrument). Every durable phase row ever
  written — 16 of 16 — read `mostly pre (pre 0.0–0.2ms)`, including an 11,630 ms
  `GET /api/questions` whose self-sample shows the handler itself inside
  `decisions_read._all_questions`. The row is composed in `web/slowsample.py`'s `slow_requests`,
  a `BaseHTTPMiddleware` whose `call_next` returns at `http.response.start`: `app_out` and
  `last_byte` are therefore unstamped at composition, `phases_of` computed `app_ms` only from
  `app_out`, and `dominant()` named `pre` because it was the sole known span. Three changes:
  the app's span falls back to `app_in → first_byte`, which the composer can actually see;
  `dominant()` takes the elapsed time the caller measured and withholds the word when the
  largest span explains under half of it (`ATTRIBUTION_FLOOR`); and the durable row keeps its
  spans — plus `first_byte_ms`, the one stamp every real request has — when the word is
  withheld, so an unattributed row is falsifiable instead of reading like an unmeasured one.
  The existing guards all drove the two phase layers alone, so they passed over a configuration
  that cannot occur in the process; the new one wires `slow_requests` outside them as `web/app.py`
  does.

## [0.593.0] — 2026-11-22

### Changed
- **The browser suite runs FOUR workers again, not two** (operator, re-measured; reverses D238's
  cut). Measured inside the `uitest` container's 4 GiB ceiling: a 101-test slice ran
  195 s / 153 s / 120 s at 2 / 3 / 4 workers with the container peaking at 1.32 / 1.62 / 1.94 GB,
  and the WHOLE browser suite at four passed 821/821 in 15.1 min with no rerun, a 2.44 GB peak
  and no OOM kill — against 21.4 min at two in the previous green gate. D238's 4.8 GB peak came
  from a harness that held far more per worker. `memgate.NEED_MB["browser"]` rises to 2,560 MB
  to admit that peak. A browser selection's `-n auto` is no longer cut by the FAST suite's
  per-worker memory price (`browsersource.auto_workers`): at two that never bit, at four it
  would have quietly run three; the browser stage's memory is admitted by its own budget in its
  own cgroup. Probed and dropped: serving `/static` from memory in the page saves ~0.15 s of a
  ~1.4 s page load (50 static requests; the time is the console's own JS and its API calls).

## [0.592.0] — 2026-11-22

### Fixed
- **`deploy/merge-to-main.py merge` no longer stops half-way when the candidate already carries
  its number.** The merge renumbers the candidate's top CHANGELOG header and `__version__` to the
  next minor above main and commits that with the gate-stamp trailer; a candidate that had
  written that very number changed no byte, `git commit` refused the empty commit, and the merge
  raised AFTER the rebase and BEFORE the fast-forward — 0.591.0 (the machine setup wizard) had
  to be fast-forwarded and pushed by hand. The commit is now made with `--allow-empty` and a
  subject that says which case it was ("assigns this candidate X" / "records the gate of this
  candidate, already X"), so the stamp is recorded either way. A new end-to-end test merges into
  a scratch repository with its own origin, for both cases; the already-numbered one fails
  against the old script with exactly "nothing to commit".

## [0.591.0] — 2026-11-22

### Added
- **Settings → Machines has a setup WIZARD** (operator request in conversation, no finding: setting
  up a machine meant generating a key in a terminal, appending it on the box, pasting the private
  half into Secrets and deleting temp files). *+ set up a machine* opens a DIALOG that shows one
  short step at a time — machine → login account → install the key → done — and the daemon does
  every step it can: it pins the host key, generates an ed25519 login key straight into Secrets
  (`<NAME>_SSH_KEY`, never shown), and installs it over a ONE-TIME PASSWORD login
  (`machine_bootstrap.py`: system `ssh` with the pinned host key checked strictly and the password
  passed through an askpass helper's environment, never argv or disk; `sudo -S` for the
  restricted-user setup) or, on request, as ONE copyable line, after which the dialog re-tests
  every few seconds until the key lands. Both shapes are built in `machine_access.py`; the
  restricted-user line carries `deploy/setup-remote-agent-user.sh` inline as base64. Every
  installed key turns forwarding off. Choosing an existing account warns that an admin account
  acts with full rights. Each catalog row gains *set up access* (the dialog from the key step,
  reusing an existing readable key, never rotating one); the raw form stays as *add or edit by
  hand*. Routes: `web/settings/machine_access.py` (`key`, `install-command`, `install`).

### Changed
- **`setup-remote-agent-user.sh` appends a key instead of replacing the file**, so two catalog
  machines can log in as the same restricted user with keys of their own; it also runs when piped
  to `bash -c` (no `BASH_SOURCE`), which is how the wizard ships it.

## [0.590.0] — 2026-11-22

### Changed
- **A branch's hand-back lands as a CARD, not as the operator's own words** (D306 points 3 and
  5, the first increment of his 2026-11-21 code-review order). `branches.hand_back` filed an
  inbox message with `via="branch"` and the chat's `user_injection` case ignored `via`, so a
  parent conversation rendered its branch's result as if the person had typed it. New
  `static/components/handback-card.js` renders it as "branch “<name>” handed back" — linking the
  branch, showing the summary, and offering each landed file under `artifacts/from-branch-<slug>/`
  as a chip that opens the artifacts panel. It is ONE renderer keyed on `via`, with `background`
  in the same table, because the detach order reuses this shape; `daemon/detached_delivery` files
  the identical record. **The paths ride the record, never the prose**: `extra={"handback": …}`
  is carried by `engine/inbox._drain_locked` and by both `user_injection` writers
  (`engine/control`, `engine/boot`), the way the order specified. `artifacts.js` gained
  `openPath` because the file route needs the console's auth header, so a card link cannot be a
  plain anchor.
- **The conversation lineage read is paid only when there IS a lineage.** `branchControls` called
  `GET /conversations/{slug}/lineage` on every conversation view, and that route reads every
  conversation's `routine.yaml` to find children. The list row now carries the conversation's own
  `parent`, `branches.familyOf` derives parent-and-children from rows already loaded, and the
  fetch happens only when a family holds — with a signature memo so the list's 20 s reload cannot
  turn one read into a poll.

### Fixed
- **`RoutineConfig` dropped a conversation's `parent` and `lineage` keys.** The model is
  `extra="ignore"` and neither field was declared, so the keys a fork writes into `routine.yaml`
  never reached the catalog — `_parent_record` had to re-read the file itself. Both are now
  declared (following the `owner` precedent) and are therefore in `TOP_KEYS`, so neither reads as
  an unknown key. Paying for it line-neutrally moved the two run-gate models to
  `config/rungate.py`, re-exported under the names callers already import.


## [0.589.0] — 2026-11-22

### Added
- **The engine image carries a hypervisor** (D309, the operator's answer (a)). `Dockerfile`
  installs the pinned cloud-hypervisor v53.0 static binary and `ch-remote` from the project's
  own GitHub release, verified against the same sha256 pair `deploy/Dockerfile.desktop` already
  uses — one hypervisor across the deployment, bumped in both files together. D170 phase 1 (per-
  call micro-VMs in place of the Landlock jail) has to be MEASURED in the container runs
  actually execute in, and that image shipped none, so the measurement had nowhere to run.
  `/dev/kvm` has reached the service since 0.571.0; this is the other half. The install ends in
  `cloud-hypervisor --version`, so a truncated or wrong-platform binary fails the BUILD rather
  than the first micro-VM call inside a run. It does NOT provide a bootable guest: a kernel and
  a root filesystem are phase 1's own work. **A daemon restart does not deliver this** — the
  image needs `docker compose build rsched && up -d`, which the operator has said he will run.
- `tests/test_deploy_image.py` holds the hypervisor pin and its checksums EQUAL across every
  image that installs it, holds a downloaded hypervisor to being checksum-verified and (engine
  image) actually run, and records — as a passing test that reds when somebody closes it — that
  `deploy/Dockerfile.desktop` verifies its download and never runs it. D309 authorized the
  engine image only, so the gap is written where the next reader of these pins will see it.

### Fixed
- **The merge step resolved the wrong checkout** (D303, shipped one release ago, caught on its
  first real use). The script lives in `deploy/`, so a candidate's worktree holds a copy and
  that copy is what a run calls — `parents[1]` therefore resolved to the WORKTREE, which gave
  three wrong answers at once: the next version was read from the candidate's own CHANGELOG (so
  0.588.0's own release was offered 0.589.0 while main had shipped 0.587.0), the gate evidence
  was looked for under a path only the main checkout has, and `merge --ff-only` would have run
  in a tree that already had the candidate branch checked out — merging nothing while the push
  reported success. `main_worktree` now derives the main checkout from `git rev-parse
  --git-common-dir`, the one directory every worktree shares, and main's shipped version is read
  from `main:CHANGELOG.md` out of the ref rather than off any working tree (a session
  mid-release has already edited that file, and its edit is not something main has shipped).
  Two refusals make the rest impossible: a candidate tree that IS the main checkout, and a main
  checkout parked on another branch. 0.588.0 was therefore landed by hand, not by its own step.

## [0.588.0] — 2026-11-22

### Added
- **One merge path to `main`, where the MERGE assigns the version** (D303). The gate lock
  serializes the test runs and nothing serialized the merges, so whoever went green second
  found main moved: on 2026-11-21 sixteen releases landed in one day and one builder release was
  overtaken four times, renumbered twice and re-gated three times, none of the overtaking
  commits touching a file of its own. `deploy/merge-to-main.py` (stdlib only, so the gate
  script, a routine's close-out and a person at a terminal can all run it) rebases, renumbers
  the candidate's top CHANGELOG header and `__version__` to the next minor above main's own
  newest header, ff-merges, pushes and reads `origin` back by commit AND version — one merger at
  a time behind `/tmp/rsched-merge.lock`, deliberately not the gate lock, so a candidate under
  test never blocks one ready to land. The re-gate owed after a rebase is CLASSIFIED rather than
  judged: prose owes nothing, leaf test modules owe themselves plus the quality stage, and
  anything that reaches every test (`src/`, `static/`, a conftest, a shared helper, the config)
  owes a full re-gate — with the class and the covering stamp written into the merge commit's
  trailer. A stamp is accepted only when its own result says green over THIS tree.
- **The hook stream answers its two questions on a page** (D305, first increment).
  `.control/hooks.jsonl` was written for every one-shot, injection and run-tree hook and no
  surface read any of it. `readmodels/hook_activity.py` folds the stream ONCE (memoized on its
  stat fingerprint): `next_fires` lists the still-pending one-shots soonest first — the PENDING
  fold first and the ordering second, because a hook's lifecycle is a later row and over raw rows
  every fired one-shot reads as overdue — and `deliveries` lists who spoke into which run,
  newest first with the stream's own position breaking a same-second tie. `GET /api/hook-activity`
  serves both to the operator's token, and the Routines page shows them under the week strip; a
  run-tree row whose write failed reads as undelivered.
- **A slow request now says WHERE its seconds went** (F906). `/api/health/blocked` records a
  2,780 ms median over 834 samples while its whole server pipeline measures ~30 ms warm, so the
  wait was in neither the handler nor the read model. `web/reqphases.py` adds two pure-ASGI
  layers that bracket every middleware and split one request into `pre_ms` (loop scheduling, the
  pool handoff, everything above the app), `app_ms` and `send_ms`, with `first_byte_ms`
  separating a slow encode from a slow write. The slow row, the log line and the DURABLE trace
  row carry all four plus which span dominates. Both layers fail open and read no header, cookie
  or query string. This is the attribution; the causal fix follows the rows it produces.

## [0.587.0] — 2026-11-22

### Added
- **The seven classes of thing that might need the operator lead a run's page, and the health
  stream is read once for all of them.** D289/D304: `readmodels/attention.classify_run` has
  returned his classes (a)…(g) since 0.547.0 and nothing called it — its own docstring said the
  renderer was the next increment. Three things now exist. `attention_for_runs(home, run_dirs)`
  reads `.control/health-events.jsonl` ONCE and groups it by `run_id` before classifying, where
  `classify_run` opened the whole stream per run (`_events` takes pre-grouped rows now), and the
  fold is memoized through `readmodels/memo` on a stat fingerprint of the stream plus every
  run's `status.json`, single flight, because the surfaces that will fold many runs refresh on
  `run_*` bus events. `GET /api/runs/{run_id}` carries an `attention` list for that run.
  `static/components/attention-block.js` renders it as a labelled block at the head of the run
  page's narrative column — one line per row: his class label, the row's `text`, `×N` for a
  repeated event, and the row's own `detail` in the reading face below it, because his
  requirement on this block was that a row be self-contained (*"worst case scenario is content
  i need to know about being referenced only by an identifier"*). The order is the server's and
  is never re-sorted client-side. An empty list renders NOTHING AT ALL, so a run with nothing
  for him reads exactly as it did before.
- A sixth stylesheet, `static/attention.css`: `static/views.css` was at its ratchet ceiling, and
  `static/index.html` states a new file as the house answer to exactly that. `static/views/run.js`
  was at its ceiling too, so the mount was paid for by moving `waitingLabel` out to
  `static/components/waiting-label.js` — a pure function, an action payload in and a sentence
  out — and the ceiling was re-tightened 583 → 578 rather than raised.
- Tests: `tests/test_attention_fold.py` pins that N runs cost ONE stream read and that the fold's
  rows are identical to `classify_run`'s, and `tests/ui/test_attention_block.py` drives the real
  page in chromium for a run with a met goal, a partial finish and a pending ask, and for a run
  with nothing (no block in the DOM at all). Both fold guards and three of the four browser cases
  were proven red-first; the fourth is the negative case, which correctly stays green.

## [0.586.0] — 2026-11-22

### Fixed
- **One answer to a report is one ledger row, and the target is told once.** The answer route
  serves a question's record until a RUN consumes it, so the same question can be answered more
  than once — a second click, a reload that re-posts — and every post relayed. Both report-born
  answers this ledger has ever carried were therefore recorded twice: R3650 two seconds apart
  and R3657 eight, identical text, while each question's own record held a single answer. A
  triage pass reading `.control/reports.jsonl` saw two decisions where the operator made one
  and had to tell a duplicate from a revision by hand. `web/report_answers.record` now compares
  the incoming answer with the ledger's LATEST word on that report under the append's own lock
  and writes nothing when they match, and `relay` skips the target's inbox message for such a
  repeat — the stem-keyed idempotency there only held while the message was still queued, so
  after the target's run had drained it a repeat delivered the same decision again. A genuine
  CORRECTION is a new fact and still goes out, including one that returns to an earlier
  wording, and an unreadable ledger fails OPEN: telling the target twice is a cost, losing his
  decision is not.

## [0.585.0] — 2026-11-22

### Fixed
- **A run gate's own `scripts/admit.py` can import the module beside it again.** The daemon ran
  the custom admission predicate as `python -I`, and `-I` implies `-P`: the script's own
  directory never reached `sys.path`, so a sibling import raised `ModuleNotFoundError` and the
  fire died before turn 0 as the undiagnosable "Run gate failed: gate exited rc=1".
  `uncensored-model-radar` was dark on every fire its declarative checks did not admit first
  from 2026-11-18 (when `from radar import …` was added) — two `run_failed` rows, one of them
  stopping a whole lane chain; `rules-review`'s gate survived only by hand-writing its own
  `sys.path.insert`. It is `-E -s` now: the same environment and user-site isolation without
  the implied `-P`, which is what `scriptenv` already does for the script ACTION, for this
  exact reason. The two doors into `scripts/` finally agree.
- **A library document that stops parsing is said out loud.** Nothing on the instance read the
  library linter, and the boot seeder restores only a MISSING seed doc — so a CORRUPTED one
  stayed corrupted in silence. On 2026-11-20 18:56 the D241 boot migration rewrote the word
  `subtask` into the prose "sequential child run" inside three workflow patterns' Python import
  lists; `converse.py` is the one every conversation is materialized from, so
  `POST /api/conversations` answered a bare `500` from then on (the operator hit it twice at
  16:52 and 16:53 on 2026-11-21) and no row anywhere said why. `daemon/library_watch` now lints
  the library when HEAD moves and files the new `library_doc_unreadable` health event naming
  each unreadable file, latched in its own on-disk marker so a restart does not re-file it;
  style problems, which 13 live docs carry, are deliberately not reported. And the create
  endpoint no longer turns the parse failure into a bare 500: it names the pattern, the line
  and how to repair it.
- **The owed-work watch files one row per routine per day across restarts.** Its "once a day"
  guard lived on the watcher object, so every daemon restart re-filed the whole sweep: 13
  restarts on 2026-11-21 turned four standing obligations into 56 health rows. Over the live
  stream, `obligation_overdue` had filed 424 rows for 101 routine-days — 4.2× — and a standing
  row's magnitude is its SPAN, so the copies measure daemon uptime, not owed work. The latch is
  read back off the stream now (`health_cooldown.filed_within`), where a restart cannot lose
  it, and `answer_undelivered` with it. `backup_stale` (2.5×) and `migration_due_soon` (4.0×)
  are inflated the same way but are NOT changed here: both have recovery semantics a rolling
  window silently breaks, which the gate caught and the new helper's docstring records.

## [0.584.0] — 2026-11-21

### Fixed
- **A slow request's self-sample names every request in flight with it.** The watcher pointed
  only the requests already over the floor at its snapshot, and its cooldown takes no second
  one — so a request that crossed a tick later, listed in the snapshot as waiting, kept no
  pointer to it on its slow row. Every request in flight at the snapshot is pointed at it now,
  and the pointers are set BEFORE the file is written (a request finishing in between lost its
  name), taken back if the write fails.
- **The browser and fast suites no longer flake.** Every test that needed a rerun in a gate log
  was repeated with the rerun shield stripped; the ones that failed all acted before the state
  they needed existed. The folder picker's tests typed a path while its opening listing was in
  flight (a `fill` landing mid-write-back appended the path to the daemon's home); the run-view
  layout test measured across a re-layout after a hash-only navigation; the auto-apply drain
  tests started the app's own drain loop beside the drain under test; the self-sample pools
  test read anyio's limiter from a bare thread and passed only after an earlier test captured
  one on a loop.

## [0.583.0] — 2026-11-21

### Changed
- **One name for a child run, finished: the transcript events are `child_start` / `child_end`**
  (D241's last increment; operator 2026-10-17: *"sub-runs / subtasks (why two terms for the same
  thing? fix that)"*). The five surfaces that fail SILENTLY on an unknown type moved together —
  `EVENT_TYPES` (which asserts on write), the console renderer (which drops an unrendered type),
  `cli_render`, CLAUDE.md's written-out list with its count, `docs/contracts.md` — plus every
  reader matching the names literally: the child-run tree, `engine/history` (a resumed leg's
  announcements and orphan set), `boot`, `subruns`, `promptsegtext`, `cost_profile`. The console
  labels follow: the rails' card is **children**, a row's address says "child run 2", the
  waiting line says "waiting on child runs…". `docs/designs.md`'s D241 entry goes with it.
- `src/rsched/migrate_child_events.py` (MIGRATION, expires 2026-12-31) renames the two events in
  every stored transcript, at every depth of every run tree, gzipped archives included — 266 on
  this instance, 184 of them gzipped. Unlike D241's earlier migrations it rewrites a RECORD,
  deliberately: an event `type` is the token those readers dispatch on, not a sentence about the
  past. It assigns a key on a PARSED row, so prose quoting `subrun_end` survives byte for byte.
- **The finish verifier cascades per routine** (D302(c), narrowing D287; operator: *"the six
  routines at 100% agreement get the screen, the three below 85% keep the prose judge for every
  claim"*). `engine/claim_screen.py` is the table; a screened routine whose claims ALL clear the
  threshold skips the prose call. The decision shadow retired earlier the same day is restored
  with it, on his instruction (*"the retirement was about an unused mechanism, and D302(c) is
  the operator asking for it to be USED"*), and keeps recording for every routine so the table
  has a live evidence source. Three rules the answer left open are written down: an unnamed
  routine keeps the judge; the threshold is 0.95, not the shadow's recording 0.5 (at which the
  screen passes 8 of the 11 claims the judge refuted); a minimum-pairs floor of 12. **Worth,
  measured:** the judge is ONE call per finish over all its claims, so over the window's 128
  calls the cascade skips 6 — 4.7%, zero missed. The 27% argued counts claims, not calls.
- **The playbook offer's composed sentence is asserted where it is composed** (R3645). The
  sentence is spliced from a console clause and a server fragment, so it exists in no file, and
  the guard written after 0.443.0 said "work" twice lived in the half that cannot see the other.
  A browser case reads the rendered line back — and found a live defect on its first run: the
  refine branch said "did real work — … — refine X with what this conversation did differently?".
  The fragment now reads "with what went differently here".

### Fixed
- **The restart recovery note READS its dead children off disk** (R3644; operator: *"make sure
  that resuming a run also includes its sub runs / children"*). It asserted "any CHILD RUN that
  was in flight did not survive" while the child's transcript sat unread in the same directory.
  `daemon/deadchildren.py` states one read line per child with turns and no finish: label, turn
  reached, that turn's `say`, the transcript path. His other half — no automatic daemon restart
  while routines run — is not in this release.
## [0.582.0] — 2026-11-21

### Fixed
- **The artifacts panel keeps keyboard focus when its list is redrawn.** The list is rebuilt
  whole — by every refresh (a finished run's view refreshes it twice on open: the panel's own
  first read, then the stream's terminal state; a live run once per deliverable written) and by
  opening a row to move the highlight. Each rebuild removed the button that held focus, which
  sent focus to the page body, so the next Tab started from the top of the page and Enter on a
  row lost the reader's place. `renderList` now finds the focused control again by its row's
  path and its own class and focuses its replacement. This was the cause of the intermittent
  `test_an_artifacts_delete_is_its_own_keyboard_stop` failure: it lost whenever the second
  refresh landed between its focus, its Tab and its check. A new browser test pins it through
  the open path, which redraws on every press:
  `test_a_rebuilt_list_keeps_the_keyboard_on_its_row`.

## [0.581.0] — 2026-11-21

### Fixed
- **`rsched census` sees the alias action kinds.** `websearch` and `code_search` (D290) are
  rewritten into the util call they spell before anything is recorded, so the census listed both
  as "never used" whatever the fleet wrote. The landed `assistant_action` event of an alias now
  carries `spelled: <alias kind>` beside `turn` and `phase` — an EVENT key, never in the payload,
  so the action schema, the prompt, the model's messages and a resumed leg's replay are
  unchanged. `completion.judge` (the one seam every first and chained action passes) wraps the
  normalized action in `kindaliases.Spelled`, a plain dict carrying the mark as an attribute, and
  `loop._land` records it — the mark travels with its own action, so every element of a chain
  is marked independently. The census counts the mark under the alias kind; `util` still counts
  every util call however it was spelled.
- **The census measures config keys where they live.** It read only the routines home, so
  `kind` and `playbook` (conversation configs) and `owner` (detached background tasks) always
  read as unused. It now reads every `routine.yaml` under the routines, conversations and
  background homes, and reports how many configs it read.
- **`dir`, `deliberation` and `tuning` are no longer routine.yaml keys.** They are RUNTIME
  fields the loader fills (the routine's dir, tuning.yaml's level and the whole of it), and no
  writer puts them in a routine.yaml — checked across the web, engine and config writers and the
  live homes. They are named once, `config.routine.RUNTIME_FIELDS`, and left out of `TOP_KEYS`:
  the census no longer lists them as unused keys, and the loader reports one found in a file as
  an unknown key ("the loader fills it, never this file") instead of accepting it silently.

## [0.580.0] — 2026-11-21

### Fixed
- **The daily owed-work check and the migration-marker scan no longer run on the event loop.**
  0.578.0's reordering of the read-model warmer did NOT shorten the restart burst — after it,
  `/api/questions` still took 31.5 s — and the slow-sample taken inside that burst showed why:
  four questions requests waiting 24-31 s, every AnyIO and I/O-pool worker IDLE, and the event
  loop itself inside `daemon/owed_watch.tick` → `proofcheck.sweep_routine`. The scheduler
  starts each upkeep watcher with `_off_tick`, which creates a task ON THE LOOP, and
  `OwedWatch.tick` called its synchronous `check` directly — every routine's obligations,
  questions and answered archive read and cards filed, a few hundred ms warm, stretched to half a
  minute at boot by the docs build, the post-deploy walk and the boot scans contending for the
  GIL beside it. `MarkerWatch.tick` had the same shape, walking every source file. Both run in a
  thread now (`asyncio.to_thread`), as the backup watch has since 0.573.0. A structural test
  (`tests/test_daily_checks_off_loop.py`) reads which watchers the scheduler starts with
  `_off_tick` and fails for any whose class never hands work to a thread — the one shape all
  three of today's loop-blocking watchers shared. The warmer's ordering stays: it is still the
  right order, it was just not the cause.

## [0.579.0] — 2026-11-21

A code review measured what the instance actually uses and removed what it does not
(~45k lines net), then added the checks that keep it from growing back.

### Removed
- The 16 converged boot migrations; a fresh library now gets the tracked `gu` dispatcher.
- Unused features: the report trigger and `/api/hooks` (webhooks stay), model trials,
  backgrounded writes (`background_order`), `autoswap`, the assist `action` payload,
  `visionsteer`, `decision_shadow`, `errorclass`, the replace-routine candidate, the ⚑ item
  priority, the `revise_confirm` dial, `rsched suggest`, the Ollama-native and OpenAI-decisions
  adapters, the conversation browser-session routes, `install.sh`, the pdoc API reference, and
  the unused seed patterns `model-trainer`, `radar` and `train-a-model`.
- Incident history in engine/endpoints docstrings and in test docstrings; CHANGELOG releases
  before 0.444.0 condensed to one line each; `docs/status-pages.md` cut to its retirement note.

### Changed
- CLAUDE.md is the working tier (≈260 lines); the long form is `docs/contracts.md` and
  `docs/testing.md`.
- Reports and reminders are read from the hook stream alone; the per-read store comparison is gone.
- One table of sealed paths feeds every write layer (`sealtable.py`); one source of side fields
  and a routing table for action kinds (`sidefieldset.py`).
- The four test memory-admission modules are one (`tests/memgate.py`).

### Added
- `ask_user` carries an optional `proof` per option (D220 wired end to end).
- A Cost regressions panel on Stats; `endpoint_watch` judges each subscription login behind
  the gateway and a dead one shows on the blocked-work surface.
- Runs are told when to set a note's `stale_after`.
- Anti-crud guards: a writing standard in CLAUDE.md, a shrink-only ratchet on dates/run ids in
  source prose, one-paragraph test docstrings, a 40-line cap on CHANGELOG entries, a 300-line cap
  on CLAUDE.md, and `rsched census` (what the instance uses and never uses).
- Builder orders in docs/designs.md: the D289 attention view, D152's replace step, the branch UI,
  playbook discoverability and detach.

## [0.578.0] — 2026-11-21

### Fixed
- **The read-model warmer no longer lengthens the restart burst.** 0.577.0's boot pass ran the
  moment the app started — beside the scheduler's boot scans and every reconnecting console tab
  — and computed the fleet-wide change fold (which spawns a `git` per change) before the
  Decisions catalog. After its restart `/api/questions` took 25-29 s, against 16-18 s for the
  whole burst at the restart before: the tabs' requests waited on a Decisions compute starved by
  everything beside it. The boot pass now waits for the scheduler's own boot (`Scheduler.booted`,
  set at the end of `_boot`; at most `BOOT_WAIT_MAX_S`, 120 s, so a boot that fails cannot hold
  it), warms the Decisions catalog first, and leaves the fold until `BOOT_SETTLE_S` (60 s) later,
  by the clock rather than by an idle event queue. Every event pass computes Decisions before
  the fold for the same reason.

## [0.577.0] — 2026-11-21

### Fixed
- **Cold read work leaves the event loop and the request path.** With the backup stamp and the
  files card fixed, the console's remaining slow routes answered in 3-100 ms when timed alone on
  a quiet box (`/api/questions` 40 ms, `/api/status` 40, `/api/conversations` 60,
  `/api/schedule/week` 52), so their slow rows were their COLD costs and the stalls those caused
  elsewhere. Timed cold in a fresh interpreter against the live store:
  - `registry.scan` took **3.3 s** for the routines home and 2.1 s for conversations, 2.9 s of it
    `load_routine` resolving fs roots on the NAS share (0.4-1.4 s per root, to report one that
    links into a credential store). The scheduler ran it ON THE EVENT LOOP — at boot for all three
    homes, and every `registry_rescan_s` — so a cold scan held every request and every fire. It
    now runs in a thread at boot (each home warmed before the loop-side orphan reap reads it) and
    on every periodic rescan.
  - Four request paths re-loaded `routine.yaml` from scratch on every call, paying those NAS
    resolutions each time: `servable.granted_roots` (the files card, twice per poll, and
    `/file`), `/api/runs/{id}`, `/api/changes/{slug}/outcome` and the refusal-flag helper. They
    now read the registry's memo, public as `registry.load_config` (re-read only when
    `routine.yaml`, `tuning.yaml` or `main.md` changes; the roots the loader reports are the
    config's own, so nothing a guard decides is cached).
  - `change_effects.changes` took **4.4 s** cold — a fleet-wide fold the routine page's one
    development line (`/api/changes/{slug}/summary`) computed in full to report one routine
    (4.3 s for a 131-byte answer on the first page open after a run) — and the Decisions catalog
    **1.1 s**. The daemon now recomputes both in a thread after every burst of run or question
    events, and once at boot (`web/readmodel_warm.py`, shaped like the util-stats watcher), so a
    request finds them warm. A request still computes on a miss; this only moves the usual miss.
  `/manifest.webmanifest` (a static file, 3 ms) and the four routes above needed no change of
  their own: alone they are fast, and 80-100% of their slow rows had other work in flight.

## [0.576.0] — 2026-11-21

### Fixed
- **The files card no longer stats a network share inside the request.** `GET
  /api/runs/{id}/files` was the console's slowest route by server time — 1,070 rows over 2 s in
  13 days, median 6.0 s, worst 87.6 s — and timed against the runs behind most of those rows,
  every second was the D172 chip predicate resolving and statting rows on the NAS sshfs share:
  60.9 s for a 318-row card, 73.7 s for a 2,056-row one, up to 5 s for a single row, with F896's
  16-thread pool barely helping because the share answers one request at a time. Rows on a
  network or FUSE mount (decided lexically from `/proc/self/mounts`, `web/netmounts.py`, with no
  filesystem call) are now judged by a background job per viewed run, which applies the same
  predicate and KEEPS each verdict with its time (`web/fileverdicts.py`): served without a
  re-check for 5 minutes, refreshed while someone looks, and never served past 15. A row with no
  current verdict is `servable: null`, the response says `pending` while a job is checking, and
  the card renders no click for it and fetches again until it lands — an unknown row is never
  offered as servable (the card now shows buttons only for `servable: true`). Local rows are
  still judged on every request, which is what F896's freshness tests pin, and resolving the
  roots for them skips roots on a share (`servable.roots_for_run(skip=…)`). The route that
  SERVES a file is unchanged: it still resolves everything at serve time, so a revoked grant
  stops serving at once and only the offer of a click on a share row can lag. Also,
  `file_activity` is now memoized shared rather than deep-copied per poll (16 ms on the
  2,056-row card); both readers only read it.

## [0.575.0] — 2026-11-21

### Fixed
- **The tables D241 missed: a retired child-run kind is now named nowhere that decides by kind.**
  0.566.0 renamed `spawn`/`subtask`/`subruns` to `child` (with a `mode`) and `children`, and
  several places still keyed on the old names:
  - `engine/loopshape._WAITING` said `subruns`, so a run polling `children` between reads — the
    same answer each time, because nothing had finished yet — was warned it was stuck and filed
    as an `action_cycle` wrong turn. It now says `children`.
  - `engine/renamedkinds.RENAMED` had no entry for any of the three, so a recipe the boot
    migration had not reached got the blank enum refusal that module exists to prevent (the F637
    shape). All three are there now, and an entry can name a FIELD the old name implied: a
    `subtask` is told to resend as `child` **with `mode='sequential'`**, because "every other
    field unchanged" alone would have turned it into a parallel child and refused its `turns`.
  - Eight run-facing sentences still told a run to use a kind the schema refuses: the `kill`
    refusal and the dead-child salvage refusal (`` `subruns` lists them ``), the `detach`
    refusal, the `child:` report-target refusal, the `list_models` note, the `background` field
    description and the deliberation paragraph (both on the wire every turn — the contract
    shrinks slightly), and the unknown-slash-command error. Each now names `child`/`children`.
  - The console: `components/obs-text.js` rendered every `child` and `children` observation as a
    raw JSON dump (its branches still keyed on `spawn`/`subruns`); it now says which child started
    and in which MODE, and lists the children table. The run page's "waiting on sub-runs…" label
    looked for `spawn`/`subtask` and now looks for `child`.
- **A regression floor for the whole class** (`tests/test_retired_kinds.py`): `RENAMED` is the
  register of retired kinds, so it is also the vocabulary refused in every literal kind table,
  dict key and `kind ==` comparison under `src/rsched` and in every `static/**.js` kind
  comparison and kind array. A future rename that adds its entry gets its forgotten tables found
  with no list to keep in step. Floors on the scan's machinery (196 tables, 104 JS comparisons
  measured) keep it from passing vacuously. It also proves each correction is a promise kept:
  following a hint's kind and fields yields an action the schema and `validate_action` accept.
- Docs swept to the D241 vocabulary: CLAUDE.md's kind list (now the real 35), `engine/child.py`'s
  docstring (which still said the modes keep their own kind names), docs/child-runs.md (its
  example was a `"kind": "subtask"` action), background-tasks, architecture, endpoints, examples,
  conversations, curated-rules, rules-permissions, prompt-anatomy and the README. The hook
  MOMENT called `spawn` keeps its name — it is a moment, not an action kind.

## [0.574.0] — 2026-11-21

### Fixed
- **The overhead reading no longer deep-copies the fleet's whole run history on every call.**
  `readmodels/overhead_reading.reading` wants one routine's slice of a fold over every measured
  run in the fleet — 907 runs over 35 routines on 2026-11-21 — and `/api/health/blocked` asks it
  once per `process_overhead_high` row to decide whether that row still holds. The fold was
  memoized through `memo.memoized`, which hands every caller a DEEP COPY: 2.6-4.1 ms of copying
  under the GIL per row per request (five rows today), growing with every measured run, and one
  of the route's slow-samples caught the request thread inside `copy._deepcopy_dict` there. The
  fold is now `memo.memoized_shared` — the cached value itself, read-only by contract — and
  `reading` only filters it into fresh dicts, so a caller still owns what it gets back. The
  setup surface, the run-end overhead alarm and the boot digest read through the same function
  and get the same saving. Tests pin both halves: a warm reading copies nothing, and no reading
  writes into the shared fold.

## [0.573.0] — 2026-11-21

### Fixed
- **`/api/health/blocked` no longer reads the backup share inside the request.** It was the
  console's slowest request — about 8,020 client-side slow traces in 14 days and about 487
  server-side rows over 2 s in 30. The slow-samples named the cause: in 14 of the 20 retained,
  all of them this route, the request thread sat in `backup_watch.newest_stamp`'s
  `open`/`read_text` on the off-box stamp over sshfs, because `web/operator_conditions` asked
  `backup_watch.staleness()` afresh on every call to decide whether a `backup_stale` row still
  holds. The daemon's own `BackupWatch` now does that read once every `CHECK_EVERY_S` (60 s), in
  a worker thread, and KEEPS the verdict; the request reads it from memory
  (`BackupWatch.current`). The rule that an unreadable state is UNKNOWN and never fresh holds
  for the kept verdict too: before the first check, after a check that raised, and once the
  verdict is older than `VERDICT_MAX_AGE_S` (300 s — what a wedged share looks like, since
  `_off_tick` starts no second check beside a stuck one), `current()` is `None`, and the row
  keeps counting. A by-product: the watch used to call `staleness()` ON the event loop at every
  5-second tick, so each sshfs read also held every request and fire behind it; it now runs off
  the loop and twelve times less often. `rsched postdeploy` still reads the stamps itself — it is
  a separate process and that read is its job.

## [0.572.0] — 2026-11-21

### Added
- **The REVIEW LEG — Hermes Agent's learning loop, fitted to a scheduled run**
  (`engine/reviewleg.py`, `engine/reviewnote.py`). A depth-0 routine run whose own record gave it
  a reason — two or more failed actions, an action a caution held, refused replies, a repeat or
  cycle warning, a person writing in mid-run, a `partial` or authored `failed` ending, or notes
  no run has read in 20 runs — and that kept no note of its own now looks back ONCE inside its
  close-out, after its authored finish and before the finish event and the autocommit: on the
  SAME message list and the same schema (so the provider serves the whole prefix from cache; the
  narrow allowlist is enforced at validation), for at most 6 turns of `memory_read`,
  `memory_write`, `read_file` and `finish`. What it keeps lands in `.memory/` and is committed
  with the run; a `RECIPE REVISION:` line it names joins the run's one D200 card. Two guards
  Hermes measured it needed: an existing note is revised only after the leg read it, and a note
  is deleted only to FOLD it into one the leg wrote. The keeping standard is Hermes' own, tuned
  there against measured failures (only what the run showed; a general rule with one clause of
  why, no dates or ids; correct in place; never a negative tool claim from one failure; never an
  unresolved failure written up as a method) — but NOT its "a pass that does nothing is a missed
  opportunity": here "nothing to keep" is a complete answer. The leg's span is left out of the
  task's overhead and frictions (`frictions.in_task`), recorded as `quality.review`, and a leg
  that fails costs the run nothing but the leg. `ServerConfig.review_leg` switches it
  (config.yaml, on by default).
- **Search a routine's past runs** (`engine/runsearch.py`): `memory_read` with `args` (words)
  instead of a `name` searches every earlier run's transcript through a SQLite FTS5 index in
  `runs/.recall.sqlite` — BM25, up to three hits from different runs, the best one with its
  neighbouring records, the searching run excluded; ALL words first, then ANY. Standard library
  only, no model call (Hermes' `session_search` is the model).
- **Which notes runs actually READ** is counted per note in `.memory/.reads.json`
  (`engine/notereads.py`, engine-owned beside `INDEX.md`). A note unread for 20 runs is named to
  the review leg, which may fold it into the note that covers it; nothing deletes a note for
  being unread.
- **Two stuck-run shapes the identical-action streak cannot see** (`engine/loopshape.py`): a
  CYCLE of two to four actions with unchanged results (never a waiting kind) and THREE DIFFERENT
  calls of one kind failing in a row (`shell` exempt). Each is a warning tail once per onset and a
  `wrong_turn_detected` row (`action_cycle`, `varied_failures`) — a measurement, never an ended
  run. The same module stands in for a large result (512+ characters) byte-identical to one
  still verbatim in the live context: the observation is marked `same_as_turn` before it is
  recorded and renders as one line, live and on replay alike (`engine/samestub.py`); the record
  keeps the full result.
- **A `shell` exit 0 says whose status it is** when the command line hides it
  (`engine/exitattr.py`): `pytest | tail` reports `tail`'s, `make || true` reports nothing about
  `make`, `pytest; echo done` reports `echo`'s. Nothing is said for a non-zero exit or once the
  command sets `pipefail`.

### Changed
- **A landed `memory_write` ends on "Saved — this write is complete; do not repeat it."**, and
  the D200 recipe-revision ask asks for "the change a general rule with its reason, not this
  run's incident and no dates". The memory kind bullet names the past-run search (+28 chars on
  the always-on contract, recorded in `tests/prompt_budget.json`).

## [0.571.0] — 2026-11-21

### Added
- **A host can give its runs KVM** (D301(b), unblocking D170 phase 1). The per-call micro-VM
  measurement D170's answer named could not start: `/dev/kvm` was absent from the engine
  container. `deploy/docker-entrypoint.sh` now names the device's HOST gid (which names nothing in
  the image) and adds `mark` to it before dropping root — the desktop entrypoint's own fix — so the
  daemon and every run it spawns can open the device; `/dev` is already read-write in the Landlock
  jail. The device itself is mapped in the HOST's `docker-compose.override.yml`, not the tracked
  compose file: a `devices:` entry for a device a host lacks stops the container from starting,
  and Docker gives up after one failed start. `deploy/DOCKER.md` "KVM for runs" says how; a test
  pins the stanza and keeps the tracked file free of the mapping. No hypervisor ships in the image:
  choosing one is the measurement's own first step.

## [0.570.0] — 2026-11-21

### Fixed
- **An operator answer to a report-born question now reaches the reports ledger — routed or
  not.** `web/api_questions._relay_report_answer` told the report's TARGET what was decided
  (F621) and told the ledger nothing, returning early when there was no target at all. So an
  UNROUTED report — one waiting in triage for a routine to pick it up — kept reading as
  undecided work, and the one fact that decides its disposition reached no reader of reports.
  Measured: R3630 was answered "Both" on 2026-11-20 at 11:58 and
  `gu rsched reports --triage --status open` still listed it as unanswered the next day,
  whereupon the triaging run forwarded it offering three options the operator had already
  chosen between (corrected in R3639 the same run). The answer is now a ledger `answered`
  event, deliberately NOT terminal (`reportproj.TERMINAL_EVENTS`): a decided report is not a
  disposed-of one — unrouted work still needs an owner, routed work still needs building — so
  the row stays on the open front and now says what was decided. The relay moved to the new
  `web/report_answers`, which also brought `api_questions.py` from 392 lines to 373 and
  tightened its size-ratchet ceiling.

## [0.569.0] — 2026-11-21

### Fixed
- **uv may install a managed Python again: the toolchain root defaults under `~/.cache/uv`** —
  R3631 reported that no routine can obtain a second interpreter. uv puts a managed interpreter
  in the XDG data dir, and `/home/<user>/.local/share` is root-owned here, so `mkdir` fails
  before Landlock is consulted; `sandbox._HOME_RW` does list `.local/share/uv`, but `landlock`
  skips a rule path that does not exist and the toolchain list — unlike a routine's granted write
  roots, which `_ensure_write_roots` creates for exactly this reason (R244/F293) — has no
  bootstrap. `~/.cache` is owned by the daemon user, is already a jail-RW toolchain root, and uv
  creates a missing install dir itself. New `rsched.uvtoolchain` fills in
  `UV_PYTHON_INSTALL_DIR` at both env composition points — `utils_verbs.child_env` (every util)
  and `scriptenv.ensure_env` (the per-routine scripts venv). It is a DEFAULT: a value already in
  the environment wins. Measured on this host: `uv venv --python 3.10` downloaded CPython
  3.10.21 and produced a working interpreter, where it had failed on the XDG path.
- **A finish `accounting` written as ONE string is the list it spells** — the shape that refused
  19 turns across 12 routines (2026-10-25..11-17), newly visible because 0.568.0 stopped the
  wrong-turn fold filing each one as its own singleton cause. 9 of the 19 wrote the entries
  newline-separated, 6 wrote a single entry, 2 wrote the list's own JSON. `engine/fieldshapes`
  now coerces it at the normalize seam, where six other evidently-intended shapes are already
  repaired. The repair is lossless by construction: `accounting.parse` judges every part, and
  the field is coerced only when EACH part is a complete `<id> <verdict>: <account>` (or
  `b<n> waits on <who>:`) entry on its own. A string whose parts are not whole entries — an
  account clause spanning lines, prose around the entries — stays refused rather than split at
  an invented boundary, and an empty field still never reaches the repair, so
  `accounting.arrived` keeps the "no field" / "a field saying nothing" distinction F805 added.

All notable changes to **routine-scheduler** (`rsched`) are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

**Versioning conventions**
- The single source of truth is `__version__` in `src/rsched/__init__.py` (pyproject reads
  it via hatch). `/api/status` pairs it with the running checkout's git commit stamp so a
  deploy is always identifiable.
- **Bump the minor on every user-facing revision**; the patch for isolated bug/regression
  fixes. Each code-changing commit that ships a revision should bump `__version__`, tag the
  version in its commit subject `(x.y.z)`, and add an entry here.
- Dates are UTC. The project has a fast, single-author cadence (many commits per day), so
  entries group related work rather than list every commit.

## [0.568.0] — 2026-11-21

### Fixed
- **The wrong-turn fold counted truncations, not mistakes.** The recorder keeps the
  validator's message as `str(exc)[:200]` (`engine/completion.py`), so a long rejection
  arrives cut — 109 of the 333 rows in the 14-day window of 2026-11-21, 83 of them ending
  inside an unterminated quote. `readmodels/wrong_turns.cause()` masked only values with
  their CLOSING delimiter, so every cut detail carried its own value into the cause and one
  mistake became one row per occurrence. `accounting: '…'` — a list field given a string —
  was 18 turns across 12 routines filed as 18 singletons, and `paths: {…}` 12 turns across 5
  as 10; neither appeared anywhere in the answer, because both sat under `MAX_ROWS = 40`,
  the bound that makes the fold readable. The mask now also matches a quote, bracket or brace
  that the cut left open, anchored at the END of the string so a balanced detail is still
  matched by the paired branch first and comes out exactly as before. Over that same window:
  126 distinct causes become 87, 87 singleton rows become 44, and `remind.description is N
  characters` is one row of 16 occurrences newest 2026-11-20 instead of 9 newest 2026-11-16 —
  the class the fold exists to surface, previously understated by 44% with a stamp four days
  stale. All 215 balanced, untruncated details in the window mask identically before and
  after, which is what the new guard node asserts.

## [0.567.0] — 2026-11-20

### Changed
- **The child-run budget keys follow the kind: `max_subruns` / `max_subrun_depth` become
  `max_child_runs` / `max_child_depth`** (D241 increment 2). Renamed, not aliased — an alias is
  how two names for one thing survive indefinitely, which is the subject of the whole decision.
  `DEFAULT_BUDGETS`, the `Budgets` dataclass, the harness contract's own budget sentence, the
  ladder's depth check, the conversation config, the routine and conversation API surfaces, the
  four console surfaces that render a budget field, and all 14 settings patterns move together.
- `src/rsched/migrate_child_budgets.py` (MIGRATION, expires 2026-12-31) renames both keys in
  every routine's, conversation's and background task's own `routine.yaml`. **Not optional, and
  not tidiness:** `config/routine_scrub` DROPS a budget key it does not know, with a problem
  line and no error — so a config left saying `max_subruns: 2` keeps validating while its run
  silently takes the DEFAULT of 8. A ceiling the operator deliberately lowered would quadruple
  with nothing but a loader warning to show for it, which is worse than a failure because
  nothing surfaces it. The rewrite is TEXTUAL on a `key:` line, deliberately: a routine.yaml is
  the operator's own document and a YAML load/dump round trip would discard every comment in it.
  A mention of the old name inside a comment is therefore left readable, and the migration
  record names every file it changed.
  It does NOT touch a routine's `state/`, its ledger or any transcript — those are the RECORD,
  and a run that really was capped at `max_subruns: 2` was.

## [0.566.0] — 2026-11-20

### Changed
- **One name for a child run: the `child` kind with a `mode`, and `spawn`, `subtask` and
  `subruns` are gone** (D241, operator in conversation 2026-10-17: *"sub-runs / subtasks (why
  two terms for the same thing? fix that)"*). `engine/child.py` has always defined ONE concept
  with several scheduling modes; the vocabulary never followed. The same thing was a child run
  in the engine and the docs, a `subrun` in the console, a `subtask` when scheduled
  sequentially and a `spawn` when scheduled in parallel — and `static/`'s transcript used
  "subtask" for sequential children and "subrun" for parallel ones, so one word meant both "any
  child" and "a parallel child" depending on which surface you read. Reading `max_subruns`
  beside a recipe's `subtask`, two mechanisms is the reasonable inference. There is one.
  - `child` takes a flat `mode` (`parallel` — the default — or `sequential`), the vocabulary
    `engine/child.py` already owned; `subruns` becomes `children`. The three old kinds are
    REMOVED, not aliased: an alias is how two names for one thing survive indefinitely.
  - `mode` is now shared with `ask_user`, as `question` and `options` already are — one
    JSON-schema property cannot carry two enums, so the union and the reasoning live in
    `engine/childfields.py` and each kind's own validator refuses the other's values. `turns`
    on a PARALLEL child is corrected rather than ignored, because a budget the engine discards
    reads to the caller as a budget it set.
  - The console's transcript marks the mode with its ARROW rather than with a second noun
    (`→` sequential, `↳`/`↰` parallel), and the CLI renderer follows.
  - **The always-on prompt contract SHRANK by 558 chars** (59,615 → 59,057, locked in
    `tests/prompt_budget.json`): two kind bullets became one, and the mode's meaning travels in
    the FIELD description the wire already carries on every turn rather than being recited.
- `src/rsched/migrate_child_kind.py` (MIGRATION, expires 2026-12-31) rewrites every INSTRUCTION
  on this instance that names a retired kind — the library's workflows, rules, permissions and
  patterns, every routine's `main.md` and `stages/*.md`, every conversation's `instruction.md`.
  Not optional: the engine's text ships with the release, the instance's does not, so a recipe
  still saying `subtask` after the kind is gone tells a run to emit something the schema
  refuses. It does NOT touch a routine's `state/`, its LEDGER or any transcript: those are the
  RECORD of what happened, and a run really did call `subtask` in October.
  A BARE `spawn` is deliberately never rewritten — it is also the name of a hook POINT
  (`moment: spawn`, which rules match on) and ordinary English besides.

## [0.565.0] — 2026-11-20

### Added
- **`GET /api/credential-screen`: the daemon compares a file's content against the credentials
  this instance stores, and answers with names, coverage and a digest — never a value** (D296,
  operator decision 2026-11-19, option a). D171 gave `rsched export` the NAMES of every stored
  credential because the util's Landlock jail hides the stores themselves — but a name cannot be
  matched against file content, so the export has been exiting 0 with `withheld_coverage:
  partial`, having compared NOTHING by value for the central store and the OAuth connections,
  while 11 named files sat absent from the mirror and an empty `withheld` list read like a clean
  one. The comparison now happens where the values already are.
  - The caller names absolute paths; the daemon opens each one ITSELF, so no caller-supplied
    bytes are ever screened and this is not a chosen-input search oracle. `O_NOFOLLOW` plus a
    regular-file check on the OPEN DESCRIPTOR (never a prior stat), inside the instance's own
    homes (`routines_home`, `conversations_home`, `background_home`, `libraries_home`), with
    containment judged on the RESOLVED path so neither `..` nor a link smuggles a target in.
  - Bounded streaming: 64 MiB per file — deliberately ABOVE the export's own 10 MiB exclusion,
    since those files are precisely the ones nothing has ever compared — with a
    `longest needle − 1` overlap, so a credential split across a read boundary is still found.
  - Each result carries the **sha256 of the exact bytes screened**, which binds the verdict to
    them: a caller can prove it publishes the file that was screened. `clean` is said ONLY on
    full coverage, a whole file read, nothing matched and nothing changed underneath
    (`st_dev`/`st_ino`/`st_size`/`st_mtime_ns` re-checked after the read). A truncated read, a
    changed file, an unreadable source or an unopenable path is `unknown` — and NEVER "absent",
    because a discovery failure read as confirmed absence is how a mirror prunes a file nobody
    screened.
  - A GET, because a screen reads: acting on the verdict stays the export's, so
    `ROUTINE_TOKEN_MUTATIONS` stays empty and the routine token the export runs under reaches it.
  - The adversarial regressions the decision named are all present and each is proven red-first
    by neutering its guard alone: synthetic-secret, escaped-secret (a multi-line key is stored
    JSON-escaped and a verbatim match would never see it), boundary-size, symlink/race,
    partial-source and the pruning refusals. 24 new tests.
  - D171's isolation is NOT loosened: no response field carries a value, asserted over the whole
    serialized body. The residual channel is stated rather than papered over — needles are whole
    stored values of at least 8 characters, so a caller who already knows a value in full can
    have its presence confirmed; no prefix and no shorter fragment ever matches.

### Changed
- `tests/memcharges.py`'s sibling for credentials: `src/rsched/credneedles.py` builds the needle
  set from each store's OWNING module (`secrets`, `oauth.store`, the new `triggers.webhook_tokens`),
  so a store renamed by its owner cannot leave a stale literal that quietly stops being matched.
  It lists a store directory with `iterdir`, never `glob`: on a directory access is denied for,
  glob swallows the `PermissionError` and yields nothing, so a store nobody could look at reads as
  an EMPTY one and coverage says `full` — the reproducible defect the export was found to have.
- `docs/architecture.md` and `docs/sandboxing.md` carry the screen beside D171's names route, and
  sandboxing.md's claim that the export "withholds every file carrying a credential the instance
  holds" is corrected: that is what the guard aims at, not what it could promise from inside the
  jail that hides the stores.

## [0.564.1] — 2026-11-20

### Fixed
- **The release gate's browser charge is the stage's MEASURED peak, read in the container that
  runs it (goal b9).** `NEED_MB["browser"]` was 3584 MB — the `uitest` container's whole 4 GiB
  CEILING standing in for a hold — and it was read against the ENGINE HOST's `MemAvailable`,
  although the browser stage runs in that container's own cgroup, which the host's figure does
  not bound. Either half alone refused the ui stage of THREE release gates on 2026-11-19, each
  with quality and fast already green, each running ZERO browser tests.
  `deploy/uitest/submit.py` has been printing the real peak on every job all along
  (`uitest memory: {"peak_bytes": …}`): **1,876 / 1,831 / 1,840 / 1,811 / 1,797 MB** over the
  five most recent stages that ran — a 4 % spread, as a fixed browser suite should be. The charge
  is the worst of them rounded up to the next 256 MB: **2048**, by the same rule the fast and
  serial figures use. And a browser session is now read against the uitest cgroup ALONE
  (`memcharges.READERS_FOR`): measured inside that container, its cgroup had 4,040 MB of headroom
  while the host read 2,413 MB available, and `mem_limit: 4g` + `memswap_limit: 5g` is what the
  kernel actually enforces there. Decided by the operator, shown those measurements: *"Price
  `browser` from submit.py's measured peak inside the uitest container, against that container's
  own headroom"*.
  A container that really IS short still waits — pinned, so the guard cannot become a rubber
  stamp — and past the deadline it still starts under a bound (D295).

### Changed
- `tests/memcharges.py` is new: what a selection COSTS, the measurements behind each figure, and
  which reading may refuse it are one responsibility, and holding them beside the waiting logic
  put `tests/memgate.py` 53 lines over the house line bar. `memgate` re-exports every name its
  callers already read, so the split reaches none of them.

## [0.564.0] — 2026-11-20

### Changed
- **A model's window and output maximum are its ROUTE's, read from that route's own catalog —
  never the model's public figure.** Behind the Otari gateway every name is routed by Otari's
  `aliases:`, and the limits refresh read the OpenRouter REFERENCE catalog for any name whose
  route published nothing: the model's public window, not the route's. `gpt-5.6-luna` was sized at
  1,050,000 tokens on the Codex Plus login that serves it at 372,000. Config now names a gateway
  (`gateways: {otari: {routes_file, plans}}`, and `gateway: otari` on each endpoint behind it);
  the refresh resolves each name through the gateway's routing file (`endpoints/routes.py`) and
  reads the route's catalog (`endpoints/routecatalogs.py`): CLIProxyAPI's model catalog
  (`router-for-me/models`) for the subscription PLAN the config names — the plan is config,
  never guessed, and two plans disagreeing leave the route unknown — OpenRouter by exact id via
  `top_provider`, Nano-GPT's detailed listing, the static Claude table for Anthropic's own API.
  A route that publishes nothing stays UNKNOWN; the reference fallback for limits is gone (the
  catalog is still the price source), and the static table no longer answers for a proxy's
  Claude ids, which a subscription may serve on a smaller window. Editing the routing file makes
  the cache stale at once. Each cache row records its `route`.
- **The output cap is the route's published maximum** (operator, 2026-11-20: *"not below"*).
  `ENGINE_OUTPUT_CEILING` (32,000) and the quarter-of-the-window clamp are retired. ONE guard
  remains: a published figure that is the window restated — OpenRouter's ⌊0.9 × window⌋
  placeholder (`z-ai/glm-5.3`, `google/gemini-4-argon`: 943,718 on 1,048,576), or the window
  itself — is not a maximum, since providers validate `input + requested_output <= window`; it is
  replaced by the figure Nano-GPT's listing and CLIProxyAPI's plans agree on under the shared
  name, or left unknown (`routecatalogs.is_placeholder`). A row with a window and no published
  maximum (Ollama, vLLM, the Claude table) claims no output figure, so the model takes the
  endpoint's `max_tokens`, then the engine default — flagged on its Settings card.
- Nano-GPT endpoints are read from the documented detailed listing
  (`/api/v1/models?detailed=true`); OpenRouter's window is its `top_provider`'s
  (`xiaomi/mimo-v2.6-pro`: 1,048,576 served under a 1,050,000 headline).

### Added
- `claude-haiku-5` in the static Claude window table (1,000,000) — `claude-haiku-5-5` on
  Anthropic's own API had no row.

## [0.563.0] — 2026-11-20

### Fixed
- **`rsched postdeploy` no longer FAILs a healthy deploy that is still busy, and says what kind
  of "no answer" it got.** The 0.562.0 deploy's session ran the command ten seconds after the
  restart, while reconnecting console tabs held every `/api/*` request for 17-20 s; its one 10 s
  status read gave up and `served-identity` said "no answer … check you are on the right host"
  about a daemon that was up, on the right host and serving the right build. The read
  (`cli_postdeploy._status`) now names why it got nothing — `refused`, `timeout`, `auth`,
  `server-error`, `bad-url`, `not-status` — and `await_status` asks again every 5 s for up to a
  minute while the why is one time can change (`timeout`, `refused`, `server-error`), telling a
  waiting person so on stderr; a refused token or a mistyped url returns at once.
  `postdeploy.served_identity` words each why as its own next move (`NO_ANSWER`) and names how
  many attempts it made. The daemon's own post-boot run passes `wait_s=0`, since
  `daemon/postdeploy_watch` already polls its port. The real HTTP read is now exercised in
  `tests/test_postdeploy_status.py` against sockets the test opens on 127.0.0.1, which closes the
  `docs/limits.md` entry that said it was exercised nowhere.

## [0.562.0] — 2026-11-20

### Fixed
- **A pending change proposing a folder grant the accept would refuse is refused at its writer.**
  Creating `feeld-webui-maintainer` on the `code-maintainer` pattern wrote a creation draft
  proposing `fs_write_roots: ["work"]`; accepting it on the routine page 400d with
  `'work' is not an absolute path`, and because the accept is whole-draft every other kept
  change failed with it. The settings judge (`patterns/validate.problem`, called by
  `drafts.write`, `store.problems` and the recommender) judged both folder-grant fields by the
  patch model's `list[str]` alone — two documented `GAP —` entries in `NO_SEMANTIC_CHECK`. The
  grant edges' rule now lives in ONE pure function, `rootgrants.normalize_roots` (absolute after
  `~`/`$VARS` expand, never a credential store unless already held), which
  `config_fields.validate_roots` wraps for its 400 and the judge calls directly — so a draft is
  refused with the accept's own sentence. Both entries leave the ratchet (`GAP` 13 → 11), and
  `store.problems` drops its own second credential-store line: the judge now says it.
- **The recommender no longer proposes the routine's own directory, or a permission whose
  WITHOUT line already covers the task.** Its prompt now names the routine's directory and says
  roots are absolute paths outside it (`patterns/recommend_roots`, which also drops own-directory
  and relative entries from a proposed list before judging it, keeping any the routine already
  holds), and lists every permission with its `effect:` WITH / WITHOUT / WHEN lines, marking a
  permission that reserves a util verb (`requires.utils: [x:verb]`) as a STANDING pre-approval to
  be proposed only when the task says those calls are pre-approved. The same draft had proposed
  `android-actuation` "for observe/navigate", which that doc's WITHOUT line says needs none.

## [0.561.0] — 2026-11-20

### Fixed
- **A routine denied the reminder route is now told its own one** (F914, reported by
  `rules-review` in R3539). It held `capabilities.reminders: global`, met the 403 on
  `DELETE /api/routines/{slug}/reminders/{rid}`, concluded from its action catalog that no
  curator-mutation lever existed, and left an operator-approved cleanup
  (`q-20261117-173432-55`) unapplied — proposing a duplicate route instead. Both halves were
  working as designed: the console's delete is the user's only lever over a reminder a run
  wrote without approval, and a run disposes of one through the `remind` SIDE FIELD riding any
  action, which is a field and so appears in no action catalog. The tier refusal already routes
  two other classes home (a config change to `ask_user` with `config_patch`, a file to
  `read_file`); it now routes the reminder class the same way, naming `op=delete`/`revise` for
  this routine's own rows and `op=dead` for a row in another store. The tier itself is
  unmoved — `ROUTINE_TOKEN_MUTATIONS` stays empty and the route stays the operator's.

## [0.560.0] — 2026-11-20

### Fixed
- **A goal the person withdrew can be retired by the words that withdrew it, even when they
  were spoken to an earlier run** (F911, reported by `folder-reorg` in R3513/R3569). The
  grounding check for `goal verb=drop` and `verb=change` read `goals.person_texts` over THIS
  run's transcript only. An operator answer is the person speaking — `_person_spoke` already
  counts an `answer` event as such — but an answer given to an earlier run was consumed by
  that run, so it lives in `questions/answered/` and in no transcript the current run can
  read. A drop quoting a settled answer verbatim ("Yes, drop b19 — it is withdrawn") was
  therefore refused as "not later user words", and every subsequent digest presented an
  obsolete obligation while calling the settled approval pending.
  `goalops.answered_texts(routine_dir, since=…)` reads that store, and `_heard_later` tries
  it ONLY for a goal whose grounding lies in an earlier run (`heard == -1`, what
  `obligations.carried` stamps), bounded by the obligation's own `since` date — the store's
  version of `heard_at(after=…)`, so an answer predating the obligation cannot withdraw it.
  A goal set from words in the current run keeps transcript ordering untouched. The refusal
  now names both surfaces it searched, so an unread store cannot read as a missing quote.

## [0.559.0] — 2026-11-20

### Fixed
- **`subruns` denied that background work existed (F923, R3600).** The operator caught it mid-run
  in `tv-show-tracker-seedbox-manager`: a `background: true` script was in flight as `bg1`, the
  run asked `subruns` to see what it was waiting for, and the answer was *"no record of a child in
  THIS leg — and none on disk either, so none was started"*. Work the run had started was
  running, and the one action whose job is to say what is running denied it. `spawn`/`subtask` and
  a backgrounded call are different mechanisms but the same thing to a run reading its own state:
  work it started, not yet in hand, delivered at a later turn boundary under an identifier —
  which is why `kill` already took both namespaces (`n` for a child, `handle` for a thread) and
  why the status table was the last surface that knew one and not the other.
  - New `engine/background_status.py` turns the live `Pending` list into rows
    (`handle`, `kind`, `brief`, `started_turn`, `state`, `stoppable`). `state` separates `running`
    from `landed` because `collect` drops a delivered Pending only at the next turn boundary, so a
    call that finished mid-turn is still in the list — reporting it as running would replace one
    false claim with another.
  - They ride a separate `background` key, not more `rows`: `subruns`' `count` has always meant
    children, and folding the two would make it mean two things and invite `kill n=1` at a `bg1`.
  - The *"none was started"* sentence now speaks only when nothing is in flight either, and the
    rows render under the children.
  - Its own module because `engine/background.py` sits at 474 lines on the size ratchet's
    grandfathered list, where one added line crosses the bar.
  - `tests/test_subruns_background.py`, red-first: with both halves neutered 4 of 6 fail, and the
    live-run test drives the real loop with a blocked util so a true in-flight call is observed.

## [0.558.0] — 2026-11-19

### Fixed
- **A playbook saved with its tags spelled out one letter at a time (F922).** The operator saved
  a playbook and the Library tab tagged it `e` `v` `a` `l` `u` `a`. `PLAYBOOK_SCHEMA` asks for an
  ARRAY of tags, the system model answered the STRING `"evaluation…"`, and
  `playbook_distill._normalize` did `for t in (data.get("tags") or [])` — which iterates a
  string's CHARACTERS — then capped the result at six. `conversations.autolabel` had the same
  line capped at three, so a conversation whose model answered a string would have been tagged
  `a` `i` `x`. Both now go through `ids.tag_list`, the ONE tag rule beside `ids.slugify`'s: a
  string is SPLIT on commas and whitespace, a list keeps working exactly as before, and the
  result is a set in order of appearance so a repeated tag lands once.
- **The playbook lint could not see it (F922).** It asked for "at least one tag" and six single
  letters satisfied that, so `rsched lint --kind playbooks` reported `pass` on the broken row and
  nothing but the operator's eye caught it. More than one single-character tag is now a lint
  problem naming the letters, which is what the PUT repair route gates on.

## [0.557.0] — 2026-11-19

### Fixed
- **The browser sidecar dies whole or not at all (D253, SD2.5).** `deploy/chrome-entrypoint.sh`
  starts FIVE processes — Xvfb, x11vnc, websockify, the CDP/noVNC auth proxy and Chrome — and
  supervised ONE: Chrome was `exec`'d as PID 1, so compose's `restart:` only ever saw the BROWSER
  die. Any of the other four dying left a container that is up and broken — Xvfb gone means
  Chrome paints into nothing, websockify gone means the noVNC page never connects, the proxy gone
  means every CDP client is refused — and nothing exited, so nothing restarted. What reached a
  person was "the browser view is black" with a healthy-looking container behind it. All five are
  now backgrounded with their pids captured, and the script waits for the first to exit
  (`wait -n -p`), logs WHICH one it was and exits with its status; `set -e` would otherwise make
  that status fatal before anything is logged.
- **A fresh install synced a venv the service never opens (D253, SD2.6).** The systemd unit keeps
  the daemon's environment outside the checkout (`UV_PROJECT_ENVIRONMENT=%h/.local/share/rsched-venv`),
  while `deploy/install.sh` ran a bare `uv sync` — populating `<repo>/.venv` and then printing
  "venv synced". A first install reported success and left the service to build its own
  environment on first start; a sync after a dependency change updated the wrong one. The
  installer now derives that path FROM the unit (so the two cannot drift) and refuses outright if
  the unit stops setting it.

### Changed
- **The claude CLI is pinned like every other dependency (D253).** `npm install -g
  @anthropic-ai/claude-code` took whatever was published that day, so two rebuilds of one commit
  could ship different CLIs with nothing in the repository to say why — and it is a native binary
  placed by a postinstall, the part most likely to change under us. Pinned via
  `ARG CLAUDE_CODE_VERSION`, at the version the running container reports (2.1.287), bumped
  deliberately like the uv and image digests above it.
- `docs/designs.md` loses the *"Deploy leftovers (SD2.5-6) and a pinned claude CLI"* entry: every
  bullet is now in the tree. Two of them were already satisfied — `setup-remote-agent-user.sh`
  has carried `usermod -aG video,render` for some time, and `backup.sh` identifies the share with
  a STRONGER predicate than the `findmnt -T` the entry asked for (`stat -c %m` resolves the mount
  a path actually lives on at any depth, plus a device comparison against `$HOME`, which the
  script's own comment explains catches the unplugged-disk case a literal `mountpoint` misses).

### Added
- `tests/test_chrome_sidecar.py` — the sidecar's entrypoint RUN, not read, against stub binaries:
  each of three processes dying ends the script within 20 s with that process's own status and
  names it in the log, nothing dying leaves it up, and a missing `BROWSER_CDP_TOKEN` still
  refuses to start (EX_CONFIG). A supervisor loop can satisfy every text assertion and still
  abort on `set -u` or wait on the wrong thing, which is why control flow is executed here while
  `tests/test_deploy_image.py` keeps holding the deploy files' pins by reading them.

## [0.556.1] — 2026-11-19

### Changed
- **The release gate BOUNDS what a test session holds instead of predicting it, and prices a
  selection from its own measured history (D295, goal b8).** The admission guard charged one
  class-wide constant per selection class — 2,816 MB for any serial selection, 3,584 MB for the
  fast stage — against host `MemAvailable`, and refused with ZERO tests after waiting 20 minutes
  when the host was below it. On this box that refused five gates in two days with
  `real_failures: []` each time, while the whole non-browser suite measurably holds ~1.2 GB. The
  operator's decision was *"b then a"*: bound the hold first, then price the estimate from
  history.
  - `tests/memwatch.py` — an RSS watchdog over the session. It samples the ENGINE CONTAINER's own
    memory cgroup (anon + swap) every 0.25 s and ends the session over its cap, which is the
    stronger guard: what the wait exists to prevent is the KERNEL choosing an OOM victim (on
    2026-11-05 it killed a live routine run), and a watchdog stops the TESTS instead of hoping
    about the host.
  - `tests/memhistory.py` — the measured holds of a SELECTION, keyed on its paths, marks and
    worker count, appended as the session ends. A selection that has run before is priced on what
    it actually held rather than on its class's worst case.
  - `tests/memgate.py` — at the wait's deadline the session now STARTS under a bound instead of
    exiting 3 having run nothing. A verdict obtained under a bound is worth more than no verdict.
  Two asymmetries are deliberate and pinned. History may lower what is ADMITTED but never the
  BOUND, which always comes from the class's measured worst case: being wrong about "may this
  start" costs a wait, being wrong about "must this be stopped" costs the gate
  (`test_history_may_lower_admission_but_never_the_bound` encodes the specimen that taught it — a
  0.3 s session's 68 MB priced a 1,024 MB cap against a real 1,056 MB stage). And the cap follows
  the ADMISSION figure, never the memory that happens to be free: the host had 2,802 MB available
  while the fast stage is measured holding 3.08-3.29 GB, so a headroom-derived cap would kill a
  healthy gate (`test_the_cap_does_not_follow_the_memory_that_happens_to_be_free`).
  The `browser` charge is untouched: it runs in the `uitest` container's own 4 GiB cgroup, so
  nothing measured in this one bears on it. That figure is ~1.9x the stage's measured hold and is
  reported separately (R3602).

## [0.556.0] — 2026-11-19

### Changed
- **C1.10 is finished: no pass-through forwarder stands in front of `libgit.commit` any more
  (D249).** The first increment converted `workflows.library`'s wrapper; the two it held back for
  judgement — `library_docs.git_commit` and `utils_lib.git_commit`, each a one-line
  `return libgit.commit(...)` — are converted and deleted, with their six call sites
  (`engine/authoring` x2, `web/api_workflows` x2, `web/api_library_utils`, `utils_stage`) now
  naming the primitive. What this buys is indirection removal, not coverage: a per-module wrapper
  hides which primitive a writer reached and is where a divergent default (`routines_home=None`,
  an unscoped stage) would spread unseen.
  `tests/test_libgit.py`'s `COMMIT_FORWARDERS_HELD_BACK` is now EMPTY and still shrink-only, so
  the test is a plain prohibition and a re-introduced forwarder reds the gate. Three imports the
  wrappers were the only users of went with them, so all three host files shrank (`utils_lib`
  349->339, `api_workflows` 331->329, `library_docs` 142->132).
- **`docs/designs.md` loses two shipped entries.** C1.10's, shipped here; and *"The outcome label
  for a rule assist's hold"* (decided 2026-09-22), whose four coupled parts all shipped on
  2026-11-06 as D239 — `engine/assist_label.py`, `rsched/assist_stats.py` with its one-shot
  migration, the health read model's four columns and the `assist_hold` observation's ask — and
  which was narrated in `docs/rule-assists.md` while its order sat here looking unbuilt.

### Fixed
- **A schema-free retry keeps the full action contract, and the correction keeps the last word
  (F903).** `completion.next_action` handed `endpoint.complete` to the transport directly, so the
  turn-head contract check (`turntakeover._contract_if_unenforced`) could not see a schema shed
  or an endpoint switch made INSIDE the retry loop: an index-only run could make its retry with
  no full field descriptions anywhere. The outbound request is now one boundary
  (`engine/completionrequest.complete_attempt`), which consults the schema this attempt will
  actually send and posts the existing once-per-run contract note when the wire no longer carries
  it — covering last-attempt shedding, a recalled rejection, an empty-reply shed and a failover
  to a non-schema endpoint.
  The note is INSERTED BEFORE this turn's debris block rather than appended
  (`engine/turndebris.head_index`, `enginenote.append(before=…)`): the correction has to remain
  the last user message, because that is where D287's repeat sentence is read
  (`engine/schemamemo`). Appending it instead turned that mechanism silently off.

## [0.555.1] — 2026-11-19

### Two blocked-work rows 0.555.0 should have cleared

- **The marker grammar's own comment declared a marker.** 0.555.0's explanation of the wrapped
  tombstone quoted it — marker, date and a line break ending on `#` — so `migration_markers.py`
  itself read as declaring an 11-30 expiry, and the `migration_due_soon` row it was written to
  clear came back. The comment no longer spells the date; a test holds the module to declaring
  nothing.
- **An overhead row on a revised recipe with no measured run is over.** 0.555.0 kept it standing
  as "unknown", but the alarm itself would not file in that state; three routines revised that
  morning (uncensored-model-radar, rules-review, library-sync) still stood. predatory-publisher-
  tarpit stays: its current recipe reads 0.343 over three runs.

## [0.555.0] — 2026-11-19

### Blocked work says only what is blocked, and owed work reaches whoever owes it

- **Wrong turns leave the blocked-work list for a panel of their own.** ~230 of the week's 285
  blocked-work incidents were `wrong_turn_detected` — turns rejected and re-driven while their
  runs went on — and they buried the one row only the operator could clear (a stale backup).
  `readmodels/wrong_turns.py` + `GET /api/health/wrong-turns` + Stats' **Wrong turns** panel fold
  the same events by CAUSE (turn numbers, numbers and quoted values masked), so one recurring
  mistake across routines is one row, one fix.
- **A standing condition that is over leaves the table.** Every standing row
  (`obligation_overdue`, `process_overhead_high`, `migration_due_soon`/`_expired`) is now asked
  whether it still holds, as the operator's rows already were (`web/operator_conditions`);
  resolved rows leave `standing_rows` and the table and are named in one line. The five
  overhead rows on 2026-11-19 were all of this kind: no routine was over the bar any more.
- **An obligation says who its next move is on.** `b<n> waits on operator|external|<routine>:
  <act>` (parses as `unmet`, `engine/accounting.py`). One waiting on the operator becomes ONE
  `owed-obligation` Decisions card the next morning — it's done / drop it / still wanted
  (`web/api_owed_obligation.py`, `components/owed-card.js`); settled rows are kept in
  `state/obligations.json` so a run already carrying one cannot write it back, and the routine
  is told in its inbox. One waiting on another routine that runs, or on the world (30 days), is
  no longer the routine's overdue work. Before this, most `obligation_overdue` rows were items no
  run of that routine could ever clear: an approval, a payment, a host change, a film not out.
- **Overhead that persists across recipe changes is judged.** A recipe commit resets the
  overhead window, so a routine revised more often than every third run was never judged
  (freelance-hunter: nine recipe commits in eight days). Now the newest 3 runs of any recipe,
  all over the bar with one under the current recipe, read `over` (`persistent`). The finish
  block's overhead line names the causes measured this month — rules and stages read up front,
  one file per turn, spilled output re-read, failing actions — instead of the bookkeeping the
  11-06 sweep had already removed (six runs saw the old line; one cut came of it).
- **The overhead classifier stops counting product names and delegated work as bookkeeping.**
  A record word now matches only as a whole word of a path component or program name
  ("journalhub", a publisher, and `runledger.py` no longer read as a journal or a ledger), and a
  run reading its OWN child runs' work (`runs/<this run>/sub/…`, `artifacts/from-*`) is task.
  Recalibrated on the original run set: the bar still separates 11 of 12, THRESHOLD unchanged.
  Counting spilled-output reads as task was MEASURED and held back — it collapses the gap
  (tv-show-tracker 0.255 = funscript-trainer 0.255) and needs a fresh hand calibration.
- **The fleet's recurring wrong turns are accepted in their intended shape** (`engine/fieldshapes.py`,
  lossless repairs only): a memory note named `X.md` is note `X`, a numeric string where an
  integer is due, one id where a list is due, a bare `verified` date, a `remind` without `op`, one
  reminder label written as a list or a map; the reminder sentence names `regex`/`description`.
  `read_file` reads a TAIL on a negative `start_line`, and `max_lines` over 500 is served at 500
  with the header saying so instead of a refusal.
- **"No revision needed" files no card**, even when it names the recipe files
  ("none needed in main.md/stages/ — …" opened a Decisions card on 2026-11-15).

- **A gateway that refuses the connection is waited out, not failed over.** Every model route
  leaves through one gateway, so when Otari restarted for 14 s on 2026-11-19 three tries in ~3 s
  failed BOTH rungs of Opus's chain and ended llmsectest-weekday's run. A refused connection
  (nothing was received, so nothing is repeated) is now retried for up to two minutes
  (`endpoints/base.UNREACHABLE_PATIENCE`); every other retryable error keeps its three tries.
- **The recipe contract lint stops failing a recipe that says the opposite.** "`LEDGER.md` is the
  engine's … a run neither appends to it nor rotates it" FAILED `rsched validate` for
  llmsectest-weekday (the ledger named before the engine; "neither … nor" was not read as a
  negation), and its d3 "outranks the rotation" — a rotation of heavy phases — was noted as a
  ledger rotation. Both are read as what they say now (`workflows/taskcontract.py`).

- **A wrapped tombstone is not a migration marker.** `cli_daemon.py`'s two tombstones wrapped so
  `MIGRATION(expires=2026-11-30)` opened a comment line mid-sentence, and the marker watch filed
  them as due-soon for migrations deleted on 2026-10-08. `migration_markers.declares` now reads a
  marker opening a `#` line under a comment line ending on a word as the same sentence — prose.

## [0.554.0] — 2026-11-19

### The index the digest points at is readable by the action it names

- **F910 — every run was told to read a file nothing could open.** `notemeta.digest_blocks`
  clipped the `.memory/` block with *"read .memory/INDEX.md for the full N lines"*, and
  `digesttail.clip` appended *".memory/INDEX.md holds it whole"* — while `read_file` refuses
  every `.memory/` path (`engine/fileops.MEMORY_REFUSAL`) and `memory_read` refused the name
  for not being a kebab-case slug. Three doors, all locked, with the engine's own prose
  pointing at them. **Measured on 2026-11-18 alone: seven turns lost across five routines**
  (config-optimizer, rules-review, llmsectest-weekday, global-utils-review, routine-improver)
  — `config-optimizer:20261118-022505` spent it on turns 3 and 4, CONSECUTIVELY, because the
  refusal taught it nothing it could act on.
- **`memory_read name=index` now returns the whole index** (`index`, `INDEX`, `INDEX.md` — the
  pointer is rendered as a filename, so the name it prints is the name that works). Writing it
  stays reserved, and `INDEX.md` is now reserved too: `name.lower() == "index"` let that
  spelling fall through to the slug rule, which answers a different question.
- **Both pointers now name the ACTION rather than the path**, and the `memory_read` bullet in
  every prompt says the digest's copy is clipped and which call returns it whole. A fix that
  only accepted the call would have left every future run making the one that fails first.
- `notemeta.index_read(kind, name)` carries the rule because `engine/actions.py` is AT its
  size-ratchet ceiling (417): a rule that costs its caller a line gets written somewhere worse.

## [0.553.0] — 2026-11-19

### The Library tab stops telling you a change to an open util "reaches nobody"

- **F909 — "noone holds this util?! that's not true" (the operator, 2026-11-18).** He was
  looking at `code-search`'s editor, which said *"no routine holds this yet — a change here
  reaches nobody."* Every routine on the instance can call that util, and the engine rewrites
  the `code_search` and `websearch` ACTION KINDS (D290, `engine/kindaliases.py`) into exactly
  that util call before any gate sees them. The emptiness was real but its reading was
  inverted: `library_impact.holders` answers *who holds a RESERVED grant*, and a util has
  holders only where some permission doc's `requires.utils` names it — 7 of today's 56 utils.
  For the other 49 — `code-search`, `rsched`, `runs`, `json`, `git` — the panel printed the
  most dangerous sentence available over the fleet's most-used tools, and the same computation
  gates DELETION from that page.
- **`library_impact.reach()` is the second fact, beside `holders` and not merged into it.** It
  reads the same `requires.utils` join `policyload.load_policy` builds `gated_utils` /
  `gated_verbs` from, for one util: `{open_to_all, gated_by, gated_verbs, fleet}`, and `None`
  for a rule or a permission — those ARE held by name, so an empty list there really does mean
  nobody, and that wording survives untouched. An open util now reads *"every routine (29) may
  call this"*; a verb-reserved one like `mail` states both truths at once — 6 routines hold
  `mail:send`, all 29 may call its other verbs.
- **Why not simply widen the holder list.** A holder is a routine the break analysis can reason
  about: `readmodels/surface.py` joins util headers through `_held_utils`, the reserved grants,
  so an open util's secrets and roots enter no routine's surface. Listing all 29 would have
  paired a full-fleet list with "breaks none of them" — a second falsehood — and cost ~3.2 s of
  surface computation per preview (measured: 29 surfaces in 1.60 s, and an impact does them
  twice) on a console whose slowness is a standing complaint. The panel says instead that the
  holders shown are those with a reserved grant and that the break analysis reads no further.

## [0.552.0] — 2026-11-18

### Reminders are served from the hook stream, and five decided items ship with it

- **D152/b4 — the reminder half of the hook campaign is CUT OVER.** `reminders.active` — the set
  the engine matches every action, observation and turn moment against — now takes its LOCAL
  definitions from the hook stream (`hooks/reminderread.active`) instead of
  `state/reminders.json`. Until now any writer that touched that file could decide whether an
  action is HELD. Tallies still come from the routine's own store, because the stream carries no
  counter by design, and `hooks/reminderproj.stored_definitions` deliberately keeps reading the
  RAW store so `drift` compares two independent sides rather than the stream with itself. The
  health view (`readmodels/run_health.cautions`) was cut over first, a page being cheaper to be
  wrong on than a hold. Three hazards were closed before either step:
  - the console's `DELETE /api/routines/{slug}/reminders/{rid}` wrote only the raw row, so a
    stream-served read handed the deleted definition straight back — under the `{"ok": true}`
    the user had already seen. It now appends `disposition="remove"` to the stream, and the
    endpoint moved to `api_routine_edit.py` (the write half), taking `api_routines.py` from 380
    to 346 lines and off its shrink-only ceiling entirely.
  - a HOLDER's keep/mute/remove was recorded with itself as origin, and the projection derived
    `owner` from that — so a disposition quietly transferred ownership of a hook a sibling had
    set, and `owner` is what decides who may remove one. A row whose origin is the holder now
    inherits the definition's existing owner.
  - a definition the stream served but the store had never seen could not be counted, disposed
    of or capped (`record`, `dispose`, `set_for` all work off the store).
    `reminderread.materialize_stream_only` gives each a real row with a BLANK tally, at the one
    point where the live set is formed.
- **F908 — a retired reminder stops firing at once, and its id is never handed out again.**
  Observed on this instance: `rules-review` retired `rem-20261117-061103-1` at 07:29:29 and that
  same cached row HELD an action at 07:46:27, costing the run a turn and a `could_not` label for
  a caution it had already withdrawn; a resumed leg then allocated the id again, so the new
  caution inherited the old death marker and was suppressed on every fresh load. A self-targeted
  `dead` op now drops the row from the live set in place, and both allocators reserve against
  `remind_dead.reserved_ids` — every id on disk plus every id an annotation retired — instead of
  against the live set the dead filter has already emptied.
- **F907 — saving a conversation as a playbook tries its configured fallback chain.** The
  operator's save-as-playbook died on an upstream HTTP 429 because `playbook_distill._infer`
  resolved one model and let the exhausted error escape to a 502, leaving the catalog's declared
  `fallbacks:` untried. It now advances the chain with the engine's own call-time primitive
  (`failover.next_after`) — retryable failures only, each rung keeping its own effort and token
  limits, malformed replies still raising at once, nothing truncated to fit a smaller rung, and
  nothing written when the chain is exhausted.
- **R3566/R3565 — an `ok` finish waits on nothing outside the run.** The operator, 2026-11-17:
  *"sufficient conditions for a full finish … include ONLY conditions that are within your
  abilities inside of a run (also covering asking for permissions and grants you might need)."*
  All three status surfaces say so now (`engine/harness.py`, the finish bullet in
  `engine/kindprose.py`, the `status` description in `engine/actionschema.py`, which stayed
  exactly on its 628-line ceiling), and both prose surfaces carry the other half: a filed request
  never excuses reachable work left undone, nor makes unfinished work or a failed verification
  done. `docs/prompt-anatomy.md` §5.1 was re-rendered and its verbatim fence and §4 updated.
- **R3467/b7 — the authoring guide no longer promises util summaries a run cannot see.** The
  served CAPABILITIES catalog has been NAMES ONLY since 2026-11-14, and `docs/authoring.md` still
  described it as "name + one-line summary"; it now names the `search` verb instead. Ships with
  the narrow per-occurrence library-subject exclusion and the actionable capability remedy in
  `workflows/recipelint.py`.
- **F896 — the files card no longer stats an SSHFS mount once per row.** One lazy app-owned
  worker pool with a per-request submission window, ordered rows and ordered error propagation
  (`web/filechecks.py`); `api_runs.run_files` delegates the row checks. The <1 s production
  target over a live network mount is NOT verified here — only the bounded concurrent behaviour
  and its 33 regressions are.
- **b6 prerequisite** — `endpoints/routine_env.py` serves both kept catalogs by name, with
  `tests/test_llm_catalog_names.py` pinning the composition. The `llm` and `decide` action kinds
  are NOT yet removed, so goal b6 remains open.

## [0.551.2] — 2026-11-18

### The declared Pydantic floor matches the API the config actually uses

- **The defect** (`config-optimizer`'s F42, decided by the operator as R3534). `pyproject.toml`
  declared `pydantic>=2.9`, while `src/rsched/config/base.py` builds its models with
  `ConfigDict(validate_by_name=True)` — a setting Pydantic added in **2.11**. An install
  resolving the declared minimum would accept by-name construction silently and drop the
  values, so the declaration promised support for versions the code cannot run on.
- **Measured, not assumed.** The real `RoutineConfig` was round-tripped (alias in, by-name in,
  dump out) against an isolated **2.11.0** target: it passes. The same probe against the
  formerly allowed **2.9.0** loses the by-name values, which is the mismatch itself reproduced
  rather than inferred. Evidence: `scheduler-builder/artifacts/pydantic-floor-check.json`.
- **Deliberately narrow.** Only the floor moves — `pydantic>=2.9` → `pydantic>=2.11` in
  `pyproject.toml`, and the matching `requires-dist` specifier in `uv.lock`. No locked package
  version, artifact, hash or unrelated metadata changed (the lock stays pinned at the 2.14.1 it
  already resolved), and the live environment was not refreshed: there is no outage to fix, only
  a declaration to correct. Verified with an offline lock-consistency check
  (`artifacts/pydantic-floor-lock-check.json`).

## [0.551.1] — 2026-11-16

### The browser suite no longer runs out of memory: an app is freed once its test ends

- **The defect.** Every full browser-suite run ended with an xdist worker OOM-killed inside the
  `uitest` container's 4 GiB cgroup (`node down: Not properly terminated`) at ~90%, after which
  the run hung. The kernel log has twelve such kills of a `uitest` python since 2026-11-13, each
  at ~1.9 GB anon. Measured per test (a throwaway probe reading `RssAnon` after each one): every
  worker grew 3-7 MB per test in every file alike, so no test was at fault. After `gc.collect()`,
  **40 FastAPI apps were alive after 40 tests**, one per test.
- **The cause.** FastAPI classifies endpoint and dependency callables through process-lifetime
  `lru_cache`s keyed on the callable (`fastapi.dependencies.models`, 4096 entries). Seven routes
  (`/api/status`, `/api/setup/complete`, `/api/stream-ticket`, `/api/events`, `/`, `/sw.js`,
  `/manifest.webmanifest`) were closures inside `create_app`, so each app added new cache keys
  whose cells held that app and its ~560 routes' pydantic models. The daemon builds one app,
  so it never showed there.
- **What changed.** Those routes are module-level in the new `web/api_core.py` (two routers:
  the token-gated three, included with the same bearer dependency, and the public four) and read
  their app from `request.app.state` / `ws.app.state`. `create_app` now stores the boot `build`
  stamp on `app.state.build`. Served behaviour is unchanged. Measured after: 1-2 apps alive at
  any point (the current one, and one whose slow-request watcher has not retired yet), and the
  three files of the probe ran in 53 s instead of 75 s.
- `tests/test_app_release.py` pins that a dropped app is freed, both bare and after serving
  requests. It fails on the old `app.py`. CLAUDE.md gains the gotcha.

## [0.551.0] — 2026-11-16

### A subscription login can be removed from its card, and two logins for one address are told apart

- **The defect.** A new ChatGPT Plus subscription was signed in from the `cliproxy-mgmt` card
  (2026-11-16) for an address the proxy already held, a free-plan Codex login whose subscription
  ran out on 2026-10-10. The proxy kept both, and the card showed two identical rows,
  `Codex me+openai@…: active` twice. Nothing on the card said which one was the stale login, and
  dropping it was possible only in the proxy's own panel.
- **What changed.** Each account row has a *remove* button. After a confirm that names the auth
  file, `POST /api/settings/endpoints/{name}/proxy-accounts/remove` (`{account}`) deletes that one
  login through the proxy's management API (`cliproxy_login.remove`, `DELETE
  /v0/management/auth-files?name=…`). A card can remove only an account it lists, so the server
  re-reads the listing filtered to that card's providers before it sends the delete. The proxy's
  delete-everything form is never sent, the routine token is refused like on every
  config-mutating route, and nothing the management key sees reaches the console. Two rows with
  the same provider and address now each name their file (`codex-…-plus.json`).
- `tests/test_cliproxy_login.py`, `tests/test_proxy_login_api.py` and
  `tests/ui/test_proxy_settings.py` pin the delete, the refusals that send no delete, the route's
  binding and token, and the card's confirm, cancel and twin labels. `docs/model-gateway.md`
  (the section is now "Signing in, signing back in, and removing a login") and
  `docs/endpoints.md` describe the control, and the gateway doc also covers an `error` status that
  is one refused request rather than a dead login.

## [0.550.0] — 2026-11-15

### The wire stops forbidding the chaining the engine runs (R3422, F897)

- **The defect.** Every schema'd call on the Anthropic wire carried
  `tool_choice: {"type": "auto", "disable_parallel_tool_use": True}`. That field was written on
  **2026-10-01** with the commit subject *"an unforced action tool is held to one call, so a
  reply cannot carry two actions"* — correct then, because the adapter read `calls[0]` and
  dropped the rest, which is how an action vanished while a later one claimed it had run
  (R2443-R2445). **Three weeks later both halves of that premise went away**: the operator
  ordered action chaining (0.446.0, 2026-10-23, *"I want action chaining to work like in the
  terminal"*) and F754 (2026-10-24) made a multi-call reply reach the engine whole as `parsed`
  + `parsed_rest`. The field was never lifted, so for seven weeks the body asked the provider
  to suppress, at the wire, the native expression of a behaviour the harness asks for on every
  turn — and current Claude models emit several `tool_use` blocks in one turn for independent
  subtasks by default. A model forming a chaining intent could act on it only by writing
  actions as JSON text, which is the channel `native_turns` renders its history away from.
- **What changed.** The default `tool_choice` is plain `auto` (`_AUTO`). The degrade ladder is
  untouched — a configured `forced` route still sends `_FORCED`, a 400 naming `tool_choice`
  still unforces and then drops the field — and the one-call re-ask of an unreadable `auto`
  reply still fires. Nothing about reading a reply changed: `replyactions.reply_actions` has
  folded text actions then `parsed` then `parsed_rest` since F754, and `engine/chain.py` runs
  them in order, stopping at the first failure.
- **Measured, since the previous figure was wrong.** `rules-review` (R3422) counted 27 narrated
  chains against 0 emitted by counting actions that share a `turn` in `transcript.jsonl`. That
  misses every chain that STOPS at its first element: the stop records one `assistant_action`
  and hands the rest back in the observation's own `not_executed` rows. Counting both signals
  over 81 fleet transcripts of 2026-11-10..14 and 11,598 actions: **8 multi-action turns + 7
  stopped-chain observations = 15, or 0.13%** — still effectively nil, which is the finding.
- **What is NOT changed, deliberately.** `anthropic_messages.native_turns` still renders each
  past action as ONE `tool_use` per assistant message, so a model still reads its own history
  as one-call-per-reply and learns from it by example. Whether a chained reply should be
  rendered back as its several calls is a behaviour-shaping question with a measurement behind
  the current shape (*two actions in 5 of 5 replies before, one call in 5 of 5 after*), raised
  for the operator rather than changed here.
- `tests/test_chained_calls.py` gains the pin that the default ask does not suppress a
  multi-call reply; `docs/endpoints.md`, `docs/architecture.md` and `docs/prompt-anatomy.md`
  record what the wire now sends and that the history rendering is the remaining teacher.

### D291's worker reduction reaches the side that was actually binding (F898)

- **Measured live, mid-gate, 2026-11-15.** A release gate of this routine's own candidate sat
  in `memgate`'s wait printing *"host memory has 3318 MB, the fast suite at 4 xdist workers
  needs 3584 MB"*, with the cgroup reporting **3,845 MB free** and the host **2,797 MB**. It
  would have waited out `RSCHED_TEST_MEM_WAIT_S` (20 min) and exited 3 — the refusal D291
  replaced — while a three-worker selection (2,816 MB) was within reach the whole time.
- **The cause.** `browsersource.fast_workers` reduced the count against
  `memgate.cgroup_headroom_mb` ALONE, while `memgate.wait_for_memory` guards host MemAvailable
  **and** the cgroup and refuses on whichever is short. So on a box where the HOST is the
  binding side — this box, whose other containers hold ~12 of 15.4 GB — the count stayed at its
  ceiling and the session was refused rather than run smaller. D291's own intent, in the
  operator's words (*"Reduce FAST_WORKERS while a routine run is active"*), was unreachable
  there.
- **The fix.** `memgate.headroom_mb` is one reading for both: the SMALLER of host MemAvailable
  and cgroup headroom, which is exactly the pair the wait enforces — for the reason `need_mb`
  already gives, that the count and the charge must not be able to disagree. Both readers
  resolve at call time off the module, so D291's wiring pin still reaches what it patches.
- Two pins added to `tests/test_memgate_workers.py`: the host-short box reduces to three
  workers instead of being refused, and the headroom is the smaller of the two readings (None
  only when neither answers). D291's eleven existing pins are unchanged and green.

### Gate evidence for this release, stated exactly

- `quality` **GREEN** (ruff + mypy over 575 source files) and `fast` **GREEN** (7,189 tests)
  over this tree, gate `1794702011479264806-release`.
- The `ui` stage did **NOT** reach a green verdict, and this release ships without one. Twice
  (`…4806`, `…1929`) it ran to **799 passed / 0 failed** and then stalled at 99% with every
  started node resolved, hitting the new `STAGE_CEILING_S` at 1,830 s; the verdict is
  `timeout`, which says nothing about the candidate. The one node left unresolved differed
  each time (`test_view_teardown.py::test_leaving_right_after_a_send_does_not_remount_the_conversation`,
  then `test_test_hint.py::test_the_card_shows_the_hint_above_the_raw_error`) and **both pass
  standalone** (14 passed / 1 passed), so the stall is in the browser session's teardown, not
  in any test. `scheduler-builder` reported the same shape twice on 2026-11-14 (R3428) before
  any of this diff existed, and this candidate touches **no `static/` file at all** — its
  Python changes are the Anthropic wire and two test-infrastructure modules, and the browser
  stage runs at `BROWSER_WORKERS = 2` which `fast_workers` does not price. The honest reading
  is that the browser suite's own ledger is green and its stage cannot report; the wedge is a
  live defect of the harness, not of this release.

## [0.549.0] — 2026-11-14

### A reminder definition disagreement is settled by the STORE — his ruling, and the comparator reaches EMPTY (goal b9, D152 step 4)

- **The operator, 2026-11-14**, answering this run's question: *"The STORE is authoritative
  (self-audit owns it) — the holder's own record wins"*, over "the stream wins", "delete the
  reminder" and "leave it drifting as a permanent exception".
- **What he settled.** `rem-20261026-233255-1` read `owner: vodafone-outage-claims` on the hook
  stream and `owner: self-audit` in the store, and `owner` decides who may REMOVE a hook — so a
  migration guessing it would hand one routine power over another routine's reminder. The other
  three `differs` ids lack `created_run`, which the mirror era dropped.
- **Why his ruling is also the right general rule**, which is what makes it safe for all four:
  the store is where the HOLDING routine writes, through `remind_write.persist` — the one path a
  routine's own act takes — and the stream's row is a mirror of that act. Every disagreement
  measured was the mirror being poorer, never the store. Measured across all 195 live reminders,
  194 store `owner: ""` (self-held) and exactly one names another routine: the id he ruled on.
- **`migrate_store_wins` appends the store's own record**; the poorer row stays as history and
  the fold (last row wins, by id) reads the agreement. That is the opposite of
  `migrate_mirror_hooks`, which rewrote `reports.jsonl` in place — because reports fold
  FIRST-wins and reminders fold LAST-wins, so here an append is both sufficient and honest.
- **THE CAMPAIGN'S PRECONDITION IS NOW REACHABLE, proven on the live stores through the pure
  finders with no write:** of 27 drifting ids, 23 are accounted for as deletions (0.548.0) and 4
  as disagreements (this release) — **0 unaccounted**. A test asserts that relation against the
  live stores, so a class nobody accounts for fails rather than hides.
- A foreign hook keeps its HOLDER as addressee and takes the store's OWNER as origin: recording
  it as a self-hook would hide it from its holder and give the wrong routine the power to remove
  it.
- **Known limit: the two migrations run at daemon BOOT, so the comparator reads EMPTY only after
  the next restart — and nothing yet SERVES the reminder store from the stream.** Still open:
  D152 step 4 / goal b4.

## [0.548.0] — 2026-11-14

### The pre-mirror reminder DELETIONS reach the stream — D152 step 4, goal b4

- **Where the campaign stands**, measured over all 31 live routines with
  `hooks/reminderproj.drift`: **122 drifting ids in 21 routines before 0.542.0 → 27 in 9 after**
  (95 store-only → **0**, 23 stream-only, 4 differs). The dangerous class is empty: a
  stream-served read would have LOST 95 live patterns, and 0.542.0's backfill closed that on the
  real instance.
- **This release takes the 23 `stream-only` ids**, and corrects what 0.542.0's own module said
  about them. They are reminders a routine DELETED before `remind_write.remove` learned to mirror
  the deletion — so the stream still implies a definition the store has dropped. "Correct
  history" was right about the history and wrong about the consequence: the campaign ends with
  the store SERVED from the stream, and a fold that cannot see those deletions would **re-raise
  23 patterns their own routines removed**. A reminder decides whether an action is HELD.
- **Not a deletion from an append-only stream.** `migrate_premirror_deletions` appends the row
  the write path never wrote — one `disposition: remove` per id, which
  `reminderproj.REMOVING` already subtracts. The definition stays; the removal is a later fact
  about the same id, and both are history in the order they happened.
- **Proven against the live stores through the PURE finder, with no write and no daemon boot**:
  it records exactly 23 deletions across 8 routines (`scheduler-builder` 7, `llmsectest-weekday`
  4, `self-audit` 4, …) and leaves exactly 4 ids drifting. A test asserts the RELATION — what is
  left is only the `differs` class — so a new drift of a different class fails it while a
  reminder legitimately deleted tomorrow does not.
- **The 4 `differs` are deliberately left and NAMED in the record**: three lack `created_run`
  (the mirror era dropped it) and `rem-20261026-233255-1` disagrees on `owner` —
  `vodafone-outage-claims` on the stream against `self-audit` in the store. `owner` decides who
  may REMOVE a hook, so a migration may not settle it by guessing.
- A foreign hook is recorded origin=owner, addressee=holder, the same correction the backfill
  needed: passing the holder as both would turn a drift into a different drift while appearing
  to fix it.
- **Known limit: 4 ids still drift, and one of them is a question for the operator (which side
  is authoritative for `owner`), so the comparator does not read EMPTY yet.** Still open: D152
  step 4 / goal b4.

## [0.547.0] — 2026-11-14

### The seven attention classes are a FOLD, and who decides a row's class is answered — D289

- **The operator, 2026-11-12** (R3365, quoted whole): operator-facing text *"must focus on things
  that might require my intervention. this includes (a) context for things i might need to decide
  (b) unexpected events … (c) any form of outgoing or incoming communication, especially personal
  (d) reached goals (e) blocks that the system cannot resolve itself (f) methodological or
  strategic pivots (g) confirmed dead-ends"*. **His answer, 2026-11-14:** *"Lead with the seven
  classes as a labelled block at the top, narrative below it"*.
- **The open sub-question he handed over is answered, and MEASURED rather than chosen.** Does the
  engine ask a run to MARK its finding's class, or does the renderer classify prose? Measured on
  this instance: (a) 529 of 623 question records carry explicit `options`; (b) 2,618 health rows
  across 36 event types; (d) 146 `status.json` files carry goals with `met` and evidence; (e)
  `BLOCKED_EVENTS` plus a `partial` finish, which by its own contract IS a blocker the run could
  not clear; (c) partial — a reserved outward util is gated but no stream records that a message
  went out; (f) and (g) recorded nowhere at all.
- **So it is NEITHER option as posed:** `readmodels/attention.py` DERIVES the four classes the
  engine already records and accepts a run's MARK only for the three it cannot. Classifying a
  derived class from prose would replace a fact with a guess that could disagree with
  `operator_blocks` invisibly; letting a run mark one would make the label a run's opinion of its
  own work, on the very surfaces that exist because its prose does not say what needs a person. A
  mark in a derived class is **dropped**, with the specimen pinned (a run marking its own goal as
  reached).
- **Every row is self-contained**, which is his requirement 1 (*"worst case scenario is content i
  need to know about being referenced only by an identifier"*): an ask carries its options, a met
  goal its evidence, a partial finish its own summary — never just an id.
- **A repeated event is ONE row with a count**, found by running the fold over all 715 live runs:
  266 of 846 class-(b) rows were the same event filed again inside one run and one run produced
  30 rows. Collapsed, the fleet reads 747 rows over 379 runs, **median 1 per run, max 10** — a
  block that can lead a surface instead of burying it.
- **A blocking ask is both (a) and (e)**: he must decide AND the run cannot proceed. His list is
  not a partition, and forcing a row into one class would lose half its meaning.
- Order is his order, (a) through (g). The fold never raises: a missing, unreadable or
  half-written status reads as nothing to report.
- **Known limit: nothing renders the labelled block yet, and class (c) has no derived source —
  an outward act is gated but never recorded, so communication relies on a run marking it.**
  Still open: D289.

## [0.546.0] — 2026-11-14

### A decision model answers the same question beside the chat model, and both are recorded — D287 step 1

- **The operator, 2026-11-14:** *"Reroute the refusal classifier AND cascade the finish verifier:
  a typed screen over each claim, with the prose judge called only on the claims it flags (184
  calls, 24.5 s average, $11.15/month, all at the moment every run is trying to end)"*.
- **This increment reroutes nothing, deliberately.** Every option he was offered carried the
  clause *"measured in parallel before it replaces anything"*, because of what D287 measured on
  this instance's own models: permuting the options moved `jev`'s probability mass by ~10 points
  while the argmax never moved. A system reading only the verdict sees perfect consistency and
  learns nothing about the instability underneath — so the swap is not step 1; the evidence that
  the swap is safe is step 1.
- **And his 2026-08-22 constraint governs this path**, quoted in `engine/refusal.py`: *"only an
  LLM classification subcall (tool_call model, schema'd verdict) may decide the non-obvious cases
  either way"*. The chat model keeps deciding; `engine/decision_shadow.compare` returns its
  verdict **unchanged** and records what a decision model would have said.
- **Both sites his answer names are wired**: `refusal.is_refusal` (34 calls/month) and
  `verifier.refuted` (184 calls, the expensive one). The finish site screens **every claim in ONE
  decision call** — one evidence, many questions — because that is both what these models are
  fastest at and the shape the cascade will ship in, so the measurement is taken in the form it
  will be used.
- **The stream is `.control/decision-shadow.jsonl`**, durable and instance-wide like
  `workflow-usage.jsonl`: the question is whether two models agree over hundreds of calls, and
  run dirs fall to retention. Each row carries both verdicts, **the probability** (the argmax is
  what hid the instability) and **the threshold** it was scored against, so a later reader can
  rescore the whole stream.
- **A measurement may never fail a run.** No decision model configured, a provider error, a
  malformed answer, an unwritable stream: every path returns the chat verdict untouched. An
  unanswered comparison records `decision_verdict: null` and `agree: null`, never `false` — an
  absent verdict and a negative verdict are different facts, and conflating them would make the
  stream lie in the direction that looks safe. An instance with no decision model records
  nothing at all rather than one identical row per classification.
- Structured evidence goes through `decisions.evidence_text`, pinned here: 0.535.2 announced it
  in the `openai` adapter — dead code on this instance — and the live path stayed broken for nine
  days under a green pin.
- **Known limit: no reroute and no cascade yet, and the stream holds no rows until runs with a
  decision model configured exercise these two paths.** Still open: D287.

## [0.545.0] — 2026-11-14

### `websearch` and `code_search` are action kinds — D290, and they are SPELLINGS, not ports

- **The operator, 2026-11-14:** *"Make `websearch` and `code-search` action kinds, matching what
  callers already attempt — two more kind bullets in every routine's prompt, and the util catalog
  loses its two most-used entries to a second front door"*, chosen over three options that left
  the action schema alone.
- **The premise, measured before building:** `kind: websearch` / `kind: code-search` are 23
  rejected turns in 30 days across 12 routines and 3 conversations — the third-largest
  action-schema rejection class, written by `claude-sonnet-5` and `claude-opus-5` alike. Every
  one recovered inside its own turn (`global-utils-review:20261113-031130` turns 77/89/90:
  *"Correcting the call shape — websearch is a util, not a kind"*, then `kind="util",
  name="websearch", args=[query]`). So the intent behind the rejected shape was known exactly,
  and is what the rewrite implements rather than guesses.
- **`engine/kindaliases.py` rewrites an alias into the util action it spells**, inside
  `actions.normalize_action` — ahead of the schema validator, the capability layer, the secret
  gate and the telemetry seam. One implementation, one permission, one Stats row: a kind with its
  own executor entry would have been a second code path to the same util, which is how
  `background` once relocated a security decision (F633).
- **It runs before the FIELD STRIPPING, deliberately.** `ACTION_SCHEMA` is
  `additionalProperties: false`, so a payload in an undeclared field (`query`, `pattern`) is
  deleted during normalisation — a rewrite any later would run the util with an empty query.
- **`code_search` is the kind and `code-search` is accepted anyway.** No action kind may contain
  a hyphen: `tests/test_wrong_kind_diagnosis.py` pins that property because it is the
  discriminator that diagnoses every OTHER util-name-as-kind mistake. Since the rewrite precedes
  the enum, the hyphenated spelling 11 of the 23 rejections used never has to BE a kind to work.
- **An alias with no derivable payload is left alone**, so the validator names the missing field
  inside the schema-retry cycle (no turn) instead of running `gu websearch` empty (one turn).
  That decline is what makes the payload tolerance safe.
- **Availability follows `util`, not a new allowlist entry** (`kindsurface._permitted`): a
  workflow that may call utils may spell it either way, and one that may not cannot reach the
  util under another name. Making each alias its own `tools:` entry would have meant editing 31
  routines to keep exactly the capability they already have.
- **Two existing invariants were taught the new category rather than weakened**: every
  backgroundable kind must be runnable (`executor.DISPATCH`) — for an alias what must be there is
  the kind it BECOMES, because no path delivers an alias to a runner (`completion.judge`
  normalises the first action AND every chained element); and `unkind_diagnosis` no longer fires
  for `websearch`, which is now a real kind, so its specimen moved to `immich` — one word, no
  hyphen, not a kind, the same hole the hyphen heuristic cannot see.
- `engine/kindexamples.py` holds `KIND_EXAMPLES` (moved out of `actions.py`, at its ceiling, and
  re-exported); `transcript.js`'s `BRIEF_FIELD` copy is regenerated from the Python map.
- **Known limit: the two utils keep their catalog entries and nothing migrated to the new
  spelling.** Accepted permanently: the aliases are ADDITIVE by design — the kind rewrites into
  the util call, so both spellings are the same call and every existing one keeps working. A
  migration would change prose that is already correct, and removing the catalog entries would
  hide the usage a bare alias cannot express (the flags, the verbs).

## [0.544.0] — 2026-11-14

### A util can learn which DECISION models its routine reaches — D288 increment 1 (goal b6)

- **The operator, 2026-11-14: "Convert fully"** — `llm` and `decide` stop being action kinds and
  become ONE util with both capabilities (goal b13's constraint: two capabilities or none).
- **The asymmetry this closes is why `decide` could only ever be an ACTION.**
  `endpoints/routine_env` has injected a routine's INFERENCE endpoints into a declaring util's
  environment since it shipped (`RSCHED_LLM` / `RSCHED_LLM_KEYS`); the DECISION catalog had **no
  such injection at all**. The engine knew which decision model a routine reaches and a util did
  not, so a util could only hardcode one — which for a routine configured to a LOCAL model is not
  a cost problem but a privacy breach, the argument the inference half already makes.
- **`RSCHED_DECISIONS`** carries every configured decision endpoint and model, the role map, and
  **each endpoint's PROTOCOL**; **`RSCHED_DECISIONS_KEYS`** carries keys for the ROLE endpoints
  only — a blob of every endpoint key would route around the operator's own `secret:<NAME>`
  decision. Both live beside the inference pair in one module, deliberately: they are one design,
  and a reader who has understood one has understood the other.
- **The role map resolves as the engine does** — the routine's own binding first, then the
  instance default (`decision_model` / `decision_media_model`), which is
  `pick_decision_model`'s order. A util told only about the routine's bindings would reach a
  *different* model than the engine does for the same routine: a second implementation rather
  than a second front door. A role naming a model the catalog lost reads as absent, never
  substituted.
- **The protocol rides the metadata, and that is F888's lesson rather than decoration:** that
  defect cost nine days because both documented ways of passing structured evidence were dead on
  the default decision model and the 0.535.2 fix landed in the `openai` adapter, while all three
  of this instance's models speak `systemone`. A util that cannot see its protocol can only
  rediscover it by failing.
- `engine/exec_env._decision_env` folds it into `_extra_secrets`, reading with
  `getattr`/`hasattr` and failing open for the reason that module documents: it runs before EVERY
  util call, including the vision fallback, whose callers pass context stand-ins.
- **Behaviour is unchanged for everything that exists today**: only a util that DECLARES the vars
  receives them, and none does yet.
- **Known limit: the `model` util itself and the removal of the two action kinds are NOT built**
  — this increment is the injection they both need. Still open: D288.

## [0.543.0] — 2026-11-14

### A ledger decision's per-option proof reaches the Decisions page (R3417, D220's last step)

- `self-audit`'s ledger writer refuses to file a decision without a proof per option — one entry
  carrying `predicate`, `reads` and `category`, with the five states derived exactly as
  `proofs.answered` derives them, **mirroring this engine's own module deliberately so the two
  cannot disagree**. Nothing read it: `decisions_read` built its question items from id, options,
  status and detail alone, so a proof reached **no surface at all** and the Unproven band (F795,
  0.520.0) covered engine-filed asks but not the ledger decisions — the ones that sit
  answered-and-unbuilt for weeks.
- `_ledger_proof` lifts the map onto the item, **validated through `proofs.problems` rather than
  trusted**: the ledger is another routine's file, and a proof the engine cannot read is worse
  than none, because it would park a row in a band no predicate could ever clear.
- **`in_progress` stops hiding a row that carries a valid proof.** D220's closure named this
  sequencing and stopped one step short: dropping `in_progress` from `_DECIDED_STATUSES` outright
  would un-hide **156 proofless decided rows** nothing can ever clear — the F659 regression, where
  six already-addressed decisions came back as answerable cards and one was answered the OPPOSITE
  way. A row WITH a proof is the one case where "already building it" is checkable.
- Its answered state and answer TEXT come from the ledger's own `resolution`: an answer recorded
  IN a report is older than the report recording it, so the marker gate reads False and the row
  would render as an UNANSWERED card — and `proofs.unproven` needs `answered` to put it in the
  band at all. Without this the reader bought nothing.
- **Measured on the live ledger, before and after:** 5 rows carry a proof (D287, D288, D289,
  D290, D291 — four `in_progress`, every one answered by the operator and none built). Before:
  1 card, 0 in the Unproven band. After: 5 cards, all 5 unproven, each carrying his own words —
  and **0 of the 156 proofless decided rows leaked**.
- `web/decisions_read.py` crossed the 350-line bar, so the self-audit LEDGER half (its own status
  vocabulary, its answered-marker records, now its proof validation) moved to
  `web/decisions_ledger.py` along a boundary that was already there: **383 → 250 + 186**, with
  `_audit_decisions` re-exported so no caller changed.

### A release's declared remainder must be ADDRESSED to someone — `docs/limits.md` and its gate (R3421)

- Of nine remainders declared between 0.444.0 and 0.445.2, the ONE given a report id (R2844) was
  fixed **the next day**; two named a route; six had none, and two of those went wrong *silently*
  — a credential watch covering one provider of three looks like a credential watch, and a
  command "never run against a live instance" is the one artefact whose purpose is to be run
  against one. The mechanism that turns a known limit into a fix is being ADDRESSED TO SOMEONE,
  and the CHANGELOG addresses nobody: it is a record, not an inbox.
- **`docs/limits.md`** is the store for the one class that had none — known, UNDECIDED, unbuilt —
  stating its own contract: what belongs, what goes to `docs/designs.md` (decided) or to a report
  (a defect) instead, the six required fields, and how an entry LEAVES (graduates when decided,
  deleted when it stops being true). Registered in `docs_build.GUIDE_ORDER` beside `designs`, its
  deliberate opposite.
- **The twelve unrouted clauses were actually dispositioned**, written from the CHANGELOG's own
  words plus the line each needed and did not have: what would make it matter. One answered
  itself — 0.535.1's "the fast stage's 3,584 MB and a live routine run cannot both be guaranteed"
  is D291, which 0.542.0 shipped.
- **The gate** (`tests/test_limits_md.py`) reads the CHANGELOG for the declaration vocabulary and
  requires each clause to carry a report id, a decision/question id, a `designs.md` entry, an
  `Accepted permanently: <reason>`, or an entry in the store joined on a ≥25-char verbatim
  fragment AND the release. Both directions are pinned, and an entry whose fragment no longer
  matches is reported rather than left to rot.
- **Two defects in that scanner were found by running its TESTS rather than its functions**, and
  both would have shipped a check that lies in the direction that matters: the clause splitter
  truncated at the next marker, so "Known limit: … Not fixed, reported as R9001" read as
  addressed to nobody (the disposition vocabulary overlaps the declaration vocabulary, so that is
  the common case); and the vacuity test keyed dispositions by RELEASE, so 0.445.1's three clauses
  overwrote each other and its assertion passed on dict ordering.
- **Known limit: this release's own browser stage was not completed** — the gate's ui stage hung
  in finalisation twice on 0.542.0 without writing a result, and 0.543.0's candidate touches no
  `static/`, `.js`, `.css` or `tests/ui/` file. Not fixed, reported as R3428.

## [0.542.0] — 2026-11-14

### Both catalogs in the CAPABILITIES section become an index — goal b5 step 4, as a three-day trial

- **The operator's decision**, answering the measurement with option 3: *"Let's try 3 for three
  days and see how it goes"*. A **reversible trial** over two compositions, whose review is a
  dated obligation rather than a note.
- **The util catalog carries NAMES only** (`engine/utilcatalog`, split out of
  `engine/capabilities`) and **the sub-workflow patterns SLUGS only**. Measured through the real
  composer against the live library: the util catalog **6,058 → 1,276 chars (−4,782)** and the
  pattern block **4,230 → 484 (−3,746)** — **−8,528 chars on every turn** of every run and
  conversation holding `spawn`, taking goal b5 from 47% to ~55% of the 108,666-char baseline.
- **Why this needed his decision when steps 1-3 did not.** Those removed prose the engine already
  enforces or already stores, each justified by a 4-gram overlap measurement. Over **52 live
  `prompt-sections.json` captures**, every part of this section overlaps harness+schema by
  **0.0-5.4%** (util catalog 0.4%, patterns 0.1%): nothing here is a duplicate. What is left is a
  CATALOG, so cutting it moves a description one call away (`util name=search`, `util name=list`,
  the library's `workflows/<slug>`) and makes it cost a turn — the trade he ruled on in step 2.
- **What keeps it honest:** every NAME stays, the category grouping stays, a reserved util keeps
  its `[reserved — …]` annotation (not a description — the fact that decides whether a call is
  possible), and both headers say where the descriptions are. `vision`'s text-only note becomes
  MORE load-bearing, not less, and is pinned as such.
- `engine/capabilities.py` went **412 → 301 lines** by moving the catalog to the module whose
  subject it is, so it came back under the 350-line bar and **dropped its ratchet entry**.
- **Stated with the number rather than left implicit: b5's 90% is not reachable by removal
  alone.** The remaining always-on inventory is digest 26,547 · harness 21,487 · capabilities
  ~7,100 after this · workflow 12,164 (each routine's own recipe) · reports 11,837 where present.
  The rest is contracts enforced by the wire instead of recited, not more trimming.

### A reminder deletion reaches the hook stream, and the pre-mirror ids are backfilled (goal b4, D152 step 3)

- **The write-path defect, live rather than historical.** Measured over all 31 routines with
  `hooks/reminderproj.drift`: **122 drifting ids in 21 of 31 routines**. Of those, **23 exist as
  a hook and in NO store** — five from November, one deleted by the very run that measured it —
  because a routine deleting its OWN reminder wrote the store and no row, while every other act
  (the write, a target's say over a foreign hook, a curator's disposal) reached the stream.
  `remind_write.remove` now owns the act so the store write and the hook row are one, and
  `hookmirror.reminder_removed` records it as a later row about the same id. **`remind.py`
  SHRANK 605 → 599 lines** by delegating, which it had to: it sits at its size ceiling.
- **Why it mattered more than drift:** the campaign ends with the store SERVED from the stream,
  and a fold that cannot see a deletion re-raises patterns their routines removed. A reminder
  decides whether an action is HELD, so that changes what binds a run, silently.
- **The 95-id backfill.** Every store-only id predates the mirror (newest `rem-20261018-…`, the
  mirror landed 2026-10-19; 41 September, 54 October, none November), so the class is closed and
  `migrate_premirror_reminders` empties it at boot — a pure read-only core (`missing_hooks`) so
  the live-store property is testable with no write, the shape the report half proved.
- **What it deliberately leaves:** the 23 stream-only ids (deletions must stay deleted) and the
  4 `differs` ids — three missing `created_run` from the mirror era, and one `owner`
  disagreement (`rem-20261026-233255-1`), and `owner` decides who may remove a hook.

### The blocked-work fold reports two units: incidents counted, standing conditions measured (R3418)

- `readmodels/blocked_kinds` holds the judgement alone, with each writer's own latch quoted.
  **Six** of `BLOCKED_EVENTS`' fourteen are STANDING — `backup_stale`, `migration_due_soon`,
  `migration_expired`, `obligation_overdue`, `answer_undelivered`, `process_overhead_high` — and
  three that look standing are not: `trigger_capped` (one event per day AT THE TRANSITION, F276),
  `scheduler_tick_error` (once per failure episode), `postdeploy_failed` (once per process).
- A standing row now carries `standing_hours` and **no count**, because its writer files one row
  a day while one fact holds: counting those rows measured how long the daemon was up, so a
  nineteen-night backup gap read `19` — the same magnitude as nineteen distinct refused fires —
  and an instance that restarts often reported a *smaller* problem.
- The payload carries `incident_total` and `standing_rows` and **the `total` key is removed**
  rather than kept beside them, so no consumer can add two units by accident. `operator_total`
  is untouched, which is what the rail's blocked badge reads. The console branches on the row's
  `kind` rather than being compiled with event names.

### The fast suite's worker count follows the cgroup headroom there IS (D291)

- **The operator's decision**, asked which side gives when a release gate's FAST stage (3,584 MB
  of this container's 5,120 MB cgroup) and a live routine run (1,086-1,717 MB) cannot both fit:
  *"Reduce FAST_WORKERS while a routine run is active, trading gate wall-clock for coexistence"*
  — explicitly **not** the cgroup raise.
- Both halves were constants, so a live run holding the difference **refused** the gate (exit 3
  after up to `RSCHED_TEST_MEM_WAIT_S`) instead of running it smaller — and because the two fit
  SOMETIMES, that refusal read as a flaky failure rather than as a rule.
- The charge is now **derived** from the table `memgate`'s own docstring records: 930 MB
  controller + 610 MB per worker, rounded up to 256 MB, and never below the `serial` charge it
  contains. A pin **re-derives the old 3,584 MB at four workers**, so the new arithmetic cannot
  silently disagree with the measurement the constant stood on. `browsersource.fast_workers`
  then picks the largest count (floor 2, ceiling `FAST_WORKERS`) whose charge fits the headroom
  `memgate` already reads — `memory.max` minus what the cgroup HOLDS, never `memory.current`.
- **An idle box is untouched by construction** (4 workers, 3,584 MB) and a box without a cgroup
  ceiling is unchanged. The refusal message now names the count it settled on: "the fast suite at
  4 xdist workers" became a lie once the count is conditional, and an unreadable refusal is what
  sent R3376's author hunting a memory leak that did not exist.
- `tests/conftest.py` is over the size bar and may only shrink, so the whole `-n auto` decision
  moved to `browsersource.auto_workers`, the module that already owned both worker counts:
  **679 → 669 lines**, ceiling tightened. The host shrank while gaining the feature.

### Every run names its slowest tests, and the expectation is finally checked (R3419)

- `addopts` gains `--durations 25 --durations-min 1.0`. `pyproject.toml`'s own comment beside
  `timeout = 300` says the slowest legitimate test takes "well under a minute" — a **299-second
  blind spot** the deadline cannot see, and F739 lived in it: a test polling to its routine's
  inherited one-minute ask timeout cost **~120 s of every release gate the fleet runs** until
  someone noticed by hand, with no production code at fault. The per-stage aggregate cannot
  localise that: 179 s is the same number whether it is 2,150 tests at 80 ms or 2,148 at 25 ms
  plus two at 60 s. Measured before editing: `durations` appeared nowhere in the repo.
- No test is given a deadline and none is excused — a flat per-test cap can never go green (the
  30 s SIGTERM-grace test is legitimate) and a gate red on arrival is a gate someone deletes.
  It earned itself inside this release: the first scanner run named mypy at 19.12 s and vulture
  at 13.28 s, numbers the project had never recorded.

## [0.541.0] — 2026-11-14

### A workflow pattern's prose is held to the standing order too — the half 0.540.0 missed (goal b7)

- **The order** (the operator's, 2026-11-13): a recipe, pattern or instruction never names a util
  or its flags — it names the capability. 0.540.0 made that a CHECK rather than a sentence, fatal
  for library rules, permissions and playbooks and a note for live recipes. It did **not** cover
  **workflow patterns**, and a workflow's prose is what `create_routine` materializes into a new
  routine's recipe: one breach there is inherited by every routine ever built from that pattern.
- **One line** in `lint_workflow_py`, on the same shared `recipelint.tool_names()` predicate the
  other three surfaces already use, so the four cannot drift apart.
- **A hardening, not a repair**: the library's 16 workflows and 14 patterns are measured clean
  today with the shipped predicate, so nothing is breaching. **Red-first**: the new test takes the
  library's own `general-task.py` — which it first asserts lints CLEAN — appends one util call
  form, and requires the failure; without the `lint.py` line that assertion does not fire.

## [0.540.3] — 2026-11-14

### A revise leg no longer destroys the task leg's turn record (F895)

- **A narrow revise leg resumes with `max_turns = 12` and `write_status` replaces the whole
  `budgets` block**, so the task leg's `max_turns`, `window_turns` and `turns_left` were gone
  from the only file that held them. The sole trace was `base_turn`, which happens to carry the
  task leg's final cumulative turn — a number that looks like an answer and is not one.
- **Measured** (`config-optimizer`'s R3393, folded in by `scheduler-builder`'s R3404): **20
  leg-overwritten run dirs across 8 routines**. `drawerrr-irreversible-actions` has 6 of 6, so a
  consumer that filters legs out samples nothing and the routine is invisible; recovered from
  `base_turn`, its task legs ran 142/19/137/19/145/149 against a 150 ceiling.
  `moltbook-heartbeat` lost its 136/140 run while 127/133/132 survived — hiding exactly the
  window a ceiling question turns on. The opposite error was equally available and happened:
  `drawerrr-irreversible-actions:20261110-140149` was reported as a "GENUINE single-window
  ceiling hit, 154/150" when it was a 12-turn leg.
- **The window is CARRIED, not recomputed.** `history.prior_legs` reads the displaced block and
  `prior_counters` reseeds it as `prior_legs` — the mechanism that already keeps the util
  histogram and the integer counters cumulative across legs (F131/F132/F140) — and
  `turnwindow.status_budgets` emits it as `budgets.legs`, oldest first. A reader asking what
  ceiling the WORK ran under reads `legs[0]`; `max_turns` keeps meaning this leg's window, so
  every existing consumer reads exactly what it did before.
- A run that never resumed carries **no** `legs` key, so an ordinary run's status.json is
  byte-identical to before. The record is capped at `history.MAX_PRIOR_LEGS = 4` — the derived
  resume chain is three windows (`TOTAL_TURN_MULTIPLE`) plus the leg that re-armed it.
- **Not back-filled, and deliberately so:** the overwritten dirs no longer hold the figures, and
  reconstructing them from `base_turn` would invent a `turns_left` nobody measured. The 20 known
  dirs stay unreadable; everything from here is recorded.
- Nothing was added to `engine/run_context.py`: it sits at its 587-line size pin, so
  `budget_block` reads the carried legs off the ctx with `getattr`, as `reviseleg.active` does.

## [0.540.2] — 2026-11-14

### `process_overhead_high` stops flagging short correct runs (F894)

- **The alarm structurally flagged any SHORT run, however correct.** Two actions of a correct run
  are process BY RULE in every routine — the outcome script and the finish — so the arithmetic
  floor under `process_share` is `2/total`, and clearing the 0.27 bar from below required
  `total ≥ 8`. `overhead.py` put no floor on the action total and `overhead_reading.py`'s
  `MIN_RUNS` floors only the number of RUNS, one level up.
- **Measured, not inferred** (`routine-improver`'s R3390, folded to me by `scheduler-builder` as
  R3404; re-measured here over all 797 measured depth-0 runs on this instance): exactly two
  routines were over the bar — `freelance-hunter` (41-55 landed actions, median 0.356, a true
  reading) and `library-sync` (4-5 actions, median 0.500), the cheapest routine on the box, whose
  four actions are two task utils, the outcome script and the finish. 756 runs land 16+ actions,
  35 land 8-15, six land fewer: the floor touches the artefact and nothing else.
- **The metric penalised the fix.** `overhead.py`'s calibration table scored
  `library-sync (25) 0.583` — correct on 2026-11-06, when an unsatisfiable Done-when line made
  every clean run finish twice. `routine-improver` fixed that at source (commit 0cd3baa), the
  denominator shrank when the waste was removed, and the alarm then fired on 2026-11-13 while
  `syncs_pushed` went 58 → 83. The table now says so in place.
- **`MIN_ACTIONS = 8`, derived rather than chosen**, with a pin that re-derives it from the bar:
  `2/8 = 0.25 < 0.27 ≤ 2/7`. One lower flags a run that did everything right; one higher stops
  judging real runs. A sub-floor run is **set aside, not counted as healthy** — it leaves the
  window, `reading()` reports `too_short` and `min_actions`, and a routine whose every run is too
  short reads `None` (no row, no event, no digest line) instead of a median of nothing.
- An action total is read by SUMMING the run's own recorded counts, never recomputed from a
  transcript, so it cannot disagree with the `process_share` it was taken over; a legacy row
  carrying no counts reads UNKNOWN and stays in the window (all 797 rows here carry them).
- `tests/test_overhead_invariant.py`'s `_row` fixture wrote `task: 1, process: 1` — a two-action
  run, a scale no real run has. It now synthesizes counts summing to a realistic total, which is
  why the floor was invisible to a green suite.

## [0.540.1] — 2026-11-14

### `decide` with structured evidence works on the models this instance actually has (F888, really this time)

- **0.535.2 announced the evidence in the wrong adapter and the defect stayed live.** That fix
  put `EVIDENCE_HEADER`/`evidence_text` in `endpoints/decisions_openai.py`, reasoning from the
  `input` field's string-or-message-list ambiguity. But `list_models` reports all three of this
  instance's decision models — `gemini-decision` (the default), `jev`, `local-qwen-vl8b` — on
  endpoint `otari-decisions` with protocol **`systemone`**, so the `openai` adapter is never
  executed here. `decisions_systemone.body()` put the object raw into `state`, and the gateway
  went on refusing it.
- **Measured again on 2026-11-14 against the live daemon (0.540.0, no build skew):** a dict
  `evidence` → `HTTP 400 {"detail":"The provider rejected the request as invalid"}`, five times,
  under two question wordings. The byte-exact announced string built from that same dict was
  answered by all three models (`gemini-decision` P=0.068, `jev` 0.050, `local-qwen-vl8b` 0.000);
  prose was answered throughout (P=0.155). `jev` answers either form, so one form serves every
  model.
- **The fix:** `evidence_text` moves to `endpoints/decisions.py`, where both adapters import it,
  and `systemone`'s `state` now carries the announced text. The refusal is the gateway's, not
  one wire shape's — keeping the helper in one adapter is what allowed a green pin to coexist
  with a broken live path.
- **New pins:** `test_systemone_announces_its_state_rather_than_sending_a_raw_object`, and
  `test_every_protocol_announces_structured_evidence`, which drives off `DEFAULT_BASE_URLS` so a
  protocol added later must name its evidence field and announce. `test_decision_endpoints`'s
  jev wire assertion moves with the shape it pins.

## [0.540.0] — 2026-11-13

### Prose names the CAPABILITY, never a tool — the standing order, enforced (goal b7)

- The operator, 2026-11-13, reading a report of mine that priced a contract change at *"31
  routines and 14 patterns to migrate"*: *"I'm **flabbergasted** that you say "every recipe,
  pattern and instruction naming them must be migrated in the same release" because we should
  have a standing order **FOR MONTHS** saying that recipes don't mention utils by name"*.
- **The order was already written and bought nothing.** `engine/kindprose.py` states it for
  library rules; `scheduler-builder`'s own `recipe-authoring` permission states it for recipes.
  Neither is a check, so nothing failed when prose broke it — and the migration estimate that
  provoked him exists only because of that. What was owed is ENFORCEMENT, not the sentence
  again.
- `recipelint.tool_names()` is the one predicate: it matches the CALL FORM — `gu <name>`,
  `util name=<name>` — not a catalog of names, because `recipelint` may not read the library
  (its charter: `rsched validate` walks every routine and must not need the library or the
  network), and the call form is what a machine can be sure of from the document alone. A
  tool's word in ordinary prose ("the mail account", "a git history") is English and is NOT a
  finding; the catalog's own meta-verbs `util name=list` / `util name=search` are excluded,
  because telling a run to ask what exists is the OPPOSITE of naming a tool.
- **The two halves carry different severity, and the asymmetry is measured, not chosen.**
  Nothing the library holds names a tool today — every pattern, workflow, rule, playbook and
  permission, checked — so a breach there is NEW and **fails** `lint_rule_text`,
  `lint_permission_text` and `lint_playbook_text`, which is what stops the order being broken
  by whatever the library hands a routine at creation. **13 of 31 live recipes already name
  one (28 findings)**, so there it is a `recipelint` note: failing `rsched validate` for
  thirteen routines nobody asked to break is how a check gets switched off in a week.
- `tests/test_tool_names.py` holds both halves, the prose false positive, the meta-verb false
  positive the live fleet produced, and the library's cleanliness itself — so the day the
  library acquires a tool name, that test says so.

## [0.539.0] — 2026-11-13

### A reference says WHAT the thing is, not only its id (R3386, first cut)

- The operator, 2026-11-12, on every surface that needs his attention: *"they must be
  self-contained. they cannot have me hunting for content in other documents. i want all info i
  need in the text i have in front of me along with hyperlinks to the files / documents where
  the info is from. **worst case scenario is content i need to know about being referenced only
  by an identifier.**"* His own first cut: wherever the UI renders an id, it renders a link
  whose text is the thing's one-line subject.
- `reflinks.js` already turned every `F63`/`D14`/`R7` in rendered prose into a link — but the
  link's text was the id and its tooltip said *"jump to R3361 on the Messages page"*, which
  tells him where it is and never what it is.
- New `static/ref-titles.js` — ONE reader of item subjects for the whole console. **No new route
  and no new store:** `/api/items` already returns every row with its `title`. One read, cached
  for the session, `folded=1` so a row another report has taken over still resolves — its id
  goes on being NAMED in prose long after it leaves the open lists, and an unresolvable id is
  precisely the failure this ends.
- `refLink(id)` is now the ONE place that decides what a reference SAYS:
  `R4242 — the Decisions page is an inbox sorted by urgency`, clipped at 72 characters with the
  whole subject in the tooltip. **The id stays in the text** — he names items by id when he
  answers, and a link whose id he cannot read cannot be quoted back.
- Resolution is **synchronous by design** (prose renders before any fetch lands), so an
  unresolved id is exactly what it has always been — still a link, never a dead end — and fills
  itself in when the one read returns. That read is kicked off by the FIRST reference the
  console renders, so a page showing none pays nothing.
- `itemcard.js`'s "refers to" line uses the same builder, so the two places that render a
  reference cannot disagree. No new CSS class.

## [0.538.0] — 2026-11-13

### The state digest's history tails are bounded in CHARACTERS, not in lines (goal b5, step 3)

- **The digest is the largest section of the composed prompt** — 29,122 chars measured over 37
  live captures (`runs/<ts>/prompt-sections.json`, the boot capture R3341 shipped) — and four
  HISTORY tails carry 69% of it: the ledger (median 7,209, max 13,326), the `.memory/` index
  (median 2,666, max **23,069**), the notes tail (median 5,508) and the previous run's
  `result.md` (median 3,861, max 9,317).
- **Every one of them was already capped — in LINES** (`notes.tail` 10, `runledger.digest_tail`
  30, `notemeta.INDEX_SHOWN_MAX` 60). A note line is a paragraph and a ledger entry is a whole
  run summary, so a line cap bounds the COUNT and not the size: that is how a "10-line" notes
  tail reaches 6,352 characters.
- New `engine/digesttail.py` owns ONE number (`TAIL_BUDGET` = 2,500) with the measurement behind
  it, and the clip: a line-boundary cut carrying a marker that names how much was dropped and
  which file holds it whole. Wired into all four producers. `notes.tail` and
  `runledger.digest_tail` keep the **newest** history (a digest exists to carry what just
  happened); `notemeta.digest_blocks` and the composer's last-run-result keep the head, which is
  where an index and a summary state what they are.
- **Why a character bound is honest here, where it would not be for a contract:** all four are
  readable on demand and the digest says where. The clip moves a cost from every turn of every
  run to the one turn of the one run that needs the older half — and a marked block can always be
  read back in one action, which a silently truncated one cannot.
- **Not clipped, deliberately:** the working plan (a run that cannot read its plan re-derives it,
  which costs more than the bytes), the goals and accounting contract, the finish-line block —
  anything stating what the run must DO. This bound only ever shortens what the run can read back
  in full.
- **Measured on the live fleet, all 31 routines:** the four tails together go from a mean 19,053
  chars to 9,796 — **9,257 chars saved per digest, on every turn of every run**. The figure is
  conservative: the comparison read `result.md` raw on both sides, so the last-run-result clip is
  not counted in it.
- 9 tests; 3 verified RED against the unbounded code before the fix.

## [0.537.0] — 2026-11-13

### A one-shot migration converts all fourteen installed patterns (D283)

- The operator's answer to D283. `src/rsched/migrate_pattern_drift.py` (new), called from
  `cli_daemon`: `max_total_turns` −1 → 0, the three ladder keys, and `outcome-first` added —
  while any list a pattern deliberately NARROWED is left alone, which is the half that makes a
  blanket rewrite unsafe and is pinned by its own tests.
- The drift was re-measured rather than inherited: 14/14 on all three counts, and each live file
  is **exactly 116 bytes** from its seed twin — which the three changes sum to exactly.
- `getattr` throughout, pinned by the four-attribute boot stand-in test, because anything
  `cli_daemon` calls at startup meets that stand-in. `MIGRATION(expires=2026-12-31)` declared, so
  `tests/test_policy.py` holds it to an expiry.

### A created routine measures something from its first run (D286)

- The operator's answer to D286. The outcome contract is already DEMANDED before anything is
  created, so the measuring script is now scaffolded with it: `workflows/outcome_stub.py` (new)
  derives a runnable `scripts/outcome.py` from the `produces` lines.
- What it prevents: a routine born with an `outcome:` and no `scripts/outcome.py` records an
  outcome error at every run and its own settings page calls it broken on the day it was made
  (`readmodels/surface_outcome.py`).
- The stub REPORTS 0 for each measure rather than leaving the measure out, with a per-measure
  marker naming what a real count would read — omitting a measure makes the engine drop the whole
  reading as an error, which is the state D286 exists to end. Checked against the engine's own
  `outcome.parse` over the scaffolded script's real output, and against `surface_outcome` no
  longer emitting `unmeasured`.
- The person's own `produces` prose is compiled into the generated file, so a label holding a
  backslash or a triple quote is defused before it can make the scaffolded script raise at
  import.

## [0.536.0] — 2026-11-13

### The action kinds' prose carries conduct, not a third copy of the field contract (goal b5, step 2)

- **The operator's decision of 2026-11-13**, answering the choice this routine put to him in
  R3379: *"Cut the bullets to conduct only — drop every field recital the enforced wire already
  carries."* With one bound, which is part of the decision and not a caveat: **no field
  description is reworded.**
- **Why there was anything to cut.** The `Action kinds:` block glossed every kind's FIELDS — their
  defaults, caps and refusals. That was the THIRD statement of one contract in a single request:
  `completion.next_action` hands the projection to `endpoint.complete(schema=…)` where every
  transport enforces it with its own `description` prose in front of the model (a tool's
  `input_schema`, a strict `response_format`, Ollama's `format`); 0.535.0's `schemaindex.index`
  states which fields each kind takes; and then the bullets said it again in prose nothing
  enforced. Measured over 21 live run captures on 2026-11-12 (`runs/<ts>/prompt-sections.json`)
  the block averaged **21,421 chars on every turn of every run and conversation.**
- **What stayed, and why those two things.** Each kind's IDENTITY — `ACTION_SCHEMA["kind"]` is a
  bare enum, so the wire says a kind EXISTS and never what it is for — and its CONDUCT, which no
  schema can hold: *"ask sparingly"*, *"the moment you run something a SECOND time, write the
  util"*, *"it gives no reasons, so ask `llm` when you need one"*. Conduct is why the operator
  chose this over moving it into library rules: a routine holding no corresponding rule would
  silently lose the instruction.
- **Measured, and smaller than the estimate this routine gave him.** Bullet prose
  **23,853 → 16,841 chars (−7,012, 29.4%)**; through the real composer the harness section drops
  **6,631 chars on every turn** (`tests/prompt_budget.json` re-locked 65,220 → **58,589**, the
  reason in its `note`). R3379 estimated ~19k by counting the whole block as recital — it is not:
  by 4-gram overlap against the schema's own 85 field descriptions, only **2,416 chars** of the
  old block was ≥20% verbatim-on-the-wire. With step 1 the always-on contract a real run receives
  is **~25.2k chars, against ~31.8k before this release and ~65.2k before step 1.**
- `tests/test_kind_bullets_carry_conduct.py` pins the ARCHITECTURE rather than a char count: no
  bullet sentence is ≥25% the schema's own words (4-gram windows, so a paraphrase is caught as
  well as a quote), every bullet opens with its kind's identity, and nine conduct sentences that
  live nowhere else survive. It earned itself at once — it caught the `job` bullet's checkpoint
  sentence at 31% on the wire, which review by eye had passed.
- `docs/prompt-anatomy.md`: §5.1's bullet fence re-rendered from the composer, and the section
  table now says what a bullet owes and that the finish summary's two labelled parts are stated
  by the schema's `summary` description. Three needles in `tests/test_prompt_anatomy.py` moved
  from cut bullet prose to the field descriptions the engine really emits.

### A run transcript's rows carry ⧉ copy and 🐞 report-as-bug, and a `view_image` row says where the reading is (F217, R3357)

- **Copy shipped for conversations only.** A run transcript's messages carried no copy button, and
  nothing anywhere carried report-as-bug. New `static/components/row-tools.js` holds `copyBtn`
  (moved out of `chat.js` with its clipboard-rejection handling verbatim) plus the 🐞 dialog, which
  queues a **message** through the existing `POST /api/routines/{slug}/messages` — the same endpoint
  the routine page's inbox composer uses, pre-filled and editable, and absent entirely where no
  routine owns the mount. No report-filing route was invented; `src/rsched/web/` has none.
- **Two real defects were found by RENDERING the page, which nobody had done:**
  - `static/views/run.js` mounted the transcript without `runId`/`sub`, so every copy and every
    🐞 from a run row named only "routine uir, turn 1" instead of the run — the feature's whole
    purpose defeated silently.
  - R3357's "↳ shown directly to the model" pointer sat inside the **collapsed** result fold:
    invisible until expanded. That is the F559 defect R3357 exists to fix, reintroduced one element
    over, and missed by a test asserting `to_have_count(1)` on an element nobody could see.
- **R3357's half:** new `static/components/obs-image.js`. A `view_image` row's paths and thumbnails
  now ride the action element instead of the collapsed fold, and reading `paths` (not just `path`)
  fixes a batched `view_image` rendering an empty brief. The vision-util path renders the util's
  returned description; the DIRECT multimodal path renders a labelled pointer with a
  `data-turn-link` to the turn where the model's reading is — landed by the listener `turnlink.js`
  already installs over the run view, so frozen `views/run.js` needed no new wiring. No answer is
  invented into that cell; the engine half (recording the reading against the row) is deliberately
  not built.
- Five new classes in `static/obs.css` (48 → 99 lines, against the 350 bar); `views.css` and
  `base.css`, both at ceiling, are untouched. F433's `@media (hover: none)` rule carried across.
  Both files the fixes touched sit AT their ratchet ceilings, so both fixes are line-neutral.
- 15 browser tests (`tests/ui/test_transcript_row_tools.py`), including the 🐞 end-to-end
  assertion that `msg-*.json` really lands in the inbox, a both-widths test at 1280 and 390 that
  verifies button labels by COMPUTED TEXT (the blank-pill artefact of an unweighted face), and the
  new address test. Looked at, desktop and phone.

### The daemon samples ITSELF while a request is still slow (F871)

- **The console's 10-30 second waits are a blocked process, not slow handlers.** `/api/llm-tasks`
  — an `async def` returning an in-memory snapshot and doing no I/O — recorded a worst of
  34,396 ms, and unrelated endpoints finished within 20 ms of each other. The case no existing
  evidence explains is the QUIET one: **33 of 79 server-side slow rows had `in_flight = 0`** and
  still took a median 2,700 ms, 24 of them on `/api/health/blocked`, whose handler measures
  1.4 ms warm. `in_flight` cannot narrow that by construction (an http middleware never sees a
  websocket scope), and `/api/debug/threads` can — but only while somebody holds a browser open
  inside the stall, which at 18:07 nobody is.
- New `src/rsched/web/slowsample.py` owns the timing middleware (moved out of `app.py`, which was
  at its size ceiling, so the host SHRANK 350 → 324 while gaining the feature) plus `SlowWatch`:
  an in-flight register and ONE OS thread that wakes every 0.25 s and, when a request has been in
  flight past `app.state.slow_request_s`, writes `api_debug.thread_report(...)` — both pools and
  every stack — to `<routines_home>/.control/slow-samples/<ts>-<nnn>.json`.
- **Why a thread and not a callback, which is the whole design:** a blocked event loop runs no
  callbacks, so a sample taken in the middleware's `finally`, in a `call_later` or in any
  coroutine would be served only AFTER the stall released, against an already-drained pool —
  exactly the misreading `api_debug.py` records from 2026-09-12. The watcher holds no anyio token
  and no `io_pool` thread, so it cannot deepen the shortage it measures.
- **The one thing the thread cannot do is resolve anyio's limiter**, which comes from the running
  async context. So `api_debug.capture_limiter()` is called from `SlowWatch.arm` — on the loop,
  since `arm` is the middleware's own entry — and the sampler reads the captured object, whose
  `borrowed_tokens` is live from any thread. Without that the snapshot recorded
  `threadpool: {"error": NoEventLoopError}` and lost half of what it exists to show; the feature's
  own test caught it.
- Bounds: ONE sample per stall (`SAMPLE_COOLDOWN_S = 30`, because the measured failure is SHARED —
  every request waiting at that instant is pointed at the one snapshot), a 20-file ring pruned on
  write, 12 frames per thread, the `waiting` list clipped to 50, and a watcher that retires after
  8 quiet ticks. Every context read on the request path uses `getattr` and fails open.
- **The four sync handlers are untouched, for a measured reason, not caution:**
  `starlette/concurrency.py` shows `run_in_threadpool` IS `anyio.to_thread.run_sync`, the same
  40-token limiter — so rewriting a `def` handler as `async def` + `to_thread.run_sync` holds the
  same token for the same duration. The real alternative is moving them onto `ioload`'s `io_pool`,
  which re-derives that pool's live-stream ceiling; this self-sample is the instrument that says
  whether it is even the right move.

### An `ask_user` batch no longer counts a REFUSED question as filed (F893)

- `askbatch.handle` counted every entry of `filed`, refusals included, so a batch of two where
  one question was rejected *before it filed anything* still reported `"count": 2` — and the
  observation read "2 questions filed as one batch … They are on the user's Decisions page
  together". Nothing of the refused one was on his page. The run read the count, believed the
  operator could see both, and carried on.
- Measured where it happened: `self-audit:20261112-233155` turn 298 filed a two-question batch
  whose first carried a `config_patch` nesting its keys under `settings`. The engine refused that
  one correctly — and the observation still said `count: 2`, with the error buried inside
  `filed[0]`. The question (that run's whole fleet-wide decision-model nudge) was found missing
  70 turns later by reading `/api/questions`, not the observation.
- `count` and `blocking` now count records that EXIST; the refusals are listed apart under
  `refused`; and the observation carries a `refused_note` saying plainly that nobody was asked
  and the question is the run's to re-file THIS turn. Two pins in `tests/test_ask_batch.py`: the
  computation and the rendered prose. Built and serially gated by `self-audit`, which handed the
  candidate over in R3395 when its own release gate's browser stage stalled.

### The Decisions page stops offering a click the server refuses (F885)

- The operator clicked `apply it` on a `fau` recipe revision at 2026-11-10T09:22:33, was refused
  (*"fau:20261110-083642 is running again — recipe editing unlocks on a FINISHED run"*), waited
  93 seconds, clicked again and got the identical refusal; it succeeded at 09:31. Those two
  toasts are the **only two `error` events in 2,998 trace rows** across 2026-11-10 and 11-11,
  while `apply it` was clicked 13 times in the window.
- `api_recipe_revision.revise_refusal(server, rec)` is now the one source of truth: it returns
  the proposing run's state and the exact 409 detail `POST …/revise` would raise, and the route
  raises *from it*, so the terminal-state list exists once and the card's promise cannot drift
  from the server's answer. `with_revise_state` adds `run_state` + `revise_blocked` to a
  `recipe-revision` row, applied **per request** in `api_pending.list_pending` and deliberately
  not inside `decisions_read.open_proposals` — that read model memoizes on the queue directory,
  which does not move when a *run* finishes, so a state folded into the memo would pin `running`
  (there is a test for exactly that trap).
- `recipe-card.js` disables `apply it` **and** `edit` (same route) while blocked and renders the
  reason, worded by case: a live run *"unlocks when the run finishes"*, a pruned run dir
  *"cannot be applied from here"* — two different promises, so two different sentences.
- Known residue, named rather than hidden: a card rendered while the run was finished and clicked
  after it is resumed can still spend one click, because the Decisions band loads once at mount
  and its refresh is wired to no bus event. That is a page-refresh change beyond this item.

## [0.535.5] — 2026-11-13

### The sub-run card stops refetching once per replayed subrun event

- **The same replay storm as 0.535.4, second panel** (F892). `views/conversations.js:392` called
  `subRunTree.refresh()` for every `subrun_start` / `subrun_end` the tail delivered — and the
  tail replays the whole transcript from offset 0, so each HISTORIC subrun event fired an
  immediate `/api/runs/<run>/tree`. `views/run.js:533-534` had the same shape.
- **Measured in the same two trace days:** 827 client-stamped `/tree` slow rows at a 3,358 ms
  median, **676 of them (82%) under a second after the previous one**, and 695 falling inside
  the artifact bursts — the two storms are one view open, amplifying each other.
- `components/subruntree.js` gains a 1,500 ms coalescing `poke()` beside its `refresh()`,
  guarded on `box.isConnected` and the existing final `stopped` flag (and cleared by `stop()`,
  so a torn-down view leaves no timer). The per-event hooks poke; the TERMINAL-state refresh
  stays a refresh, because a finished run deserves one authoritative read.
- **Both pins discriminate, proven by removing the fix:** with the two call sites reverted, 12
  replayed writes cost **13** `/artifacts` fetches and 8 replayed subruns cost **17** `/tree`
  fetches; with the coalescing, 3 and 4. The liveness pin passes either way, which is what makes
  the other two meaningful.

## [0.535.4] — 2026-11-13

### Opening a long conversation no longer fires one artifact fetch per historic write

- **The console's biggest measured self-inflicted load, and it is a replay artefact** (F891).
  `components/chat.js` fires `onArtifact` for every `write_file`/`edit_file` observation landing
  in a deliverable dir, and opening a view replays the WHOLE transcript — so
  `views/conversations.js` called an unthrottled `artifacts.refresh()` once per historic write.
- **Measured in `.ui-traces` (2026-11-12/13, 6,983 events):** 25 bursts, the largest **70
  identical `/artifacts` fetches in 9.6 s**; latency inside a burst climbs from 3,894 ms to
  13,377 ms as the requests queue against each other, and **1,566 of 1,581** client-stamped
  `/artifacts` rows arrive inside one. Those bursts occupy **305 s of a two-day trace (0.18%)**
  yet carry **17.1% of every other endpoint's slow rows**, which run **39% slower inside them**
  (3,786 ms vs 2,721 ms median against 2,721 ms outside). `/api/runs/…/tree` contributes 695 of
  those rows.
- **The fix is the pattern the sibling card already had.** `components/fileactivity.js` has
  carried a 1,500 ms coalescing `poke()` since it was written — "many file observations arrive
  in bursts: one refetch per lull". `components/artifacts.js` now has the same, guarded on
  `listBox.isConnected` so a torn-down view never refetches, and both call sites
  (`views/conversations.js`, `views/run.js`) poke instead of refreshing. Each call site is
  line-neutral: both files are at their size ceiling.
- **Not a handler fix, deliberately:** `readmodels/fileactivity.file_activity` measures **66 ms
  cold / 1 ms memoized** on a 3.6 MB transcript, so the wait was never in the handler. This is
  the request-volume half of the UI's slowness, next to the server-side half F871 addresses.
- `tests/ui/test_artifact_poll_storm.py` pins both directions: twelve replayed writes cost at
  most three list fetches, and a live write still reaches the panel.

## [0.535.3] — 2026-11-13

### An engine note no longer contradicts the grant answer printed beside it

- **Three engine messages told one run three incompatible things within two turns** (R3380, from
  conversation `c-20261025-100140`, turns 812-814): the capability refusal said the refusal was
  *"permanent for this run"*; the grant answer said *"allowed permanently — recorded in the
  routine's config (and usable now)"*; and the config-patch ENGINE NOTE, **in the same
  observation**, said it *"takes effect at your NEXT RUN — you are still running under what you
  booted with … a live run's schema is already fixed"*. The send then worked immediately: `gu
  whatsapp send` exit 0 on the next turn.
- **Only one of the three was wrong for that case, and it was the note's reason.** A grant IS
  live — `engine/requests._seed` folds the decided entity into `ctx.granted_now` and
  `rebuild_policy` re-projects the action schema, so the next turn can use it, `allow_forever`
  included (the web layer additionally writes it to the config). The note is about a config
  EDIT, which really does wait, and `configflow.CLASSIFICATION` carried that carve-out for
  `machines`, `fs_read_roots` and `fs_write_roots` — *"an access-REQUEST decided mid-run DOES
  reach this one … while a config edit does not"* — but **not** for `permissions` and
  `capabilities`, which is the pair this run hit.
- Both reasons now carry it, naming `allow_forever` explicitly. The half stays `NEXT_RUN`,
  because a config edit does wait; what changed is the sentence a run reads.
- `tests/test_configflow.py` pins the class over all five authority fields: each must tell the
  run that a mid-run request reaches it AND name the config edit as the thing that waits. The
  refusal's own *"permanent for this run"* is untouched — it is true of the refusal, and
  `engine/denialmemo.py` already teaches the request route in the same breath.

### The post-boot chain check stops asking for a change the operator has declined

- `model-chain-depth` (0.534.4) reported *"61 role(s) across 31 routine(s) cannot fail over … a
  provider failure of a few seconds ends such a run at its first attempt"* at every boot. The
  operator **answered that question on 2026-11-12: "No fallbacks"** (decision D285, filed with
  four options and the measurement that all four configured endpoints share one host, so a
  fallback declared in this catalog would read as a failover it is not).
- A NOTE that keeps prompting for a declined change trains its reader to skip the checklist, so
  the check now reports the **count** and says the count moving is the news, naming the decision
  and its date. It stays a NOTE and still never moves the exit code. The docstring's old line
  that making it fatal is the operator's open call is replaced by what he decided.

## [0.535.2] — 2026-11-13

### Structured evidence reaches the default decision model at all (`decide`'s headline capability)

- **Every `decide` call carrying a dict or a list as `evidence` failed on this instance's
  DEFAULT decision model**, with `HTTP 400 {"detail":"The provider rejected the request as
  invalid"}` and nothing else. Measured against `otari-decisions` on 2026-11-13: a
  **62-character** dict was refused while a **2,600-character prose string** on the same
  subject was answered; a multi-line string was answered; a string merely *beginning* with `{`
  and continuing in prose was answered; and the identical dict content prefixed with
  `EVIDENCE (JSON):` was answered (P(yes)=0.96). `jev` answered the dict throughout.
- **The cause is the `input` field's own ambiguity.** `decisions_openai.evidence_text` dumped
  structured evidence into `body["input"]`, and that field takes *either* a string *or* a
  message list (this module's docstring records the published shape), so a string whose whole
  content parses as a JSON object or array is ambiguous and the gateway rejects the request.
  The systemone protocol was never affected: it sends the object raw as `body["state"]`.
- **The blast radius was larger than the obvious case:** `decideaction._gather` folds text
  `files` into a dict together with the evidence, so **every `decide` call that read a text
  file** met the same refusal — the two documented ways to pass non-prose evidence, both dead
  on the default model, which is one plausible reason the whole fleet made **1** `decide` call
  in 16,721 actions over 30 days.
- **The fix announces rather than rewrites:** a bare JSON document is prefixed with
  `EVIDENCE (JSON):`, which both removes the ambiguity and tells the model what follows.
  Ordinary text reaches the wire byte for byte — a prefix on every payload would silently
  change the evidence every current caller's model reads — and a caller's own string gets the
  same treatment when it is itself a bare JSON document, because a run that passes the text of
  a JSON file it read meets the identical refusal.
- `tests/test_decision_evidence_wire.py` pins all of it: eight shapes that must pass through
  untouched, four structured shapes announced with the evidence intact, the caller's-own-JSON
  case, idempotence, and the request body. The existing wire pin in
  `tests/test_decision_endpoints.py` asserted the old string and now asserts the new one with
  the reason beside it.
- **Live proof is owed and is named here rather than claimed:** the engine's Python is frozen
  at boot, so the running daemon still refuses these calls until it restarts on this commit.
  What is proven is the provider's acceptance of the announced form (measured by hand) and that
  the engine now produces exactly that form (tested).

## [0.535.1] — 2026-11-13

### The memory gate stops charging a serial test run for four workers it never starts

- **`tests/memgate.py` has a third price, `serial`, and `tests/conftest.py` charges by what the
  session will really start.** Until now every non-ui selection was charged `NEED_MB["fast"]` —
  3,584 MB, the four-worker fast stage's measured hold — whatever it selected. The 2.25 GB that
  figure covers above a bare controller *is* the four xdist workers, and a `-n 0` selection
  starts none of them. `scheduler-builder` measured the cost on 2026-11-12 (R3376): his release
  gate's FAST stage exited 3 after 1,201 s with `real_failures: []`, two more targeted calls of
  his went the same way, and **three of his child runs handed back three built items with no
  pytest evidence at all** across ~9 gate attempts — nothing wrong with any candidate.
- **The new figure is measured, and it is not the one the report proposed.** `memgate`'s table
  rested on 27 `tests` rows (0.79-1.20 GB), which suggested 1,280 MB. The gate's evidence now
  carries 119 rows with `held_peak_bytes`, and the 23 whose stage log is still on disk can be
  split by the worker count pytest printed into its header: **21 serial rows hold 876-2,700 MB
  (median 1,965)**, the gate's own 5 `fast-serial` re-runs 851-1,317 MB, and the two 4 GB rows
  in the same label are **2-worker whole-suite runs (6,927 items), not serial at all**. So
  `serial` is **2,816 MB** — the worst measured serial hold rounded up to the next 256 MB — and
  1,280 would have admitted a selection holding more than twice what it waited for, which is
  the same class of defect as the 2,048 MB budget this module corrected on 2026-11-10.
- **The discriminator is what xdist does, not what was typed:** `-n 0` leaves `dist == "no"`
  and `numprocesses == 0`, while the default `addopts` selection resolves to `worksteal` with 4
  (measured on this box, pytest 9.1.1 / xdist 3.8.0). The browser suite is still priced by its
  marker expression, because its cost is the Chromiums it drives rather than the workers.
- **A refusal now names the selection it priced** (the report's second ask): "the fast suite
  needs 3584 MB" printed on a two-file `-n 0` call read as a memory leak in the candidate and
  sent its author looking for one. `SELECTION_PROSE` gives each priced label its phrase, and a
  test asserts the two mappings cover the same keys so a future price cannot ship without it.
- Three new tests in `tests/test_memgate.py` pin the discriminator over all three selection
  shapes, the serial budget against `MEASURED_SERIAL_HOLD_MB`, and the refusal's wording.
  `tests/conftest.py` was at its 679-line ceiling, so its change is exactly line-neutral.
- **Not fixed here, and it is an operator question:** even priced correctly, the fast stage's
  3,584 MB and a live routine run cannot both be guaranteed inside this 5,120 MB cgroup — the
  report measured a run holding 1,717 MB (3,271 MB left), while with a run live during this fix
  the cgroup held 1,086 MB and left 4,033 MB. So the collision is real but intermittent, and
  the choice between a larger cgroup and fewer `FAST_WORKERS` is his.

## [0.535.0] — 2026-11-12

### The action contract stops being sent twice (goal b5, step 1 of the prompt cut)

- **The ACTION SCHEMA section is now a compact FIELD INDEX wherever the wire enforces the
  schema** — `engine/schemaindex.py`. `completion.next_action` already hands
  `loop.action_schema` to `endpoint.complete(schema=…)`, and every transport enforces it with
  its descriptions in front of the model (the anthropic adapter as a tool's `input_schema`,
  `openai_compat` as a strict `response_format: json_schema`, Ollama as `format` — real
  constrained decoding), so the contract was travelling twice in one request. Measured over 21
  live run captures (`runs/<ts>/prompt-sections.json`, R3341's boot capture): the composed
  prompt averages **108,666 chars (~31k tokens)** and the dumped schema section **28,189** of
  them, 26,143 being description prose against ~7,800 of structure. What the prompt still owes
  is the one thing the FLAT schema cannot encode — `ACTION_SCHEMA`'s only `required` entries are
  `say` and `kind`, so nothing in it says which fields belong to which kind — and that index,
  read off `actions.KIND_FIELDS` (the map `validate_action` itself uses), is **1,442 chars for
  all 34 kinds**. End to end the section goes **35,246 → 1,855 chars**, on every turn of every
  run. No description was reworded: description wording swings tool selection by more than 10×,
  so a bulk rewrite is the one edit this change does not make.
- **The full dump returns whenever nothing else states the contract.** `schemaindex` asks it of
  the `main` chain's every member transitively, because `degrade` advances down a chain mid-turn
  and the composed prompt can never be rewritten afterwards (it is a caching contract, and a
  cold window of this size has cost over $100 on this instance). An endpoint at `schema_mode:
  none` or `json_object` requests no grammar, so there the prompt stays the whole contract;
  anything unreadable — no registry, an unresolvable role — falls back the same way. The answer
  is cached on the run context, so the chain is resolved once per leg.
- **…and when a turn is served with neither, the engine appends it.** `turntakeover` asks
  `schemaindex.turn_needs_the_contract` at both of its branches and posts the full projection
  ONCE as an ENGINE NOTE — for the uncensored takeover, for an unenforced endpoint, and for the
  repeat-streak grammar shed (`loop._schema_off` / `_shed_schema_turns`). That matters here
  rather than in theory: on this instance `uncensored` is served by `otari-openai-plain`, whose
  `schema_mode` is `none` precisely because its model refuses `response_format` — so weighing
  that chain at composition time would have answered "unenforced" for every routine and saved
  nothing. Sent once per run, because an action definition must never be unloaded after a run
  has used it.
- A field the model still gets wrong is taught at the moment it is wrong — `schema_guard
  .retry_message` with `schemamemo.field_help` already quotes the schema's own words for the
  named field, inside the retry cycle that existed before this. The adherence baseline it is
  measured against: 242 `wrong_turn_detected` rows over 2026-10-19..11-12, 219 schema-rejected,
  **median turn 66** with only 13 at turn ≤ 1 — a long-context failure — and the top causes are
  missing `say` (20) and invalid `kind` (18), the universal fields the index states explicitly.

### A refused `supersedes` now says which row IS live (R3364, from `rules-review`)

- `report_threads.supersedable` returns a clause per id instead of one shared reason — `is
  unknown to the ledger` / `was retracted` / `is folded into R<n> — the live row; name that
  instead` — and the folded clause follows the fold chain to its END, so a caller is never sent
  to an intermediate nobody is working. `owner_of` was rebuilt on the same walk, so a row's
  owner and the live row offered to a caller cannot disagree. The reporter named this the better
  of its two options because a caller learns the precondition only at the moment of refusal.

## [0.534.5] — 2026-11-12

### Removed
- **Two converged one-shot migrations and the two readers they needed, eighteen days before
  their markers would have redded every gate on this host.** `tests/test_policy.py` turns an
  expired `MIGRATION(expires=…)` marker into a FAST-stage failure, so the date is not advisory:
  on 2026-11-30 every release gate here — mine and the builder's — would have failed on files
  the candidate never touched. The daemon's own `migration_due_soon` event had fired five times
  naming the same six markers.
  Both migrations have converged, and their records say so: `.control/migrations/converse-goals.json`
  (2026-10-08, the `converse` pattern's goals revision, which the add-only seed sync could not
  carry) and `.control/migrations/run-records.json` (2026-10-08, 1,331 runs rebuilt, quality
  recovered for 967, with everything unrecoverable named per field). The records stand; nothing
  re-derives them. So `migrate_converse_goals.py`, `migrate_runrecords.py` and the two readers
  written only for it — the instance's own git history and what a past run's files say about it —
  are deleted, with their three test modules and both `cli_daemon` call sites, exactly as their
  own docstrings said they would be ("all three go with the migration").
  Seven files removed, ~700 lines. Every piece of prose that cited them is rewritten to say what
  the backfill DID rather than to point at code: four source docstrings, `tests/helpers.py`,
  `tests/test_accounting_floor.py` (where the citation carries the reason the accounting ruler
  must not be changed — that reason is kept), one console comment, and the two guides. One of
  those rewrites still backticked `RECORDED_SINCE`, an identifier that went with the module, and
  `test_doc_claims.py::test_every_backticked_identifier_still_exists_in_the_source` caught it —
  which is the second time this week a guide-claim test has caught a stale citation in prose a
  human had just written.

## [0.534.4] — 2026-11-12

### Added
- **The post-boot checklist now asks whether the fleet's model roles can fail over at all** —
  a fifth check, `model-chain-depth`, reusing `conformance.chain_problems` unchanged.
  That answer has been computable offline and free since R2196, and its only caller was
  `rsched validate` — a command somebody has to type. `daemon/postdeploy_watch`'s own docstring
  says what that is worth: *a check that runs only when a human types a command is a check whose
  silence means nothing.*
  What the silence cost, measured today: `deficit-coach:20261111-230002` died with "the model
  chain was exhausted after 0 rungs" on a provider connection refusal, and the previous
  `self-audit` run died the same way hours earlier. `engine/failover.next_after` searches
  strictly AFTER the failed member, so a chain of ONE dies at its first failure — and 12 of the
  13 catalog models declare `fallbacks: []`. The retry budget is real but short (3 attempts,
  exponential), so a provider 503 lasting seconds is enough to end a run.
  **A NOTE, never a FAIL, and deliberately so:** `cli_validate` already reports these lines
  without counting them, because whether a single-member or single-domain chain is acceptable is
  a judgement about THIS instance's providers — a one-provider instance has no other domain to
  reach — and a boot check that went red over it would be switched off inside a week. Making it
  fatal stays the operator's call. What changes is that the verdict reaches
  `.control/postdeploy.jsonl` at every boot, with the offending roles named per routine, where
  it is read without anyone asking. Disabled routines are walked too: one enabled tomorrow runs
  on the chain it has today.
  An unreadable catalog reports itself as unread rather than as a pass — `chain_problems` skips
  any role whose model is not in the catalog, so an instance whose catalog failed to load would
  otherwise report zero problems, indistinguishable from a healthy fleet. (That is not
  hypothetical: a sandboxed reader got "0 chain problems" out of a config it could not read
  while building this.) `record_row` already stored verdicts by name, so the row shape is
  unchanged; one test that hardcoded four verdicts now derives the count from `CHECK_NAMES`.
  **Not verified live, and worth knowing:** a routine's sandbox cannot read `config.yaml`, so
  the real catalog could not be walked from the run that built this — the check returned its own
  "catalog could not be read" NOTE, which is the behaviour above working, not a reading. The
  daemon process can read it; the first real verdict lands in the record at the next boot.

## [0.534.3] — 2026-11-12

### Fixed
- **All fourteen shipped settings patterns dropped `outcome-first` from every routine they
  created.** A pattern's `rules` list REPLACES the computed defaults rather than adding to them:
  `workflows/scaffold` builds `rules: active_rules` (DEFAULT_RULES ∩ library + the workflow's
  includes) and then spreads `patterns/apply.routine_yaml(settings)` over that config, where
  `rules` is one of the copied keys. Every one of the fourteen omitted `outcome-first` — **and
  only `outcome-first`**, the other six defaults being present in all fourteen — because the
  patterns were written in `307e127b` before that rule existed and nothing ever compared the two
  lists. `git log -G` on the installed `project-steward.yaml` shows exactly one commit touching
  those lines, so nobody had narrowed it by hand either.
  The consequence was live and measurable: of 26 routines on this instance 25 hold the rule, and
  the one that does not — `drawerrr-irreversible-actions` — is the newest and the only one born
  from a pattern since the rule was added. The rule it lost is the one that makes a run answer
  for what it PRODUCES.
  All fourteen seed patterns now carry it, and `tests/test_pattern_default_rules.py` pins every
  shipped pattern's rules against `DEFAULT_RULES` (plus the other half of the same guard: a
  default rule the seed library ships no document for would be filtered out by `scaffold` and
  reach no routine anyway). Its sibling `test_patterns.py` has pinned exactly this shape for
  budgets since it was written; rules never had the equivalent. A new module because that file
  is at its size ratchet ceiling.
  **This reaches patterns installed from now on, NOT the fourteen already in a live library:**
  `bootstrap.sync_seed_library_docs` is add-only by design and never syncs content drift, so on
  an existing instance the stale lists stand until a one-shot migration converts them — which is
  the operator's call, because the library is his to edit and its repo pushes on commit. Filed
  as an audit decision rather than done silently here.

## [0.534.2] — 2026-11-12

### Fixed
- **The `closes` refusal cost 11 routines 22 turns by naming only the moves a REPLY can make.**
  Measured over `.control/health-events.jsonl`: 22 `wrong_turn_detected` rows on this one
  sentence across 11 routines and 18 runs since 2026-10-19 — and three runs earned it twice or
  three times *within a single run*, so it was not teaching on the second reading either. Of the
  13 whose transcripts survive, only 3 carried `supersedes`: **ten carried `target` + `closes`
  alone**, a run ORIGINATING a notice it wanted nobody to reply to. The old wording stated the
  rule (which is the operator's, D196, and is unchanged) and left that run with nowhere to go,
  because every face it named — `answers`, `settles` — belongs to a reply.
  Both entrances now name the move for an originating notice: **leave `closes` out.** The
  action layer adds why that is not the loss it looks like — the row is then the recipient's to
  dispose of, which is what puts it in front of them, whereas a closure is deliberately kept out
  of the report trigger's inbox predicate (`daemon/triggers`, `include_closures=False`), so
  marking one would hide the notice rather than deliver it quietly. The grammar, the phrase
  `engine/schemamemo` parses out of the message, and every existing refusal all stand.
  `tests/test_report_closes_refusal.py` pins the measured shape at both entrances; proved red
  against the old messages before shipping.

## [0.534.1] — 2026-11-12

### Fixed
- **`uv.lock` was four commits behind `pyproject.toml`, and nothing in the suite could tell.**
  0.531.4 added `websockets>=13` as a real dependency — `web/api_screen_view.py` imports it at
  module level, behind no try/except — but the lock was left at its previous resolution. A
  container rebuild syncs the environment from the LOCK, so the committed tree declared a
  package no rebuilt environment would install: the daemon kept working only because the live
  venv had `websockets` from an earlier hand install. `uv lock --check` was red on the committed
  tree and green on the re-locked one, which is the whole proof.
  The lock is re-locked (two lines: the `websockets` requirement recorded against `rsched`), and
  `tests/test_quality.py` gains `test_uv_lock_matches_pyproject`, which runs
  `uv lock --check --offline` through the same `_gate` helper as ruff/mypy/vulture — so the
  manifest and the lock can never disagree silently again. `--offline` keeps it network-free:
  it compares recorded requirements and resolves nothing new. The node was proved in both
  directions (green on the re-locked tree, red on the committed one) before shipping.

## [0.534.0] — 2026-11-11

### Added
- **"Undo this run" on the run page (⤺) — D269's second increment.** The first covered the
  unversioned case in 0.530.0 (a conversation's per-reply artifact snapshots); this is the
  versioned half, and a different mechanism for a reason: a routine's directory IS a git repo
  autocommitted at every run end, so the material to undo is the run's own commit.
  Three bounds, each load-bearing. It reverts the run's **deliverables and recipe and never
  `state/`** — that is what the NEXT run reads, so reverting it would make the next run repeat
  the work or act on a world that no longer exists. It is **REFUSED when a later commit touched
  the same paths**, naming them, because a revert that silently drops someone else's later edit
  is worse than no undo (these repos have several writers: the routine's runs,
  `routine-improver`, you by hand) — and the check is per PATH, so an unrelated later commit is
  not in the way. A **resumed run is several commits**, one per leg, and all of them go
  newest-first; undoing one leg leaves a state nobody can reason about.
  It REVERTS rather than rewrites: the run's work stays in the history, the undo is itself a
  commit and itself undoable, and nothing already pushed needs a force-update. The control reads
  its plan BEFORE offering itself, so a refusal arrives as the server's own sentence and the
  confirmation names the actual files, the commit count and the `state/` files it leaves alone.
  New `engine/rundo.py` + `web/api_run_undo.py`; 11 unit tests over a real git repo.

## [0.533.0] — 2026-11-11

### Added
- **Clicking a coloured prompt segment in the run rail opens its actual TEXT (R3341).** You
  asked for it; the honest answer needed two halves. `history` and `observations` are the
  message list, which IS on disk, so they are REPLAYED from the transcript through the engine's
  own renderers and labelled *"what was sent"*. The always-on sections had no recorded text
  anywhere — and re-composing one at read time is unsafe, not merely approximate: the path that
  builds the digest drains a shared store, touches the job ledger and writes `answer` transcript
  events, so a GET endpoint that re-composed would mutate state on every tap. So, on your
  authorization, `engine/promptcapture.py` records the section texts ONCE per leg in the run dir
  (~60-250 kB), and the view labels them *"what is being sent — this leg's composed section"*. A
  run booted before the capture says *"text not recorded"* for those sections, which is the one
  honest answer for it. Nothing changed about what is sent to a provider, and
  `tests/test_prompt_segments.py`'s byte-identity hold is untouched.
  Rows carry the turn the transcript stamped and token figures that SUM to the segment total the
  bar draws, so the view is auditable against the bar; the body of one message is a second call,
  because these segments run to hundreds of thousands of tokens and you read this on a phone.
  New `GET /api/runs/{id}/prompt-segment` + `readmodels/promptsegtext.py`; the click is a `btn`
  on the legend row (a 0.5%-wide rect is not a thumb target) reusing `panel`/`pre.doc`, so no new
  CSS class. The panel closes itself when you leave the run — a browser test caught it staying
  open on another page.

## [0.532.0] — 2026-11-11

### Changed
- **A conversation over its prompt floor now STARTS, and the engine's first act is a hard
  compaction that states what it dropped (D223/F539).** The operator's answer reverses D223's
  own first one (a health event only, 0.479.0): `prompt_floor_over_window` had fired nine times
  for a single conversation, climbing 1,063,175 → 1,138,237 tokens against a 1,000,000-token
  window, with 1,333 turns on disk and no way to add to the thread. `engine/promptfloor.check`
  — same call site in `boot`, before turn one — now elides the MIDDLE via
  `compaction.maybe_compact` and trims oversized bodies via `clamp_to_cap`, then states the
  loss through `enginenote.append`: the next turn's model reads it in the prompt, and the
  conversation page and run transcript render it as an engine note. It compacts against the
  catalog window minus output+schema and reserves 400 tokens for the statement, because
  compacting to the exact ceiling and then appending the note put the prompt back over. A floor
  no compaction can rescue records `fits: false`, says so once, and still starts the run — no
  boot may fail here, and no refusal was reintroduced.
- **Every report surface now READS the hook stream; `reports.jsonl` is its projection (D152
  step 9, the campaign's "replace last" half).** `reports.read_reports` — the one seam ~20
  modules read through — folds `.control/hooks.jsonl` (`hooks/reportread`); no caller changed.
  What the step cost was the history: 8 rows filed on 2026-10-19, between the mirror and the
  source, had hooks carrying no disposal, so six settled exchanges would have read as OPEN and
  two folds would have been lost. `migrate_mirror_hooks` recomputes exactly those records
  through the declared inverse, refusing to guess where an `(id, event)` key names two rows.
  One read rule, so no reading can lose a report: the stream decides, and when the ledger names
  a report the stream's fold does not, the ledger is served and the shortfall is filed — a
  hand-written `reports.jsonl` keeps working. The two decisions moved onto the stream in steps
  7-8 reverse direction with the read: the stream's answer is acted on, the projection is the
  cross-check.

### Added
- **An `llm` call can name any endpoint you configured (F868, R3338, goal b7's engine half).**
  `model: "<endpoint-name>/<model-id>"` — the endpoint half must be a name from your
  `endpoints:` config, the model id is free and needs no catalog entry, which is exactly what
  the cloud-only catalog made impossible. A bare URL is refused with an error naming the
  configured endpoints: the `llm` action runs in the engine, not in the util jail, so a URL
  there would make the network destination a model's own choice. `list_models` now lists the
  configured endpoints; a spec on `spawn`/`subtask` is refused with a reason naming `llm`.
  A util learns the same through new `RSCHED_LLM` (every endpoint, and which one each model role
  resolves to) and `RSCHED_LLM_KEYS`, injected under the declared-only gate, keys scoped to the
  routine's ROLE endpoints so every other key stays behind its own `secret:` grant row. It
  exists because a privacy constraint — read these scans with a LOCAL model, never a cloud one —
  could not be expressed through the engine's own model channel at all.
- **A routine DRAFT that names a path the engine owns says so before the routine exists
  (D281).** `create_routine`'s preview now carries `reserved_paths` + `reserved_warning`
  (`engine/draftlint`), scanning the instruction, `done_when`, `never`, `setup` and
  `finish_line` against the engine's own reserved list, read from `engine/storeseal`'s constants
  rather than restated. It WARNS and never refuses — the paths have legitimate alternatives, and
  a draft exists to be corrected. The failure it ends: a new routine put its obligations
  register at `state/obligations.json` and met the refusal on its FIRST run while seeding state,
  with its own Done when pointing at a file that cannot exist.
- `hook_stream_unreadable` — the report read fell back to `reports.jsonl` because the hook
  stream holds no report rows (12 h cooldown: the read runs per request).

## [0.531.4] — 2026-11-11

### Fixed
- **`websockets` is declared.** `web/api_screen_view.py` imports it for the noVNC relay and it
  was declared **nowhere** in `pyproject.toml` — arriving only through `uvicorn[standard]`'s
  extra, which is exactly the silent-break case the deps list's own comment four lines above
  declares `pydantic`, `markdown2`, `cryptography` and `py-vapid` against. A bump that repinned
  or dropped that extra would have taken every screen in the console with it. The tree's ONLY
  `except ImportError` guarded that import, and its own comment admitted the branch cannot be
  taken (`# pragma: no cover - uvicorn[standard] ships it`) while the library was used
  unguarded two lines later, so the guard was deleted and the import moved to the top, where
  mypy and the import lint can see it. Found by the deep cruft sweep; `uv.lock` already pinned
  it (17.2).
- **A path a source docstring names must exist — and one did not.** `web/assetskew.py` told
  every reader that the build-skew verdict is rendered by `static/components/notices.js`, a
  file this repo has never had; the renderer is `skewbanner.js`. `test_doc_claims.py` has
  proved this for `docs/` for weeks, and nothing proved it for the prose *inside the code* —
  the new `tests/test_prose_paths.py` does, over source docstrings and console comments (385
  citations across 556 files, now zero missing), with the detector itself pinned on the real
  defect's own sentence so a dead pattern cannot pass as a clean tree.
- **`hooks/store.py` stopped telling the reader that a defect in it is harmless.** Its
  docstring still said *"Nothing reads this stream to make a decision yet"*, which was true
  when the mirror landed and false since campaign step 7: `hooks/reportinv.open_threads_now`
  answers the open-thread cap from this stream and files a `hook_stream_diverged` health event
  when the two disagree, and `reports.py` says from the other side that the hook has been THE
  SOURCE since step 4 — a hook that cannot be written now refuses the filing. A reader who
  believed that paragraph would weigh a change here far too cheaply.

## [0.531.3] — 2026-11-11

### Fixed
- **The answer field's placeholder is no longer clipped on a phone** (R3334, found by
  `scheduler-builder` by RENDERING at 390px — no attribute test can see it). `answerform.js`
  builds its field with `rows="1"`, and at that width the hint wraps to two lines: **measured,
  the field rendered 39px tall while its placeholder needed 64px**, so the reader saw
  *"your answer… (Ctrl+Enter"* and the word *"sends)"* was cut off. The field is now two lines
  tall inside the existing ≤860px block (`.row > .answer-input { min-height: 4.2em }`) — CSS
  rather than JS, so the shared control is untouched and F238's full-width mobile rule stays
  intact. Two rows rather than auto-grow: a height that changes as you type moves the buttons
  under your thumb.
  Not cosmetic in the way a cut label usually is — the placeholder exists to teach the send
  chord at the moment of use, and the chord changed today. The browser test measures the
  rendered box against its own `scrollHeight` (proved red-first at 39 vs 64px), so a font
  change that re-breaks the clip cannot pass it; a fixed pixel expectation could.
  **The surface is the RUN view's live-question card**, which takes the default placeholder —
  the Decisions page passes its own short hint with `control: "input"` and does not clip, which
  the test now says out loud so nobody re-aims it there.

## [0.531.2] — 2026-11-11

### Fixed
- **A slow request measured across a SUSPENDED tab now says so, instead of entering the store as
  a three-minute server wait.** The `load` kind got this guard in 0.525.5 (F863); the `slow`
  kind did not, and the first two days of production rows show why it needed one: **24 client
  rows claiming 106,205-183,081 ms were all REPORTED inside one 3-second window** (2026-11-10
  12:35:58-12:36:01 UTC, every one status 200) — a backgrounded browser flushing its queue on
  wake, with `performance.now()` having counted the time the laptop was asleep. **No SERVER row
  anywhere in the same store exceeds 16,790 ms**, which is how the artefact was identified. An
  optimization reading the unfiltered client maximum would have been measuring sleep.
  `performance.now()` keeps running while a tab is hidden — it is a monotonic clock, not a work
  meter — so the duration cannot be corrected, only DISCLOSED: the row is kept and **marked**
  (`, backgrounded (wall clock, not server time)`) rather than dropped, because a slow request
  nobody was watching is still evidence about the server, and discarding it would bias the store
  toward whatever the operator happens to be looking at. The visibility flag is latched, as the
  load guard's is, since the tab is usually visible again by the time the response lands.
  Two browser tests: a hidden tab's row carries the marker (proved red-first — the unguarded
  build wrote `1814ms, status 200, 1 streams open`), and **a visible tab's row does NOT**, which
  is the half that would otherwise let a guard marking everything pass both tests.

## [0.531.1] — 2026-11-11

### Fixed
- **An escalation dispatch no longer tells the supervisor the worker answered three questions
  nobody asked it** (R3320/R3325, filed twice by `fau`). The rung's brief said the dispatch
  carried "its own answers to 3 questions", and `worker_says` was `turn_records[-3:]`'s `say`
  lines — a SUBSET of `build_dispatch`'s `says`, which already carries every line of the
  interval. So the supervisor was shown one witness quoted twice and told it was two.
  That matters because the ladder's whole value is the gap between the worker's account and
  the engine's counts (`engine/oversight.py`: *"the party under suspicion must not be the only
  witness"*), and this collapsed that cross-check **invisibly** — a rung comparing one source
  against itself reports that it compared two. The dispatch still carries `worker_says`, now
  described as what it is (the newest three lines again, for recency: the interval can be
  dozens of lines and the newest intent is the one a verdict turns on), and
  `worker_questions` is named as the RUNG's checklist to answer FROM the evidence. Two tests
  pin it: that the prompt makes no answered-questions claim, and that every `worker_says` line
  is also a `says` line — so the two-source story cannot be re-invented.

## [0.531.0] — 2026-11-11

### Changed
- **Ctrl+Enter sends, everywhere a message or an answer is composed** (operator, 2026-11-11:
  *"we need to switch from shift-enter for send to ctrl-enter for send"*). Plain Enter still
  inserts a newline — that is the half of the 2026-08-16 order (F353) he kept. **Cmd+Enter sends
  too**, because the same console is read on a Mac and macOS reports that chord as `metaKey`;
  a switch to `ctrlKey` alone would have left Mac users with no send chord at all.
  Changed in one pass across every surface that composes, so the console cannot teach one chord
  and obey another: `components/answerform.js` (the Decisions page, the run view, the finish
  banner and the conversation answer form all share it), `views/conversations.js`,
  `views/run.js`, and the two placeholder hints in `components/chat.js` and
  `components/transcript.js`. **Shift+Enter now does nothing** — the browser test asserts that
  as well as the new chord, because a superseded chord that silently keeps working is how a key
  change half-lands and nobody notices for weeks.

## [0.530.1] — 2026-11-11

### Fixed
- **A util name sent as an action `kind` is now diagnosed from the instance's own catalog, not
  from a guess about hyphens.** The correction for an unknown `kind` (`engine/shiftedfields.py`,
  shipped 2026-11-08) rested on a property of the action vocabulary — no kind contains a hyphen —
  and so could only recognise a HYPHENATED util name. 34 of this instance's 57 utils have no
  hyphen, and the measurement says that is where the mistake actually lives: of 14
  `wrong_turn_detected` invalid-kind rejections over 23 days across 9 routines, **8 were
  `kind: "websearch"`** — the single most frequent shape, and the one the heuristic could not
  see. It got the bare enum of 34 kinds and no hint, and the run lost a turn. The util branch
  now asks `utils_lib.exists` first (one stat call) and says `IS A UTIL` for a name the catalog
  carries, keeping the hyphen reading as a fallback for a name it does not — so a run that means
  a util it has not installed still gets told the shape. The lookup is strictly best-effort: it
  runs inside a schema rejection, the one path that must never raise, so a loop without a server
  and a library home that does not exist both fall back silently (both are tested).

## [0.530.0] — 2026-11-10

### Added
- **⟲ rewind puts a conversation's FILES back, not only its transcript** — the first increment
  of "Undo a run, and per-reply artifact snapshots". A routine's directory is the git repo
  `autocommit` writes at every run end, so its deliverables have a history and a run can be
  undone from it. A conversation is NOT versioned: `artifacts/` holds exactly what the newest
  reply left there. So a rewind took the transcript back to the chosen turn and left the files as
  the discarded replies had written them — the conversation saying one thing and its deliverables
  another, which is the error nobody notices until they act on the wrong file. Each reply's
  close-out now copies `artifacts/` to `.artifact-snapshots/<run-ts>-t<turn>/`
  (`engine/artsnap.py`, called from `loopend.finish_run` for conversations only) and
  `rewind.rewind_transcript` restores the newest snapshot at or before the turn it kept.
  A COPY rather than a diff, because the moment a snapshot matters is the moment the automation
  is wrong and a person can act on a copied file with no tool at all; what was live is copied
  aside as `pre-restore-…` first, since a rewind is itself an act someone may regret; the store
  is bounded per conversation (200 replies against a 40 MB artefact set would otherwise grow by
  8 GB, silently) and never prunes a pre-restore copy; every entry point is best-effort, because
  a lost undo point is a far smaller harm than a reply that fails to finish. The result's
  `artifacts` key is ABSENT when no snapshot covered that turn — never false-y — so "nothing to
  put back" cannot read as "the files were restored", which is exactly the case for a routine run
  reaching the same code. A reply that wrote no files writes no snapshot at all.

## [0.529.0] — 2026-11-10

### Added
- **A rate-limited quota read now says when it will read again** (D277's remainder). Any non-200
  from the subscription-quota read used to become one soft error string, so a 429 was
  indistinguishable from a dead credential — and BOTH consoles simply re-asked on their own
  cadence (the Settings card on every visit, the dashboard chip on every load), which hammers an
  account that is already being throttled while telling the operator nothing. The two cases want
  opposite behaviour: an ordinary error should be re-read the moment he has fixed something, a
  rate limit must be left alone. A 429 now comes back as `rate_limited: true` with
  `retry_after_s`, carried from the upstream's own header by `cliproxy_quota.retry_after_seconds`
  — **both arrival shapes**, and the second is the one that matters, because the proxy usually
  answers 200 and carries Claude's status and headers INSIDE the envelope, so a 429 from Claude
  never touches the outer status at all. RFC 9110 allows a delta OR an HTTP date and Anthropic
  has sent both, so the conversion to whole seconds happens once server-side rather than in two
  renderers. **An unusable header yields NO figure** — blank, a word, negative, zero, absurd, or
  a date already past — while still reporting the rate limit: a wrong countdown is worse than
  none, because *"reads again in 0s"* invites exactly the immediate re-ask this exists to stop,
  and a caller that backed off only when a number arrived would poll straight through the
  commonest 429, which carries no header. The Settings card reads *rate limited — reading again
  in Ns* and re-reads itself when the delay passes; the header chip reads `quota · rate limited`
  rather than "subscription quota unavailable", whose wording exists to point at a one-line fix
  that a rate limit does not have.
- **A branch of a branch records its whole lineage** (E3.7(b)). `rewind._leg_start` bounds a
  reply at the latest `finish` or fork cut before it, and a branch recorded only its immediate
  fork point. A fork is indexed by TURN, so it can land MID-REPLY — the inherited reply it cut
  then has no finish of its own. One level deep that is fine; two levels deep an INHERITED reply
  lying before the immediate fork found no bound at all (the fork cut is after it, and the
  grandparent's fork was nowhere in the header), so it read as the conversation's FIRST — and
  flagging it with ⚑ would have discarded the ENTIRE inherited history and re-sent
  `instruction.md` in place of the message that opened it. `fork_conversation` now writes
  `lineage: [{slug, turn}, …]`, oldest first, in both `routine.yaml` and the transcript header;
  `rewind._fork_turns` reads `branched_from` too, so every conversation forked before this keeps
  working on its single cut.
  Its sibling half — whether a branch's first CHILD reuses a number the inherited transcript
  holds — **needed no fix and is pinned instead**: a branch copies `state` and `attachments`
  only, never `runs/`, and `childrun.claim_child` numbers from the highest child directory ON
  DISK rather than from the transcript. The order itself said "fix it, or delete this half if it
  does not".

## [0.528.0] — 2026-11-10

### Fixed
- **The test session's memory guard waited for 2 GB while the fast stage holds 3.3** (goal b8).
  The operator's ruling was to lower the gate's worker count rather than raise the 5 GiB
  ceiling, and his follow-up named the figure to design against: `held_pct_of_limit` — anon +
  swapped pages, what an OOM is actually decided on — never `peak_pct_of_limit`, which counts
  page cache the kernel drops at the limit. Folding every stage the gate has sampled since that
  field shipped: a `tests` stage (controller alone) holds 0.79-1.20 GB, a `fast` stage
  (controller + 4 workers) **3.08-3.29 GB in 205-264 s**, a `ui` stage 0.80-1.03 GB. So a fast
  worker holds **~600 MB, not the ~250 MB every comment claimed**, the whole stage holds at most
  3,367 of 5,120 MB, `oom_kill` is **0 across the entire record**, and **duration does not track
  held pressure** (the highest hold took 241 s; the highest peak took 205 s). The defect was
  therefore not the worker count but `memgate.NEED_MB["fast"]`, which admitted a selection
  needing 3.3 GB after waiting for 2 — a guard reporting safety it never checked. Now 3584 MB
  (the worst measured hold, rounded up on the MB figure: 3.29 GB rounds to 3328, which is BELOW
  the hold, and the new test below caught exactly that). The existing coupling test could not see
  it, because its floor was `FAST_WORKERS * 250` = 1,000 MB against a 2,048 MB budget — the
  relation held while both numbers were jointly too small; its floor is now the measured
  600 MB/worker, and **two new tests** pin the budget against the measurement itself and against
  the cgroup it waits for (a budget over the ceiling can never be satisfied and would exit 3 on
  every gate forever). `FAST_WORKERS` stays **4**: on the figure the operator named, the stage
  sits at 61.6-65.8 % with 1.7 GB spare, so cutting it would cost every release gate wall clock
  for headroom the record does not show it needs — that call is his, with the table in front of
  him. `CLAUDE.md` also still said the cap was 6 and the budget 2 GB, and never mentioned the
  cgroup half of the guard.

### Added
- **An answer given on a run's finish banner is now REVISABLE** (D212, the last part). The
  operator's brief asks for closing asks to be *"collected, presented, answered and revisable in
  the run's final summary"*; answering on the banner locked the form and put nothing after it, so
  the answer was final the moment it posted — while the same answer on the Decisions page has had
  a revise path since F525, which exists because he answered one of four sub-questions and then
  said *"i want to revise it to add the other answers"*. The banner now shows a receipt with his
  own words and a *revise* button that opens an editor pre-filled with them, posting to the SAME
  `POST /api/questions/{qid}/revise` route — no second route, no second answer shape. Two browser
  tests drive the whole round trip; no new CSS class, since both stylesheets are at their ratchet
  ceilings.
- **The `second-opinion` library rule** (D279(b), the unbuilt half of that order). Before a
  judgment that cannot be taken back — a release, a closing verdict, a message that cannot be
  unsent — one independent read from a child run on a model of a DIFFERENT family: briefed
  self-contained and WITHOUT the asking run's conclusion (a reviewer shown your answer finds
  reasons for it), with no write rights, and the finish says where the two disagreed. It names
  the capability, never a model or a util. Seeded in `library-seed/rules/` and authored to the
  live library; it binds no routine — which routines hold it is the operator's call.
  `docs/curated-rules.md` records its provenance and says plainly that it rests on an argument
  (self-correction without external feedback measures net negative; two models of one lineage
  share their characteristic errors) rather than on effectiveness data.

## [0.527.0] — 2026-11-10

### Added
- **A resumed leg is TOLD when its last action's outcome is unknown** (D278). The engine
  records `assistant_action` before dispatch and the `observation` after it, so a process
  killed between the two — a deploy, the D99 SIGKILL auto-resume, `daemon/reap_resume.py`
  after a restart — left the transcript ending in an action with no result, and the only
  framing the resumed leg got was *"continue from the last observation"*. For an OUTWARD
  action that is the costly case: freelance-hunter sending mail, vodafone-outage-claims
  filing a claim, do-my-taxes submitting a return — a model that sees no result is likely to
  issue the action AGAIN. `boot` now answers each unanswered trailing action with an ordinary
  `observation` event (`engine/unknownoutcome.py`, payload kind `outcome_unknown`): *the
  process ended while your `<kind>` action was running… it may or may not have taken effect —
  check its effect before repeating it.* A real transcript event rather than an engine note,
  written on the dead action's own turn and appended to the list boot replays from, so it
  rides THIS leg's prompt and a second resume replays it instead of re-authoring it —
  exactly-once falls out of that, with no stored flag, because after one resume the
  transcript no longer ends unanswered.
  **The trap this design exists around, pinned and proven:** `chain.take_turn` returns
  `loop._finish(action)` without calling `_observe`, so a `finish` never writes an
  observation — "the events end in an unanswered action" is therefore TRUE of every normally
  finished run and of every conversation follow-up leg. `kind == "finish"` is excluded
  EXPLICITLY, deliberately not by testing for a trailing `finish` event (a kill between the
  finish action and the finish event leaves the same bare shape). With the exclusion removed,
  the negative-control test fails with `action_kind: "finish"` recorded on a run that ended
  normally — the defect this would otherwise have shipped to every resumed run. It grounds
  nothing (`donothing.NON_GROUNDING_KINDS`), so a resumed leg cannot pass the fabrication
  guard on an action nobody knows ran, and the dead action's kind is kept out of the payload's
  `kind` field so `history.seen_paths` cannot read it as a file the run saw. No new transcript
  event type. Paid for `boot.py`'s frozen 350-line ceiling by moving the `seen_paths`
  rehydration into `history.py`, where every other transcript-derived fact already lives.
- **A catalog model carries a "when to use" note, and `list_models` returns it** (D279(a)). A
  run choosing a `model` for `spawn`/`subtask`/`llm`, or a `role_swap` position, saw a catalog
  of NAMES: nothing told it that one model is cheap and blunt, that another is the only one
  here with vision, or that a third is rate-limited to uselessness before noon — and the
  operator knew all three with nowhere to write them. `ModelConfig.note` is that line, edited
  on the model's own catalog row in Settings and returned as `when_to_use` beside the name,
  **only when set** (an empty value would read as "no opinion recorded" where there is simply
  no field, on every model of every catalog). **Advice, never a routing rule:** nothing in the
  engine reads it to decide anything, and a test pins that by grepping `src/` for readers —
  the only two allowed are the surface that stores it and the surface that serves it.

### Fixed
- Model stand-ins in `tests/test_model_role_override.py` carry `note`, which
  `do_list_models` reads unconditionally — the stand-in trap a grep for a reading function's
  callers cannot see (it broke two pre-existing tests the moment the field landed).

## [0.526.0] — 2026-11-10

### Added
- **A one-shot is a hook**, which gives the `schedule` trigger its first user and closes the
  hooks campaign's last gap. `hooks.TRIGGERS` has named four things that make a hook fire since
  step 1 and three had users; `schedule` carried a note instead — *"a time. Nothing uses this
  yet; the field costs nothing and naming it keeps the next mechanism from being a fourth
  store."* That next mechanism already existed AND was a fourth store: the one-shot request
  spool. So "what hooks exist in this instance" — the question the campaign exists to make
  answerable — could not be answered for anything time-fired. The new
  `hooks/scheduleproj.py` projects an armed one-shot onto the stream with its ending (`fired` ·
  `cancelled` · `skipped`) as a later row about the same id, wired at all five sites, with an
  EXACT inverse written in the same change. The spool stays the SOURCE, unlike a report: the
  daemon fires from it and consuming the request file IS the non-repeating guarantee, so these
  rows are best-effort and can never stop a one-shot being armed. (D152 gap 5)
- **The Claude quota readout reads BOTH payload shapes.** The API is moving model- and
  surface-scoped weekly limits into a `limits[]` array while still sending some as legacy
  `seven_day_<model>` keys, and we read four fixed keys — so a scoped limit that existed only in
  `limits[]` was invisible, and the card's weekly Opus/Sonnet line would have vanished with no
  error: **a limit we cannot read reads as a limit that is not there**. `normalize` now returns
  the two unscoped windows plus every scoped limit keyed by its SCOPE (so a limit sent both ways
  is one row), and names a limit it could not read instead of dropping it. The console renders
  scoped rows from their own display names, and the header chip takes the tightest cap across
  EVERY row — before this it read only the unscoped windows, so a weekly Opus limit at 2% was
  invisible on the page you actually look at. (D277, first increment; the `Retry-After` half is
  still owed and stayed in `docs/designs.md`.)

### Changed
- **Web Push holds back while you are at the console.** Tier 2 exists for an operator who is
  AWAY; while you are here the ask is already on your Decisions page and on the header badge, so
  pushing your phone for it is the noise that trains a person to ignore the channel. The console
  beats `POST /api/push/present` every 30 s — foreground tabs only, because a browser left open
  on another desktop is not a person looking at the page — and a new push is held for 90 s after
  the last beat. **Deferred, never dropped**: the ask stays out of the dedupe set, so the first
  pass after your presence lapses pushes it exactly once, and if you answered it meanwhile
  nothing is pushed at all. A withdrawal is never held — it clears an alert already on your
  phone. (D280)

## [0.525.9] — 2026-11-10

### Added
- **The scope editor links the Google Cloud console where it is needed.** A granted scope is
  useless until its API is enabled in the project — the Docs API answered `SERVICE_DISABLED` with
  its scope on the token — and the console was reachable from nowhere in rsched. The editor now
  links the project's *enabled APIs* and *consent screen*, and every API row a *console* link to
  the project's API library searched for that API. The project number is read off the OAuth
  client id itself; the providers' own console links carry it too.

## [0.525.8] — 2026-11-10

### Added
- **A scope editor on every scoped provider in Settings → Connections.** Google's consent
  scopes lived in a write-only Secrets entry, so changing one meant retyping ~48 URLs blind and
  clicking reconnect on another card — llmsectest-weekday's Drive and Docs calls 403'd for two
  scopes nobody could see were missing. The editor shows the list grouped by API, expands short
  names, counts against Google's observed 50-scope consent ceiling, compares each connected
  account's GRANTED scopes with the list as edited, and saves (*save*, *save & reconnect*)
  through `PUT /api/settings/oauth/{provider}/scopes`, judged by `oauth/scopes.py`.
- **Google Health as its own connection** (`google_health`). The Health API refuses a token
  carrying any non-`googlehealth.*` scope, which is why deficit-coach's calls failed with the
  Health scopes on the token. Same Google OAuth app, its own scopes
  (`GOOGLE_HEALTH_OAUTH_SCOPES`, `googlehealth.*` only) and token
  (`GOOGLE_HEALTH_ACCESS_TOKEN`); a routine binds it beside `google`.

### Fixed
- A failing util serving several connections is routed to request one only when none of them
  is bound — a routine with Google bound is no longer told to ask for a Health connection
  whenever a Google call fails.

## [0.525.7] — 2026-11-10

### Added
- **The server's own slow-request evidence is now durable, which is what a "why is the console
  slow" question needs.** `web/app.py`'s timing middleware has recorded every request over 2.0 s
  since 2026-09-12 — into a 50-entry in-RAM deque read by `/api/debug/slow`, which takes the
  operator's PRIMARY token. So the record died at every restart (this daemon restarts several
  times a week for its own releases), no run could ever read it, and `api_traces.py`'s own
  docstring already said so. Each slow request now also appends one row to the same 14-day
  `.ui-traces` store the browser's `slow` rows land in, through the existing
  `record_server_trace` door: kind `slow`, `view: "server"`, and a `detail` that keeps the
  leading `<n>ms` shape every reader of that kind parses, plus the method and the **in-flight
  count** — the figure that separates a slow HANDLER (nothing else running) from CONTENTION (one
  of thirty), which took a stack sample to tell apart on 2026-09-12. Because the kind is
  unchanged and only the VIEW distinguishes the two sides, the browser-observed and
  server-measured times for one endpoint can now be SUBTRACTED: the first real way to say
  whether the cost is the server, the network or the browser's connection ceiling.
  `record_slow_request` owns both halves (ring and row) so the middleware calls one function;
  `/api/debug/slow` and its auth tier are untouched, and the existing four assertions still
  hold. **Measured motivation, from the first day of client rows:** 388 `slow` rows, median
  1,898 ms outside the 17-18h UTC bunching and 9,254 ms inside it, with unrelated endpoints
  finishing in the same second to within 20 ms (`/api/llm-tasks`, which returns an in-memory
  snapshot, took 34,396 ms) — the signature of a blocked event loop, which no client-side row
  can ever attribute.

## [0.525.6] — 2026-11-10

### Fixed
- **The suite's second most re-run browser node was racing the router, not the feature.**
  `tests/ui/test_formpersist.py::test_credential_shaped_fields_are_never_kept_as_drafts`
  injected its probe fields into `#view` — the element the hash router OWNS and whose children
  it replaces on every render. A render landing between the injection and the first `fill()`
  deleted the probes, so `[data-probe="0"]` never appeared and the node died on a 15-second
  `Locator.fill` timeout. Measured across the recorded gate logs: **8 reruns in each of 5 gates
  and an outright failure in 3 of them**, twice tonight — each time costing the release gate its
  `flaky` verdict and a whole extra serial stage. The probes now mount on `document.body`, which
  changes nothing about what the test proves: `formpersist` saves through document-level
  `input`/`change` delegation and `fieldKey` disambiguates over `document.querySelectorAll`, so
  a field in `body` is seen exactly as one inside a view. Proven both ways — five consecutive
  clean runs after, and the mechanism reproduced on demand by injecting into `#view` and forcing
  one render, which fails with the identical timeout.

## [0.525.5] — 2026-11-10

### Fixed
- **The page-load instrument shipped yesterday recorded a 71-minute page load.** A tab
  BACKGROUNDED while loading does not finish loading: the browser defers timers, images and the
  `load` event itself until the tab is looked at again, so `performance.getEntriesByType(
  "navigation")[0].loadEventEnd` measures how long it sat in another window. The very first
  production reading of the new `load` kind (F858, 0.521.0) held **4,271,306 ms** for that
  reason — and nothing downstream can tell such a number from a real one, so it poisons any
  median or worst-case it lands in, which is precisely the reading the instrument exists to
  give. `static/trace.js` now latches `visibilitychange` from install (the event fires BEFORE
  `load`, so by the time `load` runs the tab may be visible again and would look as if it had
  been in the foreground all along) and reports a hidden document as `load unavailable
  (backgrounded)` instead of with a false figure. `response` and `dom` are kept, because both
  are normally past before a tab can be switched away from, and the audit reader parses
  `load <n>ms`, which simply does not match. The sibling `render` kind already guarded this
  class with `RENDER_MAX_MS` — this restores the consistency `load` was missing.
  `tests/ui/test_request_timing.py` gains the backgrounded case (driven by overriding
  `document.visibilityState`, since this Chromium build answers `wasn't found` to
  `Emulation.setPageVisibilityOverride`), and the existing load test now parses the load
  milestone as a NUMBER — asserting the substring `load ` alone, it would have passed with
  every row suppressed.

## [0.525.4] — 2026-11-10

### Fixed
- **The `ungated_write` marker spent its one row per run on a read in 6 of 29 runs.** The shape
  that recognises a bare write command (`engine/shellwrite.marks_a_write`) matched its write
  tokens ANYWHERE in the command line, so `for ln in open(f)` — Python's commonest loop variable
  — read as the `ln` symlink command, and `echo "=== files I would touch ==="` as `touch`.
  Measured over 3,941 shell commands in the fleet's own transcripts: 996 flagged before, 924
  after, **72 changed from flagged to clear and none from clear to flagged** (64 `ln` loop
  variables, 4 `touch` in narration, 4 `rm` inside a grep pattern or a `--help`). The harm is
  not noise: the health event is latched ONE PER RUN, so in 6 of the 29 runs that filed one, the
  row named a read-only command while 21 to 85 genuine write-shaped commands in the same run
  were named nowhere — a compensating control defeated, which this module's own docstring calls
  worse than none ("a detector a reader learns to discount"). The write token must now sit in
  COMMAND POSITION, with runner flags allowed between (`xargs -0 -r rm -f` kept its flag, a
  regression the first draft introduced and the fleet-wide diff caught). `-exec` is anchored
  without a leading `\b`, which cannot match before a `-` — the dead-anchor class of F730, and
  the `find-exec-rm` pin failed on the first draft of that very line.
- **A release gate could hang for twenty minutes holding the lock every other gate waits
  for — only when the box was loaded.** 0.525.2 made `tests/conftest.pytest_configure` wait on
  the engine container's cgroup headroom as well as host memory, admitting any config without a
  `workerinput` attribute as "the controller". A unit test that calls that hook directly to pin
  the distribution logic passes a stub with an `option` namespace and nothing else — so
  `tests/test_ui_harness.py`'s three such nodes entered the real memory wait, two of them asking
  for the BROWSER need (3,584 MB). Measured on gate `1794268634309389266-release`: both blocked
  for exactly the 300 s pytest timeout, were marked `rerun`, restarted, and the fast stage never
  reported a verdict at 6,736 of 6,737 tests — while holding `/tmp/rsched-gates.lock`, so the
  hang is not local to one gate. The same nodes pass in 0.0 s on a quiet box, which is why it
  shipped: a gate hang that appears only under load appears only when the gate matters.
  `memgate.should_wait` now requires a real pytest config (`pluginmanager`) as well as the
  controller role, with the measurement in its docstring. `tests/test_memgate.py` had nine tests
  and exercised this seam in none of them; it now drives the REAL hook with the REAL stub shape
  and fails if the wait is entered at all, because asserting on the predicate alone would not
  have caught a defect that lived at the call site.
- **`role_swap` told 14 of 30 routines a false fact about their own configuration.**
  `engine/roleswap.cheap_model` returns None for two causes — the `tool_call` role resolves to
  nothing, or it resolves to the model `main` is already served from — and the engine note
  enumerated only the first: *"this routine configures no tool_call model and this instance sets
  no fleet default for it"*, followed by *"do not retry the swap"*. The equal pair is the COMMON
  case, not the corner: 14 of 30 routines set `models.tool_call` to the same catalog model as
  `models.main`, which is what the settings patterns ship, and only 2 can serve a cheap turn at
  all. `cheap_unservable` now resolves which cause fired (`UNSET` / `SAME`) and the note names
  it, because the two have opposite fixes — set a model for the role, versus set a *different*
  one. One test per cause: the old wording was pinned nowhere, which is how it came to describe
  one case while firing on both. Found by `config-optimizer`, measured and forwarded by
  `scheduler-builder` (R3295 → R3297 → R3304); its fleet reading stands — `role_swap` was
  attempted 3 times since 2026-10-20 and served 0 times.

## [0.525.3] — 2026-11-09

### Fixed
- **The blocked badge counts what still holds, not a week of history.** It read 10 on
  2026-11-09: nine fires skipped under a pause lifted five days earlier, and a `backup_stale`
  three snapshots after the backup came back. Every operator-only row is now asked about NOW
  (`web/operator_conditions.py`: the pause file, the backup watch's own verdict, whether the
  answer still has no reader, whether a lane still names the missing slug); one known to be over
  is `resolved` — on the Stats panel with what is true now, off the badge. A condition that
  cannot be told keeps counting. `answer_undelivered` joins the operator set: only the operator
  delivers or settles an answer.

## [0.525.2] — 2026-11-09

### Changed
- **No file is frozen because it reached the size bar — four were split along their seams.**
  The operator, 2026-11-09: *"files should never be frozen just because they hit their line
  limit."* The health vocabulary lived in `health_events.py`'s DOCSTRING, read back by seven
  tests through `__doc__`, so the next event could be documented nowhere ("the next event cannot
  be documented there", 0.511.0's CHANGELOG). It is now DATA: `rsched/health_vocab.py`'s
  `EVENTS`, with each event's paragraph beside it, and every test reads the tuple
  (`health_events.py` 350 → 75 lines). `readmodels/health_stream.py`'s two per-question reads,
  `recent_events` and `budget_endings`, moved to `readmodels/health_reads.py` (350 → 288), so
  `BLOCKED_EVENTS` stays where every reference points and has room. The router list left
  `web/appwiring.py` for `web/routers.py` (350 → 230), undoing the line-merge 0.525.1 used to
  register its route.

### Added
- **`answer_undelivered` on the blocked-work surface.** The daily owed-work sweep
  (`daemon/owed_watch`) files one row per routine or conversation holding an answer that NO run
  will read and that has waited past a day. Who reads an answer is one derivation,
  `readmodels/answer_pickup`, shared by the Decisions page and the sweep, so the daemon reads it
  without importing the web layer.
- **The test memory gate waits on the container's own ceiling too** (`tests/memgate.py`,
  accepted proposal q-20261107-020004-53). It admitted a selection on the HOST's free memory
  alone, while the fast stage runs in the engine container's cgroup. It now waits for both, the
  smaller deciding, and names which refused. The container figure is `memory.max` minus
  `anon` + swap: `memory.current` counts page cache the kernel drops at the limit, which is why
  51 gates read 96-100% of it while nothing was ever killed. `NEED_MB` and `FAST_WORKERS` are
  unchanged and are re-tuned from the new figure later, not from the old one.

## [0.525.1] — 2026-11-09

### Fixed
- **An answered decision says who reads it — and one nobody will read waits on you again.**
  Two conversation answers (2026-09-21) sat unread for seven weeks, filed under Settled as
  "answered · queued" exactly like an answer a run reads that night. Both were filed during the
  conversation's LAST turn: the answer route resumes only a finished conversation (F39), so it
  left them for a next turn that never came. The post-finish sweep now re-opens a conversation
  for a stranded answer as it does for a stranded message (`daemon/runner_state.stranded_input`;
  a routine's answer still waits for its next fire, by design). Every answer in flight carries
  `pickup` on `/api/questions` (`web/decision_pickup.py`): `live`, `scheduled` + `at`,
  `trigger`, or `never` + `why`, the last stated only when known. The Decisions page shows a
  chip saying which, lists `never` rows in a new **Undelivered** band that waits on you, the
  header badge counts them, and each offers **deliver now** (one run reads it) or **settle
  without a run** (archived like a consumption, starting nothing) —
  `POST /api/questions/{qid}/deliver|settle`, both refusing a row that has a reader.
- **A held backup lock fails the nightly instead of passing it, and no test reaches the live
  backups** (`e1c9ec9a`, shipped ahead of this version). The 2026-11-09 01:09 snapshot was
  skipped with exit 0 because a release gate's backup tests held `/tmp/rsched-backup.lock` —
  the engine container shares the host's `/tmp`. `BACKUP_LOCK` moves the lock (the tests use
  their own); a contended run waits `LOCK_WAIT_S` (1800 s) and exits 75, so `OnFailure` pushes.
  The same tests mirrored fake homes into the LIVE off-box NAS share whenever they named no
  `MIRROR_ROOT` (since 2026-11-05); the helper now defaults to local-only and refuses `/mnt/`.


## [0.525.0] — 2026-11-09

### Added
- **A block only you can clear reaches the rail**, not one Stats panel inside a 7-day window.
  The blocked-work fold answers "what did the fleet owe and not deliver" and had exactly ONE
  reader, so a block that waits for a PERSON waited on a page nobody has reason to open:
  `backup_stale` ran nineteen unnoticed nights, and a pause that ate eight due fires was found
  by an audit reading the ABSENCE of run directories. The second question it never answered is
  **who can clear it** — `readmodels/operator_blocks.py` holds that judgement and nothing else.
  Five of the twenty events are blocks nothing inside the system will clear: a stale backup (a
  host unit), dropped fires under a pause (never made up — D237), a one-shot consumed unfired,
  a lane naming a member that is not a routine. Everything else clears itself and is
  deliberately left off, including the stream's three loudest events — 202 `wrong_turn_detected`
  in three weeks here — because a badge that is never dark is the same as no badge; each
  exclusion's reason is in the module's docstring. `/api/health/blocked` marks each row
  (`operator`, `waiting_on_you`) and serves `operator_total`, so the console renders a
  judgement it was not compiled with and adding an event lights the badge with no JS change.
  The count is DISTINCT ROWS, one per thing to go and fix. (F796)
- **The daemon runs the post-deploy checklist itself, a short while after boot** — "no human
  step ever again" (operator, 2026-11-09). `rsched postdeploy` shipped in 0.445.0 and nothing
  ever called it: it is a command a person types, while the path this instance takes several
  times a week is the restart SENTINEL, which exits for the supervisor and verifies nothing.
  Measured: the daemon came up on 0.520.0 while the record's newest row was two days old, and
  that row's own backup-freshness check was a FAIL nothing re-checked for sixty hours. The new
  `daemon/postdeploy_watch.py` waits for our own `/api/status` to answer, runs the command's
  OWN checks (never a second implementation — two checklists disagree) and appends the same row
  shape with `trigger: "daemon boot"`, so automatic and typed rows stay comparable. A FAIL now
  reaches a surface: the new `postdeploy_failed` health event is on the blocked-work fold. What
  this shape cannot catch is "the wrong host answered" — the right host always answers when it
  is the one asking — and the module says so, so no reader over-reads the automatic row. (F854)
- **A per-run context breakdown**: what each turn's prompt is MADE OF — the composer's own
  sections, plus `history` and `observations` as they grow — recorded beside
  `window.note_prompt_size` and shown as a stacked bar on the run view
  (`components/promptsegbar.js`). Diagnostic only and byte-identical to what is sent: the
  composed prompt is a caching contract, and a cold window of this size has cost over $100
  here. Measured in the engine's own `estimate_input_tokens` units, so a segment and the window
  it is measured against read in the same currency. (D270)

### Changed
- **The Decisions page splits by CLASS**, not by whose process happens to be parked. Increment
  (a) put a class on every record in 0.465.0 — `blocking` means the run could not do the work it
  was fired to do without the answer, `closing` that it finished and the answer shapes the next
  run — but the page still grouped by `mode`, and over the 282 records this instance has filed
  `mode: "blocking"` was used **0 times**. So the band promising "a run is waiting on you" was
  structurally empty, while a run that declared `blocks_work: true` and then ended was demoted
  to Proposals, the band whose own caption says nobody is waiting on it. The bands, the filter
  chips, the rank and the captions now read one derivation (`questions-taxonomy.classOf`): a
  parked run is blocking whatever the key says, else the recorded class, else the subject
  default — a typed access request is blocking, anything else closing. (D212 increment (c);
  the revisability half of (b) is still owed.)
- **A ⚑ priority flag on handed-over work now reaches the builder**, not the ledger keeper.
  Every flagged `F`/`D` resolved to `self-audit` because every such row lives in its
  `report.json` — so a star on a decision BEING BUILT landed in the digest of the routine that
  may not build it, for exactly the items a flag is most likely to be set on. Ownership is now
  read from the row (`priorities.flagged_item_owner`): the builder while the hand-over marker is
  in `detail` and the row is `in_progress`, the ledger keeper otherwise, so a flag on finished
  work still reaches someone who can clear it. One module owns the marker constant, so the
  recipes' wording and the resolver cannot drift apart silently. (D240)

## [0.524.0] — 2026-11-09

### Added
- **A daemon boot that reaped something now reaches the health stream**, so a container
  recreate that RESUMED its runs is countable. Measured 2026-10-29: pid 1 restarted at 23:58:22
  and run `self-audit:20261029-233250` continued across it — and `health-events.jsonl` held
  nothing at all. No `orphaned_run` (the run was resumed, not orphaned), no resume row, no
  shutdown mark. So the instance's own count of recreates saw only the ones that left wreckage,
  which is the wrong bias in a number used to judge whether the daemon is being killed: it
  under-reported exactly the cases where the engine coped. (F799)
- The new `boot_reaped` event carries the four facts no single place held: **pid 1's start**
  (what tells a container REPLACEMENT from a daemon that died inside a living container), the
  deliberate-shutdown mark's **reason or its absence**, how many dead-pid runs were **closed
  out**, and how many of those a restart killed and were **re-opened** (F746). As fields, not
  only prose, so a sweep can count without parsing a sentence.
- **A boot that reaped nothing files nothing.** Almost every boot reaps nothing — the daemon
  restarts when it is idle, by design — so a row per boot would say "all quiet" every time, and
  `health_cooldown` would then suppress the interesting ones behind the quiet ones inside its
  window. The emitter also never raises: it sits in the boot path, and a full disk at boot is
  exactly when orphans exist.
- It is emitted BEFORE `clear_shutdown_mark`, because the mark is the event's own `why` — pinned
  by a test, since the ordering is invisible at the call site.

## [0.523.1] — 2026-11-09

### Fixed
- **The directory picker overwrote the path you were typing.** `dirpicker.js` focuses its path
  field on open — typing a path is the way in — but the opening listing is still in flight while
  those first keystrokes happen, and its write-back replaced them with the daemon's home
  directory. The next "go" then read the field it had just clobbered and browsed `/home/mark`
  instead of the folder asked for. A listing now fills the field only when the field still holds
  what it held when that listing began; `cur` and the list update as before, so "select this
  folder", a folder row, Enter and ".." are unchanged.
  The existing newest-listing-wins guard could not see this case: the opening listing is not
  superseded, it is simply late, so it was never discarded. (F853, raised from
  `scheduler-builder`'s R3262.)
- **The flakiest node in the browser suite was this defect, not the harness.**
  `test_the_folder_picker_keeps_its_height_whatever_the_folder_holds` was re-run 20 times across
  eight release gates — the most re-run node in the suite — and on 2026-11-07 it defeated both
  the parallel reruns and the gate's serial re-run, costing a release cycle. Its aria snapshot
  showed the field holding `/home/mark`: neither what it typed nor a prefix of it, which is the
  clobber rather than slowness, and is why more reruns could not help. Three new browser tests
  pin it deterministically by HOLDING the opening `/api/fs/list` response with `page.route` and
  releasing it at the moment that makes the defect certain, instead of waiting for a race:
  the field survives, "go" browses what was typed, and the picker still fills the field when it
  navigates itself.

## [0.523.0] — 2026-11-09

### Added — how long a VIEW takes to settle after a navigation, the cost this console mostly is (F861)

0.521.0 added request latency and page-load milestones, and a gap survived both: `static/app.js`
is a **hash router**, so only the FIRST page load of a session fires `load`, and every
navigation after it swaps `#view`'s children in place. The store's own numbers say how lopsided
that is — **752 `nav` rows against roughly one page load per session** over the 14-day window.
So the thing the operator meets most often (clicking Routines → Run → Questions and waiting for
the page) was measured by neither kind, unless some request happened to cross the 1,000 ms
floor. A view can feel slow while every one of its requests is fast: three sequential 400 ms
fetches, a large render, a skeleton that lingers.

A third duration kind, `render`: one row per navigation, carrying the view and the milliseconds
from the `hashchange` until `#view` **stops changing**, read by a `MutationObserver`.

Three honest properties, each pinned:

- **"Settled" is a quiet period, not a paint.** The row is filed once no mutation has arrived
  for `SETTLE_MS` (150), and that interval is subtracted from the figure.
- **One row per navigation.** The run view streams, so its mutations never truly stop — the
  observer disarms after reporting, so a live view cannot file a row per frame.
- **An implausible figure is dropped, never recorded.** A backgrounded tab resuming minutes
  later would otherwise book its whole absence as render time (`RENDER_MAX_MS` 30,000) — the
  same class of lie 0.521.0's below-the-token-gate clock avoids.

It lives in `static/trace.js`, not in the router: `app.js` sits at its size-ratchet ceiling
(398) and may not grow, and a tracer that watches the DOM needs nothing from the router — the
app does not know it is measured, which is the better seam.

6 fast tests and 1 browser test, red first (5 of the 6 failed before the implementation; the
app.js-size guard passed, which is the point of having it). The browser test drives a real
navigation in the real console and asserts the row names the view it rendered.

## [0.522.1] — 2026-11-09

### Fixed — a trace kind the console emitted and the store rejected, for 115 days (F860)

`static/views/run.js:344` has emitted `trace("run-question", qid, state)` since commit
`5afed71b` on **2026-07-17** — the run page's own diagnostic for whether and when a blocking
question actually reached the surface the operator reads, which the question ledger cannot tell
from its own records. `run-question` was never in `api_traces.KINDS`, and `ingest` drops an
unlisted kind silently by design: the POST answers 200, `recorded` counts it out, and
`grep -rl run-question ~/routines/.ui-traces/` finds it in no day file. Not one row in 115 days.

Found while answering the operator's question about what UI data the instance collects — the
honest answer had to include a path that looks wired and discards everything.

The kind is now accepted, with its reason recorded beside it. And the cause is pinned rather
than the instance: `tests/test_trace_kinds_agree.py` reads the shipped source on both sides and
compares the two lists **in both directions** — an emitted kind the allowlist rejects reds, and
an accepted kind nothing emits reds unless its reason is recorded (`submit` is reserved and
unused; `stream-close` is written server-side through `record_server_trace`). The two lists live
100 lines apart in two languages and nothing had ever compared them.

3 tests, red first: 2 of the 3 failed naming `run-question` exactly, and the mirror direction
passed before the fix, which is what makes it a guard rather than a restatement.

## [0.522.0] — 2026-11-09

### Changed — a run-tree message's disposal fields are NAMED, not refused and not swallowed (F859)

A `report` addressed to `parent` or `child:<label>` is a hook delivered at that run's next turn
boundary. `engine/runtree` is explicit that it "is not an item and owes the ledger nothing" — it
gets no `R<n>` at all. The four disposal fields were handled in two opposite, and both wrong,
ways:

- **`closes` was refused.** Accurate, and paid for with the whole turn, for a field
  `runtree.send` never reads. 0.511.0 made that refusal target-aware after 19 of them across 11
  routines — and then **three more landed in a single `scheduler-builder` run on 2026-11-08**
  (turns 67, 223 and 110, at 16:14, 16:15 and 17:18; all three of the event's all-time fires,
  two of them in children). A clearer refusal is still a refusal.
- **`answers`, `settles` and `supersedes` were swallowed.** They validated, and
  `admin_handlers.report` returns at its run-tree branch *before* the ledger is read — so a run
  that wrote `answers: R123` had its hook delivered and believed a row disposed of. That half
  cost more than the refusal: a refusal costs a turn, a false belief leaves a row nobody closes,
  reading as open in every backlog.

Both are answered in one direction now. `reportfields.field_problems` returns early for a
run-tree target — the disposal grammar governs ledger rows, and there is none — so the turn
happens and the hook goes out. `runtree.send` takes the disposal fields and `ignored_disposal`
names them back in the observation of the action that wrote them, with the ids, and says what
would actually dispose of them (a separate `report` with a slug target, or none for triage).
`closes` is deliberately left out of that warning: it names no row, so there is nothing to be
mistaken about, and a line that scolds every terminal reply would teach runs to stop replying.

The warning rides the existing `note` key rather than a new observation field, because
`engine/obs_admin.py` sits exactly at the repo's 350-line size bar and a second branch there
would have to buy its own room — one key, one renderer, no second wording to keep aligned.

**D196 is untouched**: a ledger target still refuses a bare `closes` with the operator's own
message, and that is pinned in both test files. 20 new tests; the superseded refusal case in
`test_report_closes_refusal.py` is rewritten to assert the new behaviour and says in its
docstring what changed and why. Bidirectional probe run at each site separately: removing the
early return reds 4 of the 12 disposal cases, removing the observation fold reds exactly 1, and
the remaining 19 stay green either way.

## [0.521.0] — 2026-11-09

### Added — the console records how long its own requests take, and how long a page takes to load (F858)

The UI trace store existed to answer "where is the console slow", and held no duration of any
kind. Over its whole 14-day retention window it had 2,016 `click`, 819 `nav`, 654
`stream-close`, 178 `reconnect` and 22 `error` rows — and not one number a latency question
could be answered from. The operator has reported the console as slow since 2026-10-31 and
nothing could measure it.

Why the two existing instruments did not cover it, each for its own reason:

- `static/trace.js`'s freeze observer reads the browser's **Long Tasks** API, which fires only
  for work that BLOCKS the main thread. Time spent *waiting* on a request blocks nothing, so a
  console that is slow because its requests are slow produced no event at all — which is why
  `freeze` has **0 rows in 14 days** while the page feels slow.
- the daemon's own `slow_requests` deque keeps the last 50 requests over 2.0 s **in RAM**, so
  every restart erases it, and `/api/debug/slow` takes the operator's primary token — no run
  can read it (403, `ROUTINE_TOKEN_DENIED_READS`).

Two kinds are added to the store the rest of the friction evidence already lands in:

- **`slow`** — one row per request over a floor of 1,000 ms, carrying the endpoint, the
  milliseconds, the HTTP status and the concurrent open-stream count. `static/api.js` gains one
  one-directional observer hook (`onRequestTiming`, the shape `openStreamCount` already has) at
  `authedFetch`, the single authed-fetch loop every request shape shares. The clock starts
  **below** the token gate: `requestToken` waits on the operator typing, and a clock above it
  would make the slowest rows in the store the ones where somebody was signing in.
- **`load`** — one row per page load, carrying the browser's own Navigation Timing milestones
  (`response` / `dom` / `load`), so the cost of a page becoming usable is attributable to the
  server, the JS, or the assets rather than guessed at.

The policy lives in `trace.js`, not in `api.js`: the floor keeps the console's fifteen
`setInterval` pollers out of the store (3 s, 20 s, 30 s, 120 s …), which would otherwise bury
the signal under their own volume. A query string never reaches the store — it can carry what
the operator typed — while the path is kept whole, because which endpoint is slow is the whole
question.

8 fast tests and 2 browser tests, red first. The browser pair drives the real console: a
response genuinely held 1.8 s past the floor, and the real Navigation Timing entry — neither
quantity exists under pytest, so a fast test can only read the source, and what would break in
production is the instrument not being mounted.

## [0.520.0] — 2026-11-08

### Added — an answered-but-unproven decision stays on the Decisions page (D220 step 4 / F795)

0.519.0 shipped the proof lifecycle and nothing rendered it. The page now holds a decision he
ANSWERED whose authorized work nothing has yet shown to have landed, in a band of its own
between what waits on a person and what is settled — above Settled because the item is not
finished, below everything that waits because it is not a question. His hard constraint is
honoured: **a row whose proof is unproven is never re-presented as an unanswered question** —
the card keeps his answer and its revise control, nothing offers it an answer box, and the
group's count wears the muted modifier rather than the console's "a person is waited on"
colour.

A `negative` proof — the actions were performed and the proof is STILL absent, his own
"unexpected event" state — is the one thing this page says loudly about a decision already
made: *"proof absent — the work was authorized and has not shown up"*. An `in_progress` one is
muted, because nothing is wrong yet. A `positive` one clears the row into Settled. A decision
carrying no proof map behaves exactly as it always did, which is every row the instance has.

Each card in the band says WHAT is being checked — the predicate and what it reads — so the
band can be read rather than guessed at. No new CSS class: the two chip treatments already
exist, and both stylesheets are at their size ceilings.

**Still owed, deliberately:** `web/decisions_read._DECIDED_STATUSES` still contains
`in_progress`, so a self-audit REPORT decision at that status stays hidden. Those rows come
from `audit/report.json` and carry no proof map yet, so removing the status today would hide
nothing less and would re-ask six already-addressed decisions — the F659 regression its own
docstring records. They need proofs first.

### Added — a memory note carries `stale_after` and `verified` (D268)

`memory_write` gained two optional fields from the Open Knowledge Format: `stale_after`
(YYYY-MM-DD) and `verified: {by: run|human:<who>, at}`. Notes have been writing "verified live
2026-07-30" in PROSE; this makes it data. The engine renders both in the note's INDEX line, and
a note past its `stale_after` is listed under its own heading in the state digest — so a run
reads which of its own notes have expired instead of trusting all of them equally.

The INDEX rendering extends the one engine-owned writer rather than adding a second, and both
fields are declared in `ACTION_SCHEMA` **and** in `actions.KIND_FIELDS` — a new sub-field
missing from the second is silently stripped. `docs/prompt-anatomy.md` §5.1 was re-rendered by
its own script.

The always-on prompt contract grew **+824 characters**, all in the schema section, and its
ceiling was re-locked by hand with the reason: that budget exists to make exactly this cost
visible and deliberate.

**Beyond the literal order, kept on purpose:** a later `memory_write` MERGES these markers
rather than replacing them. Replace-only semantics made any ordinary revision silently drop an
expiry an earlier run had set, and losing an operator-visible marker on an unrelated edit is
the worse failure.

### Changed — the settings digest names rule approval (D252c)

`components/settings-digest.js` named four of the five approval dials and never
`rule_confirm`, whose value always arrived (it is in `grants.APPROVAL_DIALS`). So turning rule
approval DOWN — the only direction that matters, `always` being every routine's default —
showed no difference in a changed-field note, an override note, a closed "more" menu or the
Library's pattern settings. It is now named on the same departs-from-default rule the config
and recipe dials follow, and a second test checks the digest against `APPROVAL_DIALS` ITSELF,
so a sixth dial added with no digest line fails a test instead of shipping silent. This
completes D252 and its `docs/designs.md` entry is deleted.

## [0.519.0] — 2026-11-08

### Added — a routine's inbox message takes ATTACHMENTS, and both pages serve them back (D275)

The operator, 2026-11-07: *"I wanna be able to add an attachment to a routine's inbox message
field"*, with his scope answer of 2026-11-08: *"page too: also serve it back for
viewing/downloading on the Messages page"*. Both halves ship.

`POST /api/routines/{slug}/messages` takes MULTIPART (`text` + `files`), reusing the saver the
conversation composer already uses (`conversations_common._save_attachments`, its per-message
count cap and per-file size cap, 413 on either) and `conversations.attachment_note`, filing the
rels through `inbox.file_message(extra={"attachments": …})`. **Nothing engine-side changed** —
`engine/inbox.py` already read that key off any `msg-*` record and already auto-attached images
and PDFs to the run that receives it. New `GET /api/routines/{slug}/attachment?path=` serves
`attachments/` and nothing else, through the same confinement the conversation file route uses
(traversals, cross-routine paths, `artifacts/`, `routine.yaml` and `/etc/passwd` all 400).

The files are shown back on the routine's own Messages panel AND on the global `#/messages`
queue, as thumbnails or open chips, fetched with the auth header and rendered from blob URLs
whose lifetime each mounting component owns.

**An attachment with no words IS a message**, with the `[attached files]` block as the body —
not taste, but the engine: `inbox._drain_locked` injects only a truthy `text`, so an empty-text
record would be consumed with the run never seeing the file, and a refusal would hand the
operator a 400 for the real case of dragging in a screenshot and saying nothing. No words AND
no file is still a 400.

### Added — a decision's option carries a PROOF, and recording the answer is not the end of it (D220)

The operator, 2026-10-29: *"You need to come up with a proof at creation and accept / decline my
response based on the result of you checking the proof"*. The first three increments of that
lifecycle, as a complete and inert mechanism:

- `rsched/proofs.py` — his five states (`open`, `in_progress`, `declined`, `positive`,
  `negative`), his two categories (`operator_act` / `routine_act`), the state machine, and the
  three derived questions a surface needs: the decision's state, whether it is ANSWERED BUT
  UNPROVEN (F795's predicate), and whether it must render louder than an unanswered question
  (`negative` does — the actions were performed and the proof is still absent).
- `rsched/proofcheck.py` — the check that moves a proof on, swept daily by the owed-work watch,
  whose subject is already "work a person asked for that nobody finished". One predicate is
  implementable today: `config_patch_live`, compared key by key against the config the patch
  targeted. A predicate id with no registry entry leaves the state ALONE — a check that cannot
  run must never read as a check that failed.
- `rsched/answerlines.py` — the ONE order in which an answer's verdicts reach the asking run
  (the patch receipt, the decline, the proof), shared by both delivery doors, so a verdict can
  no longer reach a run at boot and not mid-run.

A decision carrying no `proof` behaves exactly as it always did, which is every ask the fleet
has ever filed. **Not yet shipped**: the Decisions page's answered-but-unproven band, the
are-you-sure-gated override control, and `in_progress` ceasing to hide a row.

### Fixed — a resumed leg keeps the failure tally and the schema-off switch (D245)

`loop.failures` and `loop._schema_off` reset on every leg, so a call that failed once before a
restart and once after it read as two FIRST failures — and the fix-the-cause repair hint, which
fires at the second failure of one call, never fired in the runs most likely to resume into the
same wall. Both are now rebuilt from the transcript as a `guardscope` layer
(`engine/resumedcounters.py`), with no payload extension needed: the failure key is a pure
function of an action and its own observation, and the schema-off decision was already recorded
as an `error` event nobody read back. A HELD action is not tallied, because the rebuild asks the
same `grounds_a_finish` gate the live loop does.

### Fixed — abort by run id, and the runner's active map keyed per home (D247)

`runner.active` was keyed by slug across all three homes, so a routine, a conversation and a
background task sharing a slug collided — and the web abort named a run id the runner resolved
back to a slug. The map is now keyed `(home, slug)`; `Runner.abort(run_id, home="")` takes the
id the route already had and aborts NOTHING when two homes answer to it, because run ids are
not unique across homes; `is_active(home, slug)` makes a missed caller break loudly rather than
read the wrong home. No slug-keyed path is left alive. New `daemon/active_map.py`, because
`runner.py` was at its size ceiling.

**`/api/status`'s `active_runs` is now keyed `home/slug`** — a payload shape change. Every
consumer found reads the key count or the values rather than the key text.

### Changed — the engine's action deadlines are SERVED, and the finish line's day is the scheduler's (D252)

`static/components/actiontime.js` carried its own copy of the engine's action timeouts
(`{util: 300, script: 300, shell: 120, wait: 600}`), so a console row could contradict the
engine that would actually kill the call. `/api/status` now serves `action_limits`, built in
`web/actionlimits.py` from the engine's own constants with no number typed in it — which
required naming the one that was a bare literal, `waitop.WAIT_DEFAULT_TIMEOUT_S`, now read by
the deadline itself. `static/action-limits.js` is the console's one reader (an action's own
`timeout_s` still wins) and the copy is gone with no fallback; a row distinguishes a limit, a
known absence of one, and not knowing yet.

`finish-line.js` computed "today" on the VIEWER's clock, so a date outcome near midnight read
differently from another zone than from the box that retires the routine. `api_finishline`
serves the scheduler's own local day and the client judges against it. The pre-existing test
pinned the defect and was replaced; the two new ones sit in opposite zones (UTC+14, UTC-10) so
neither can pass by accident of the host's zone.

The third part of that entry — the settings digest never naming `rule_confirm` — is NOT built;
its `docs/designs.md` entry is shortened to that remainder rather than deleted.

### Documentation — three conventions the review found missing (D254)

CLAUDE.md now carries the blob-URL rule behind 0.371.0's token-theft fix (a `blob:` document
inherits the console's origin, so a new tab opens only through `components/blobtab.js`; whoever
mints an object URL revokes it), that `~/.credentials` is optional in the deploy inventory, and
that `tests/test_deploy_state.py` pins that inventory against compose. Each claim was verified
against the tree before it was written down.

## [0.518.0] — 2026-11-08

### Changed — the gate for the next fire is the last run's to write; closures stop buying fires

Measured on the instance before the change: 118 gated fires across 8 routines, ONE skip. About
half were admitted before any check was asked — 43 by the inbox, 10 of those by nothing but
`closes: true` report replies (nine of them rules-review's). The checks that WERE asked answered
"work" every time because each watched a level no run drained: a request queue waiting on air
dates (14 of 14), models nobody could test (13 of 13), a phase value the idle list did not know
(7 of 7), a state key that did not exist (4 of 5). The operator's design (2026-11-08): after each
run, the routine writes the gates for the next one from its own work context.

- **Closures are not freight.** The gate's inbox predicate (`daemon/gate_builtin.pending_inbox`)
  leaves out `closes: true` replies, as the report trigger always has: a closure asks nothing
  and waits unconsumed for the next admitted run, bounded by the `max_quiet` backstop.
- **Run-written gates** (`rsched/gatecompose.py`). A run writes `state/next-gate.json` —
  `{"why", "checks"}` in the gate's own check vocabulary — and for the next scheduled fire those
  checks REPLACE the routine's (`scripts/admit.py` included; adding specific checks beside
  general ones could only make skips rarer). Asked once at boot, in the digest's new NEXT FIRE'S
  GATE block (`engine/gate_digest.py`): what is in force, how the gate has done, the routine's
  own checks and the check kinds. Floors no run moves: the built-in reasons; the routine's own
  `max_quiet`, always asked as `backstop`, which a run may shorten and never lengthen (a gate
  with none takes no written gate at all); `script` is not a run's to name. An unusable file —
  unparseable, invalid, lengthening the floor, an ungranted path or secret — is SET ASIDE, never
  a failed fire: the routine's checks are asked and `gate.json` records `source.rejected`, which
  the next run is shown. `gate.json` now records `source` (whose checks were asked), the run
  history's verdict hover shows it, and the job ledger maps a written check's `job:` tag from it
  (`gatecompose.checks_in_force`). *Test the gate now* never reads the file. **On by default,
  with an off switch per routine**: `run_gate.run_written` (the Run gate section's *let each run
  write the checks for the next fire* box) — off, the routine's checks are asked on every fire
  and no run is asked to write any.
- **What the gate has done** (`readmodels/gate_yield.py`): per routine check, how often it was
  asked and found work over the last 20 judged fires. A check that answered work on every one
  of at least 5 asks has never let a fire skip — a setup-surface NOTE `gate:<id>` with its newest
  reason and the new remedy `edit_gate` (lands on that check's row, `data-gate-check-id`), and a
  line in the run's NEXT FIRE'S GATE block.
- The built-in reasons moved out of `daemon/gate_prepare.py` (at its size ceiling) into
  `daemon/gate_builtin.py`; `gate_prepare.py` is back under the bar (383 → 331).

## [0.517.1] — 2026-11-08

### Fixed — the ungated-write marker flagged read-only commands two ways (F855)

- **A shape's scan read past `&&`.** `engine/shellwrite._WRITE_SHAPES` bounded its multi-token
  shapes with `[^|;]`, which excludes a pipe and a semicolon but not an ampersand — so `&&`,
  which ends a command as surely as either, was scanned straight through. Measured on
  `self-audit:20261107-233235` turn 4: a pure read ending `… && git worktree list && echo
  "=== STASH ===" && git stash list` matched as one apparent `git … stash` and filed an
  `ungated_write` health event (1 of the 5 in that window). The same hole sat in the in-place-sed
  shape, where `sed -n '1,5p' f && grep -i x log` took grep's `-i` for sed's.
- **`git stash list` and `git stash show` are reads.** `stash` is the only verb in that shape's
  set with read-only subcommands of its own, and `git stash list` is precisely what the house
  rules tell a run to do before popping anything — so marking it taught the opposite lesson.
  Bare `git stash`, `push`, `pop`, `apply` and `drop` still flag, including when they carry a
  `stash@{n}` ref, which is itself now pinned: the ref contains the word `stash` and must not
  be read as the verb.
- The interpreter clause is bounded only up to its `-c`/`-`/`<<` flag. The program body after
  it may legitimately contain `&&`, so bounding that would lose real writes — the separator rule
  is about which COMMAND a flag belongs to, never about what the program may say.
- Bidirectional probe: with the ampersand and the stash guard removed, exactly the 4 new pins
  failed and the 39 pre-existing shape tests held; restored, 43/43 green.

## [0.517.0] — 2026-11-07

### Added — a `kind` that is really a field or a util is told so, instead of getting the enum

- `kind` is the one field whose rejection the enum cannot answer: the validator lists all 34
  current kinds and the thing the run actually wanted is not among them. Two fleet runs lost a
  turn to this in one evening (`wrong_turn_detected`, reason `schema_rejected`):
  `tv-show-tracker-seedbox-manager:20261107-170000` turn 27 sent `kind: "note"` — a rider that
  costs no turn — and `deficit-coach:20261107-230003` turn 10 sent `kind: "code-search"`, a util.
- `shiftedfields.unkind_diagnosis` now names the other place that word belongs: for a FIELD it
  quotes the contract's own first sentence about it and says a rider belongs beside `kind`, never
  in it; for a util it gives the call shape `kind="util", name="<util>"` plus how to look its
  usage up. Wired into `completion.diagnose`, the one composer every schema-rejection path reads
  — beside `renamedkinds.hint`, which has done this since 0.416.0 for a kind that was RENAMED.
- Narrow on purpose, because a diagnosis that fires on ordinary mistakes teaches the model to
  ignore it: the kind must be UNKNOWN first (so `delete`, which is both a real kind and a schema
  field, is never diagnosed), the field branch reads `ACTION_SCHEMA` at call time (so a renamed
  field cannot leave a correction pointing at one that is gone), and the util branch rests on a
  property of the vocabulary that its own test asserts — **no action kind contains a hyphen**.

## [0.516.3] — 2026-11-07

### Fixed — a child's hand-back named every file its slot had ever accumulated

- `engine/child.collect_handback` listed the DESTINATION (`dst.rglob("*")`) to answer what a
  child handed back. The landing slot is namespaced by the child's **number**, not its run, so
  every run's child #1 lands in the same `artifacts/from-sub-1/` and that directory accumulates
  across runs. Measured on 2026-11-07: a child that wrote **2** files was announced to its
  parent as having handed back **66**, the other 64 dating from 2026-09-11 to 2026-10-27 and
  including whole nested `from-sub-2`…`from-sub-6` trees of earlier runs — the real deliverable
  was the 36th name in the list.
- The answer is now enumerated from the SOURCE and confirmed against the destination. The copy
  stays additive and nothing is deleted: a child may hand back more than once as it goes, and
  another run's deliverables are not this run's to remove. Only what the parent is TOLD is
  narrowed to this child.
- This reached every routine that spawns a child, in `wait` observations and turn-boundary
  announcements alike (`handback_paths_line` has one spelling for both).

## [0.516.2] — 2026-11-07

### Fixed — a "no change" recipe revision still filed a decision card when it gave its reason

- `engine/recipeproposal.extract` matched its closed null-verdict set against the **whole**
  revision line, so it only ever caught the bare wording. The ask asks what THIS run showed, so
  a complying run writes its verdict **and** its reason — `RECIPE REVISION: none — the recipe
  served this run as written; …` — and those got through: **four of the five cards filed on
  2026-11-06/07 were null ones** (`freelance-hunter` twice, `moltbook-heartbeat`,
  `uncensored-model-radar`), each an empty decision the operator had to edit and approve.
  R3086's fix (0.482.0) was right in kind and too narrow in reach.
- The null set is now matched against the line's **opening clause** (`_verdict`, cut at the
  first `—`/`–`/`;`/`:`/`.`/`,`/` - `), because a revision line's verdict is its first clause
  and the rest is its reason. A real revision's opening clause names the FILE it would change,
  which is why reading only that clause cannot eat one — pinned both ways, including the lines
  that merely OPEN with a null word (`none of the stages name the cursor, …`).
- Both seams `extract` feeds are fixed at once: the Decisions-page card (`engine/loopend.py`)
  and the `Recipe revisions:` block of the routine's `LEDGER.md` (`engine/ledgerentry.py`).
- Operator, 2026-11-07: *"if a routine decides it doesn't need to change its recipe, I don't
  want a "no change" message /decision, I want no message"*.

## [0.516.1] — 2026-11-07

### Fixed — a "docs-only release needs no restart" claim that cannot be true of any release

0.515.0's commit message concluded that because the release changed only docs and tests it
"needs no daemon restart". `ship_row` contradicted it in the same breath by requesting one, and
`ship_row` was right: every release bumps `src/rsched/__init__.py`, which is the version the
running daemon reports at `/api/status` and renders in the console header — so a docs-only
release does not exist, and the version a reader is shown stays stale until the restart. Nothing
was mis-shipped; the sentence was wrong, and a pushed commit message cannot be corrected, so the
correction is recorded in 0.515.0's own CHANGELOG entry where a reader meets the claim.

## [0.516.0] — 2026-11-07

### Changed — the pre-finish hold that asks a run to answer a hand-off decides from the hook stream (D152 step 8)

The hooks campaign's claim since step 4 is that the hook stream is the SOURCE and
`reports.jsonl` is its projection. Step 7 made one decision answer from the stream (the write
path's open-thread cap); this is the **second**, and it is the one that tests the claim where it
matters: `assist_predicates._unclosed_delivered_report` asks, at a run's finish, which handed-in
reports it still owes a reply to — over rows **every other routine wrote**, not one sender's own
filings. So it is the first use that depends on the pre-stream backfill being complete across
the fleet rather than within a thread, which is exactly what step 7 measured (5,016 rows, zero
round-trip mismatches) and could not otherwise exercise.

`hooks/reportinv.still_owed_now` mirrors `open_threads_now`: both stores asked, the stream
folded through `reports.fold_rows` before comparing (F847's trap — the projection writes a
filing and each lifecycle fact as separate rows, while the open/closed predicate reads flags
only the fold puts on a filing row), and the **ledger's answer kept** whenever they differ, with
`hook_stream_diverged` filed instead. That conservatism is the point rather than a hedge: this
decision HOLDS a run's finish, so answering from a stream missing history would ask a run again
for a reply it had already sent — the exact failure R2086 fixed by re-reading the ledger at the
moment of asking. The filing of that event is now one shared function, because the diagnosis is
identical for every decision moved and a second copy would be a second thing to keep true.

`docs/architecture.md` now names which decisions read the stream — a reader could not previously
tell that the campaign had begun closing the gap `hooks/store.py`'s own docstring describes
("nothing reads this stream to make a decision yet"), and ten modules still fold the projection.

## [0.515.0] — 2026-11-07

### Added — §5 of `docs/prompt-anatomy.md` is verbatim in all four of its parts (D246 item 2, closing the entry)

The order read *"§5's full verbatim example is stale: regenerate it from a composed prompt"*.
Measured before editing, the premise was void: §5.2's kickoff and util-reminder tail, §5.3's
budget-left line and §5.4's `converge()` and history tails all still matched their composers
exactly — F672's fix plus this file's two existing tests had closed the gap §5.1 was the symptom
of, and a regeneration would have changed nothing.

So the real remainder was the **missing pin**, which is also why the first drift went a month
unseen: the section calls itself verbatim and exactly one of its four parts was checked.
`test_the_rest_of_section_5_quotes_the_engine_verbatim_too` now holds the other three against
`composer.kickoff_message`, `loopnudge.build_util_reminder`, `turnwindow.converge` and the two
per-turn tail shapes.

A finding from proving it red: the kickoff is quoted **twice** in that document (an earlier
walk-through and §5.2), and the first perturbation hit the other copy, so the test passed on a
doc that had been changed. A pin on one copy leaves the other free to drift — the same
one-of-N gap at a smaller scale — so the test asserts that EVERY `Begin run …` line in the
document equals `kickoff_message`'s output, and that there is more than one of them.

With this, every item of the **"Model-facing wording that drifted"** order is shipped — (5) in
0.506.0, (1)(3)(4)(6) in 0.514.0, (2) here — and its `docs/designs.md` entry is deleted in this
release. 28 entries remain.

> **Correction to this release's own commit message**, recorded here because a pushed message
> cannot be fixed without rewriting history. It ends *"Docs and tests only: no src/ or static/
> change, so this release needs no daemon restart."* The first half is true of the item; the
> conclusion is false, and `ship_row` was right to contradict it by requesting a restart. **Every**
> release changes `src/rsched/__init__.py` — the version bump itself — and that is the string the
> running daemon reports at `/api/status` and renders in the console header. A "docs-only release"
> therefore does not exist: the version a reader is shown stays stale until the daemon restarts.
> Nothing was mis-shipped (the restart was requested); the sentence was simply wrong.

## [0.514.0] — 2026-11-07

### Fixed — one child exit, one headline (D246 item 1)

A child's exit is reported by two paths — the turn-boundary hook and a `wait` the parent was
already sitting in — and which one gets there first is a timing race. They said it two ways:
the hook `CHILD RUN FINISHED (parallel child run) — #1 'x' (pattern p, status ok, 7 turns)`,
the wait `SUB-WORKFLOW 1 'x' FINISHED (status ok, 7 turns)`, off a second mode vocabulary
(`MODE_SHOUT`) whose only caller that was. `docs/child-runs.md` already stated the rule the
wait broke — *one headline whatever its mode, with the mode named inside it rather than
changing the noun* (F338) — so the table is **deleted** rather than re-worded, and
`child.finished_headline` is now the only place that sentence exists. The hook's one extra is
the child's `pattern`, which a wait does not carry. Pinned over every mode, asserting the
hook's line IS the shared sentence with the pattern named inside it.

### Fixed — neither hold promises that a re-emit runs (D246 item 3)

`hold.before_dispatch` returns at the FIRST matching source (consequence reminders before
general rules) and each layer spends only its own ledger key, so "emit the SAME action again
— **it runs this time**" was a promise the other layer could still break: a run held twice
having been promised once, which reads as the seam ignoring its own confirmation. Both
sentences now name their own layer and say the other is a separate hold that may still fire
once. A **second copy** of the same false promise was found and fixed in
`static/components/obs-text.js`, which composes the hold a *person* reads in the transcript
and had drifted to "runs it" on its own — `tests/ui/test_transcript_events.py` pinned the
console's copy, so fixing the engine alone would have left the page asserting the defect. The
new pin compares the two surfaces against each other, on the rendered prose rather than the
source's line breaks.

### Fixed — the `request` field named two allow shapes where there are three (D246 item 4)

The action schema said the user decides "once (this run) or forever". `requests.DECISIONS`
carries five decisions and **three** allow shapes — `allow_now` (this run), `allow_once` (ONE
action), `allow_forever` — and `allow_once` is offered for exactly `entities.ONCE_CLASSES`,
which is what a `request` takes. Paid line-neutrally at `actionschema.py` 629/629, and its
+10 chars of always-on prompt contract were paid back **inside the same description** rather
than by raising the budget ceiling (which this release's first gate arming caught as a red):
the contract is locked one char lower at 64396, with the reason in `tests/prompt_budget.json`.
§5.1's schema dump re-rendered from source.

### Fixed — a resumed leg with nothing above it is framed as the kickoff it is (D246 item 6)

The resume note told EVERY resumed leg *"the conversation above is the run so far — continue
from the last observation; do NOT restart from step 1"*. But `rewind_transcript`'s
`before_reply` cut keeps everything BEFORE the message that opened a reply, and for a
conversation's FIRST reply that is the header alone — which is exactly what the operator's ⚑
refusal flag does (discard the reply, re-send its message to another model). So the commonest
route to an empty window was the one case where both halves of the sentence were false,
leaving the run no permitted action at all. `rewind.resume_framing` now picks between two
sentences on whether the replayed window holds an ASSISTANT turn; the empty one negates both
halves, says why the window is empty so the leg does not read it as a malfunction, and notes
that anything an earlier attempt wrote to disk survives.

### Added — every guide in `docs/` is claims-checked (R3247)

`tests/test_doc_claims.py` extends what `tests/test_doc_counts.py` did for two documents to
all **39** guides, for the claim types a machine can settle: a written-out count against the
`len()` of what it counts, a backticked identifier against the source, and a cited path or
`path:line` against the file. Four non-vacuity guards back it — including one that reds when
an allowlist exemption outlives its reason — because a claims-check that silently stops
finding anything is worse than none. Out of scope and declared: `designs.md`'s symbols (it is
the unshipped work queue), `status-pages.md` (a foreign codebase), and whether a cited line
still *says* what the prose claims.

It found **5 drifted claims across 3 guides, 8 sites**, fixed prose-to-source: `16 rules` →
17 (`library-seed/rules/` holds 17), `19 docs` → 18, the `DEFAULT_RULES` sentence naming 6 of
7 slugs (`outcome-first` missing, in both guides), the rules table missing its `outcome-first`
row, and **`decompose_decision()`** — a function that exists nowhere in the repo — cited in
`architecture.md` and `child-runs.md`, where `general-task.py:189` actually carries
`separable(work)`.

### Changed — `docs/items.md` records what `--wrongly-settled` answers today (R3246)

0.513.0 shipped the verb; this adds the arithmetic behind its count, which is **236 rows, 69
never routed** and not D182's 492. The raw predicate still reproduces 495/319 — the
difference is two rules decided since: **246** of those claims are `self-audit` disposing of
an UNROUTED row, which F840 made its own act as `TRIAGE_READER`, and **22** are later reports
re-naming an id whose first disposal already settled it.

## [0.513.0] — 2026-11-07

### Fixed
- **The escalation-ladder switch on a CONVERSATION said it was supervising "runs of this
  routine".** D216(d) (0.504.0) mounted the routine page's `ladderSwitch` on a conversation's
  capabilities panel and shipped its wording with it, so all three of its strings named the
  wrong subject: a conversation has REPLIES, not runs of a routine, and the control therefore
  asserted something false about the very thing it switches on. `ladderSwitch` now takes a
  `subject` (a key of a `SUBJECTS` table, defaulting to `runs`, so the routine page is
  byte-identical) and `ladderPanel` — whose only home is a conversation — passes `replies`.
  Pinned in the BROWSER on the text a person reads, in both homes and in both directions (the
  old words must be absent), because the three existing browser tests for that panel all passed
  over the wrong prose: every one of them asserts a `data-ladder-*` hook or a file that was
  written.
- **`daemon/backup_watch` reported a backup root it may not LOOK at as one with no stamp**
  (F846, the operator's "build the ENOENT/EACCES split"). `newest_stamp` swallowed every
  `OSError` into `continue`, so "the file is not there" and "I am not allowed to read it"
  produced identical output — and the detail then asserted one cause out of three ("the share
  may not be mounted"), which for the local tier was simply wrong. It now returns the roots this
  process was REFUSED (`EACCES`/`EPERM`; `FileNotFoundError` stays the ordinary "it did not run"
  path) and says so instead of naming a cause it cannot know. `unknown` is still the state and
  `cli_postdeploy.backup_freshness` still FAILs on it, deliberately. Measured rather than
  assumed, because a fix firing for no reader would be inert while looking shipped: the daemon
  runs as uid 1000 and OWNS `/mnt/usb500` (`drwx------`), so the refused branch is a RUN's (and
  `cli_postdeploy` inside one), never the daemon's tick.

### Changed
- **The five regression-alarm constants that were never measured, measured** (R3245, accepted:
  *"Yeah. Do that!"*). D181 computed the distribution for `BALLOON_RATIO` alone; its siblings
  carried plausible prose. Over this instance's own stream (4,877 records → 1,997 folded depth-0
  runs, 227 actors, as adjacent same-actor window pairs where nothing changed between the halves)
  **no constant had to move**, and three turned out to carry load nobody had written down:
  `FAIL_RATE_JUMP` +0.4 sits at the **p99** and is the only value distinct from 0.2 (a 5-run fail
  rate moves in 0.2 steps, so 0.3 flags the same 40 of 1,412 windows); `MIN_RUNS` 3 is what stops
  ONE failure deciding a verdict (at a window of 2 it is a delta of 0.5 and clears the threshold
  alone); `VOLUME_FLOOR` is the WHOLE decision for the 66 windows whose before half has no
  reading, where any growth satisfies the ratio. `TURNS_FLOOR` is **inert** — every candidate from
  3 to 13 flags the same 4 windows. Each number is now in its own comment, the three load-bearing
  facts are pinned in `tests/test_balloon_ratio.py` (each proven red by perturbing its constant),
  and `docs/run-analytics.md` states when these should be re-measured.

### Added
- **`rsched reports --wrongly-settled`: which reports a non-owner disposed of** (R3246,
  accepted). D182 closed the gate in `report_threads.disposable` but left the rows settled
  deliberately — and "do not re-open them" and "nobody can ever see which ones they were" are
  different decisions, only the first of which was taken; until now the sole artefact that knew
  the number was a docstring. `report_threads.disposed_by_non_owner` asks `disposable`'s own
  question backwards, one call per disposer, so the listing can never disagree with the refusal a
  run would get today. It is a READ: no row re-opens, no verdict changes. On the live ledger it
  lists **236 rows, 69 never routed**, including the operator's own `R2552`; the raw predicate
  still gives D182's **495/319**, and the difference is 246 claims that F840 made triage's own act
  plus 22 later re-claims — `docs/items.md` carries that arithmetic so the two numbers cannot read
  as a contradiction.

## [0.512.0] — 2026-11-07

### Fixed
- **A `config_patch` granting a capability no held permission requires is refused at filing,
  instead of applying successfully and writing the mapping without it.** `capabilities` is a
  real `RoutinePatch` field, so `patch_shape` passed it and `patch_nested` never looked inside;
  the apply routes it to the permissions surface, which RAISES the mapping to cover every held
  doc's `requires:` and FLOORS it back to them (D8) — a gated action survives only while a held
  permission names it. A patch naming only `capabilities` keeps the routine's current docs, so
  a gated action with no permission behind it could never land, and nothing said so.
  `engine/config_values.capability_value` now compares the patch against the apply's own floor
  (`grants.floor_capabilities` over `grants.capabilities_for`, same doc set) and names the
  permission that would carry the capability, so the correct filing is one patch away.
  *Measured, and this is the shape that makes it worse than a dead button:* on 2026-11-06 at
  03:25:57 a patch `{"capabilities": {"actions": ["write_recipe"]}}` for `deficit-coach` (which
  holds `util-authoring` alone) was applied and recorded `config_patch_applied: true`. The
  written file came out `actions: [write_util, revise_util]`. The asking routine reported to the
  target that the capability had been granted; the target's next run tried `edit_file main.md`,
  was refused for want of it, and filed a **blocking** question asking for a grant it had
  already been told it had (`q-20261106-230001-55`). The button was not dead — it lied.
  Never stricter than the apply, by construction: the floor compared against IS the apply's.
  Two cases plus the standing no-stricter property in `tests/test_config_patch_values.py`,
  proven to discriminate (with the check neutered, exactly those two fail and the other 12 pass).

## [0.511.0] — 2026-11-07

### Nineteen refusals in eleven routines told a run to supply a report id that cannot exist

- `kind=report: 'closes' is valid only together with 'answers' or 'settles'` is the most common
  schema rejection on this instance — **19 events across 11 routines** between 2026-10-19 and
  2026-11-06, with `llmsectest-weekday` meeting it in **five separate runs**. Each one costs the
  routine its turn.
- The refusal was accurate for a routine target and **unactionable for a run-tree one**. A
  `target: parent` or `child:<label>` report is a MESSAGE to a live run, read at its next turn
  boundary — `engine/runtree` says it "is not an item and owes the ledger nothing", so it never
  gets an `R` id. There is therefore no id `answers` could name, and a run told to supply one
  cannot comply: of the five rejected actions still recoverable from their transcripts, **three
  were `parent`/`child:` replies to a hand-back the run was trying to close inside the same
  run**, which is exactly the behaviour the contract asks for elsewhere.
- The refusal is now target-aware: for a run-tree address it says to **drop `closes`**, names
  the target that makes it so, says there is no report id to name, and points at saying "no
  reply needed" in `detail` instead. For a routine slug — and for no target at all — the
  original sentence is byte-identical, because a refusal a run has learned to read is a surface
  too (`reportfields`' own rule). **No grammar changed**: the same shapes are refused and
  accepted as before.
- `tests/test_report_closes_refusal.py` carries both: the run-tree refusal asserted for
  `parent` AND `child:<label>` (naming the action, the target and the absent id, and asserting
  it does NOT read as a demand for a field the shape cannot have), and a pin that a slug target
  and an absent target still get the original wording. Shown red with the branch removed.
  `tests/test_reports.py` is at its size-ratchet ceiling (760), so the cases went to the file
  that already owns this refusal.

## [0.510.0] — 2026-11-07

### A release gate was running the daemon's own memory cgroup to its ceiling for two minutes at a time

- The FAST stage has peaked at **89–100 % of `memory.max`** (5 GiB) on every one of the **27
  covering stages recorded since 2026-11-01** — exactly **100.0 % on 14 of them** — and the
  stage's own sampler counted the cgroup hitting its ceiling 238, 306, 385, 700, 803, 902,
  1,203, 1,254, 1,918, 2,696, 2,889, 3,458 and once **6,399** times inside a single 95–340 s
  stage. That cgroup is the ENGINE container's: the daemon and every live run share it, so a
  stage sitting at the ceiling for three minutes is the whole box's allocation failure, not the
  gate's own. The `ui` stage was never part of this (43–55 % of its own 4 GiB `uitest`
  container).
- Neither existing guard bounded it, which is why six workers looked safe: `FAST_WORKERS` was
  chosen for WALL CLOCK (2026-11-05: "six workers ran the suite in 110 s against 72 s at
  eleven"), and `tests/memgate.py` waits on the HOST's `MemAvailable`, not on the container the
  stage actually runs in. Both can be satisfied while the stage runs its own cgroup dry.
- `FAST_WORKERS` 6 → **4**, on the same tree and selection: `-n 6` 141–173 s at 96–100 % with
  287–1,254 ceiling events; `-n 4` 213 s at **78.0 %** attached, and **199.9 s at 88.5 % with no
  ceiling events** on the release gate that shipped this very change. It costs ~40–70 s per fast
  stage and buys headroom no other knob was providing. Same reasoning `memgate` already states:
  a gate is the one heavy load on this host that can wait, and nobody is watching it run.
- **What that does and does not prove**, since one gate is one gate: the peak moved off a hard
  100.0 % to 88.5 %, which is the sounder signal. Zero ceiling events is NOT yet a property of
  four workers — two six-worker gates also recorded none (96.2 % with none, 98.9 % with 8). The
  next few gates decide whether the bound holds, and the evidence to judge them by is already
  written per gate in `.gate-evidence/memory-peaks.jsonl`.
- `tests/test_memgate.py` pins the RELATION between the two, not the numbers: `memgate`'s own
  docstring says its budgets ARE "the measured peaks of the selections: the fast suite at
  FAST_WORKERS workers (~250 MB each)", which makes `NEED_MB` arithmetic over a constant in
  another module that nothing checked — and a too-small budget does not fail, it simply stops
  waiting when it should have waited. Either number may be re-tuned now, but not alone: shown
  red at `FAST_WORKERS = 9` before commit. **Not a past violation**, stated plainly because the
  tempting story is wrong: at six workers the floor was 1,500 MB against a 2048 MB budget, so
  the relation held throughout. What the measurement really shows is that the ~250 MB
  per-worker figure understates the cost about **fourfold** (3.90 GiB peak at four workers), so
  the floor is deliberately a weak lower bound and the cgroup headroom is what the gate's own
  evidence measures per run.
- No causal claim about run deaths: the 2026-11-05 `rc=-9` orphan burst does not overlap any
  gate window measured here, and `memory.events oom_kill` reads 0 from inside the container,
  where that counter is not meaningful.

## [0.509.0] — 2026-11-07

### Twice in seven days a routine lost its `uncensored` model role and every check on this instance stayed green

- A `models:` PATCH replaces the mapping wholesale — which is what makes "clear a role"
  expressible at all (`web/patch_bindings.py`, deliberately not merging) — so a console edit that
  re-sends the map without one key CLEARS that role. Nothing said so. `self-audit` ran for
  **three days** (commit `ede1738`, 2026-10-30 → 11-02) and `routine-improver` for **27 minutes**
  (`9181e0f`, 2026-11-06) with no `models.uncensored`, and the only reason either was found is
  that a config audit happened to resolve the role by hand (config-optimizer, R3227/R3228).
- Nothing could have told anyone: `load_routine` reported no problem for either routine, no
  health event was filed, and `EndpointRegistry.for_uncensored` returning `None` is a legitimate
  value meaning "referral off" — so the engine cannot distinguish a deliberate off from a loss.
  The one trace left behind was `efforts.uncensored` sitting there for a role that no longer
  resolved, and it is a tell only to somebody already looking.
- The consequence is a FAILED run rather than a degradation: with no uncensored target a
  CLASSIFIER refusal advances the fallback chain, every model in this instance's catalog carries
  `fallbacks: []`, `_switch_to_fallback` returns `None` and `engine/degrade` raises.
- `config/routine_scrub` now reports that exact shape — `efforts.uncensored` set with
  `models.uncensored` naming nothing — as a loader problem, which reaches the routine's own page
  and card through `RoutineInfo.problems` at its next load. It REPORTS and does not drop: the
  effort is correct again the moment the role is restored, and deleting it would erase the only
  evidence the role was ever configured.
- **Narrowed to `uncensored` on purpose.** `endpoints.for_model` falls back to the instance
  `system_model`, so an `efforts.main` or `efforts.tool_call` with no model of its own is
  legitimate — six routines on this instance are in that state — while `for_uncensored` has no
  fallback at all. Measured over all 30 installed routines before commit: **zero** fires, so it
  ships silent and only speaks when a role is actually lost.
- `tests/test_effort_setting.py`: the lost-role shape reported once and naming what is missing,
  how it was lost and what it costs; the three fallback-backed roles NOT reported; a deliberate
  off (no model, no effort) silent; and the effort surviving the report. Shown red with the check
  unwired before commit.

## [0.508.0] — 2026-11-07

### Nine wasted *approve & apply* clicks in nine minutes: a `config_patch`'s VALUES were judged only when the operator pressed the button

- `engine/config_bridge` judged a proposal's KEYS — which surface it is for, which fields that
  surface has, which inner keys each mapping field takes — and one VALUE, the model trial's. So a
  patch whose names were all real reached the Decisions page wearing an apply button that could
  not work, and the operator found that out by pressing it.
- Measured on this instance 2026-11-06, `.ui-traces/20261106.jsonl`, every toast `view=questions`:
  **`measures: List should have at most 6 items after validation, not 7` five times** between
  02:25 and 02:34 (an `outcome` contract one measure over `config/outcome.MAX_MEASURES`), and
  **`models.tool_call: must be a catalog model name` four times** between 02:33 and 02:34 (a role
  bound to a model this instance does not serve). `outcome` is a real `RoutinePatch` field, so the
  key check passed it and nothing looked inside; `patch_nested` judged the role NAME and stopped.
- Both are now refused on the turn that FILES the ask, with the vocabulary named: the outcome
  shape and its cap, or the catalog this instance actually serves. New module
  `engine/config_values.py` holds the value half — `config_bridge` was at 254 lines and the two
  checks took it to 355, over the house bar, so it was split rather than grown
  (`tests/test_file_size.py` rule 1). `config_bridge` re-exports them, so every existing caller
  and `tests/test_trials.py` are untouched by the move.
- **Deliberately no stricter than the apply**, which is its own failure mode — a filing check
  that refuses a proposal the click would have taken loses a change nobody can see was lost. So
  the two DECISION roles are judged against `server.decision_models` and not the chat catalog
  (which is why `trials.roles_problem` is *not* reused: it checks every role against the chat
  catalog and would refuse a valid `decision` proposal), a blank role still means "clear it back
  to the instance default", `{"outcome": null}` still clears the contract, and a model's
  context-window fit stays with the click because the engine may not import the web layer.
- `tests/test_config_patch_values.py`: the two measured cases, the catalog distinction both ways,
  a pin that every role in `ROUTINE_MODEL_ROLES` stays proposable through its own catalog, the
  standing "what the apply accepts is never refused here" property, and the end-to-end `ask_user`
  proof that no pending question file is written — shown red with the wiring removed (the pending
  record reappears) before it was committed.

## [0.507.1] — 2026-11-07

### `hook_stream_diverged` fired five times in a day and every one was spurious: the comparison folded one side only

- `hooks/reportinv.open_threads_now` compared the FOLDED report ledger against the UNFOLDED hook
  stream. `read_reports` gives the caller folded rows — a filing row carrying the
  `retracted`/`superseded`/`delivered` facts its lifecycle events wrote onto it — while
  `reportproj.stream_rows` is the raw projection, where those facts are separate rows. So
  `report_threads._is_open` could never see them on the stream side, and every thread disposed of
  by an event counted as still open there. The comparison was structurally incapable of agreeing
  for any sender with a folded or settled row.
- Measured on this instance 2026-11-06: **five `hook_stream_diverged` events in one day across
  three sender/owner pairs**, every one of them false. `scheduler-builder` → `self-audit` read
  **19** open threads on the stream against the ledger's **3**; folding the same stream gave 3.
  Over the whole live stream, 5,102 rows fold to 2,753 — the ledger's own row count. The
  divergence event exists to make a real unmirrored write findable (D152 step 7), so a check that
  cries wolf on healthy instances retires the signal it was built to carry.
- Fixed by folding the stream with the ledger's OWN fold (`reports.fold_rows`), which is exactly
  what `reportproj.rebuild` already documents as the only safe composition: *"never a second
  implementation of it, which is how two readings of one stream come to disagree."* The cap's
  ANSWER is unchanged — the ledger still decides — and an unreadable stream still never breaks a
  filing.
- `tests/test_hooks_decide.py::test_folding_the_stream_is_what_makes_the_two_agree` pins it
  through the live write path: three filings where the third supersedes the first two, the live
  shape that produced today's events. Proven to discriminate — with the fold removed it fails
  with `the stream says ['R1', 'R2', 'R3'], the ledger says ['R3']`.

## [0.507.0] — 2026-11-06

### A resumed leg reads what the live leg read: a replayed observation keeps its tails

**D245's first increment.** `EngineLoop._tails` appends everything that rides an observation
message AFTER the result itself — the reminder note, the rule-assist line, the archived-history
pointer, the spent once-grant notice, the repeat warning, the budget remainder. The observation
EVENT was written before any of them existed, so `history.replay_messages` rebuilt that message
WITHOUT them: **from the first such turn, a resumed leg's prompt differed from what the model had
actually read.** Two costs, both already counted in this codebase — the provider cache misses
from that message on (re-WRITTEN at 1.25x instead of re-read at 0.1x, which is exactly what
`engine/enginenote.py` exists to avoid), and cautions the model was shown are simply gone from
the leg that continues its work.

New `engine/obstails.py` owns the payload key `tails`; `loop.py` renders the tails BEFORE writing
the observation event and records the rendered string on it, **line-neutral at 435/435** (paid
for by collapsing the event call and one statement); `replay_messages` appends it, so the
replayed message is byte-identical to what the model read. An observation recorded before the key
existed reads `""`, so a run resumed across this change degrades to the old behaviour instead of
raising.

It is the RENDERED text, never the facts behind it: re-rendering at replay would recompute
`[BUDGET LEFT: …]` against the NEW leg's window and re-apply reminder ops that already ran, and
one string keeps the ORDER of several tails on one message true by construction. A payload
EXTENSION, not a new event type (`EVENT_TYPES` untouched) — the house rule `assist.recorded` and
`engine/replyactions.py` already follow. One cost is documented rather than hidden, in
`docs/prompt-anatomy.md` §3b: recording tails on the event forces rendering them before it is
written, so a crash inside the rare blocking `remind` approval now records neither the
observation nor its tails — the same window a pending `ask_user` already has. That §3b also
asserted *"The tail itself is not in the transcript"*, which this change makes false, and it is
corrected in it.

Part (a) of the same entry — `loop.failures` and `_schema_off` surviving a resume — is **not** in
this release and the designs entry stays. What it needs first is recorded: neither counter has a
layer module of its own and `loop.py` is at its ceiling, so their `rebuild` needs a new sibling.

## [0.506.0] — 2026-11-06

### A raise gives a run its turns back, and the most expensive output a run buys stops losing its middle

**A live budget RAISE now withdraws the spent reserved finish turn** (D244, review item E1.4).
The first budget violation grants one more turn with the schema narrowed to `finish`, so a run
ends in its own words instead of an engine string. Budgets are LIVE config, adopted at a turn
boundary. The two never met, and the reason is pure ORDERING: `EngineLoop.run` asks
`ctx.budget_spent()` at the TOP of its loop and calls `_turn_boundary()` after it — and the
boundary is where the raise is adopted. So the raise arrived one step too late **every time**,
and a run whose operator had just given it another 40 turns still stood in a finish-only grammar
until the next RUN. The withdrawal now happens where the raise lands
(`switches._withdraw_reserve`): the violation is re-asked, and if nothing is over its limit the
flag is cleared, the FULL action schema restored — the narrowed grammar is what actually stops
the run working — and ONE engine note says so, because a run told "this is your LAST turn" has
to be told just as plainly when that stops being true. A raise too small to clear the violation
changes nothing, so this cannot be used to walk past a budget by patching it upward by one.
The red-first test is worth a sentence of its own: the first version wrote `control.json` before
the run started, which gets it adopted at the FIRST boundary with nothing spent — the reserve
never fires and the test passes while proving nothing. The raise is now written from inside the
last pre-violation turn, and the probe is a `write_file`, which under the reserved turn's grammar
is not expressible at all — so a landed write proves the SCHEMA came back, not merely that a flag
flipped.

**An oversized `llm` reply spills instead of losing its middle** (D246's first increment, and the
only item in its group that loses information). `do_llm` ended with the head-and-tail truncation,
which elides the middle and keeps nothing — while `util`, `script` and `shell` have always written
the whole stream to `.util_outputs/` and carried a `[full output]` pointer to it. The one kind
whose output is a MODEL's answer, the most expensive thing a run buys and the least reproducible
since a re-ask returns a different reply, was the kind that threw it away. It now spills through
the same store with the same pointer sentence, appended by a new `outputs.observation_tail` at
the one place a rendered observation is assembled — so every kind that spills gets it, instead of
each renderer growing its own copy (`util`/`script`/`shell` are excluded there by name because
their shared body already emits it). Both obvious homes for that helper were full: the `llm`
renderer sits at exactly 350/350 and `observations.py` at 348, and putting it in the latter took
that file to 368, which the ratchet refuses to baseline — `outputs.py`, which owns the store and
the pointer sentence, is where it belonged anyway.

## [0.505.0] — 2026-11-06

### A hold can be labelled, a claim must have executed, and five migrations that had done their work are gone

**A rule assist's HOLD can finally be told apart from a false positive** (D239) — the last of the
six seams the 2026-09-22 review named and did not finish, and therefore the whole remainder of
**D143**, the oldest live decision in the backlog (handed 2026-09-24, 44 days). What leaving it
cost was measurable: the one `hold`-payload assist in the library,
`git-checkpoint/before-the-first-repo-edit`, stood at **31 / 31 / 10 fires** across
`llmsectest-weekday`, `self-audit` and `routine-improver` — 72 turns bought, with no way to tell
a useful hold from a false positive, which is the one number that decides whether a trigger keeps
a turn-costing rung. The entry's own first increment was "all four together: until every one
lands the label has nowhere to go", and all four are here: `remind_feedback` routed by id SHAPE
(a `/` means an assist key — `engine/assist_label.py`, with the one-label-per-hold ledger);
`state/assists.json` grown from an int per key to the reminder's own `STAT_FIELDS` exactly, with
a boot migration converting the live file in place; the health read model and its console table
carrying the four label columns, reusing the reminder row's class so no new CSS was needed; and
the `assist_hold` observation asking for the label the way `reminder_hold` does. Both files the
seams live in — `engine/remind.py` at 605/605 and `readmodels/run_health.py` at 350/350 — were
shrink-only, so two parts went into new siblings with line-neutral edits at the seams.
Three limits are stated rather than left to be found: the label inherits `remind_feedback`'s
`reminders` capability dial (so a routine with `reminders: none` cannot label a hold at all —
that gate is the operator's and was not widened; all three firing routines carry `reminders:
local`), `scaffold` and `do` still need a helper channel that does not exist, and
`REMIND_FEEDBACK_FIELD`'s `id` description still says "the reminder that fired", which is now
narrow.

**The finish's claim guard counted HELD and REFUSED actions as taken** (D243, review item E4.3).
`finishgate` handed it `{r["kind"] for r in loop.turn_records}` — and `turn_records` is appended
at the assistant-action stage, BEFORE dispatch. So it held every kind the model EMITTED,
including one a consequence reminder or a rule assist HELD (it never reached the dispatcher) and
one the reserved finish turn REFUSED. A summary saying "filed report to self-audit" therefore
**passed on a report that was held and never re-emitted**: the one guard whose whole job is
catching narrated-but-unperformed work was reading an unperformed action as performed. "Taken"
is now what EXECUTED — `loop.executed_kinds`, maintained beside `executed_actions` through the
one predicate `donothing.grounds_a_finish`, live (`record_grounding`) and on resume
(`grounding_from`), so a kind that cannot ground a finish cannot back a claim either. The
reserved turn's refusal is NOT a hold, so it had to be excluded explicitly — half the defect the
entry names. `loop.assist_undo_points` and `loop.reminder_owed`, set by their layers and
undeclared on `EngineLoop`, are declared; `loop.py` was at 437/437, so the lines were paid for by
dropping two DEAD `__all__` re-exports (`MAX_SCHEMA_ATTEMPTS` and `RunAborted` are defined in
`completion.py` and `control.py`, and every importer in the tree takes them from there).

**Five `MIGRATION(expires=2026-11-15)` markers are converged and deleted** (F841) — nine days
before they would have redded the FAST stage of every release gate on this host, for a defect
nobody created that day (the F701 precedent). They resolve to three migrations, two declaring
twice: `migrate_enabled` (`enabled.json` folded 37 routines on 2026-10-01; the only surviving
`schedule.disabled` key sits under `.archive/`, which its glob cannot reach), `migrate_seed_utils`
(`replaced: {}`, every util already current) and `deploy/backup.sh`'s `migrate_single_mirror`
(shipped 0.372.0, run before any rsync, so every attempt since performed it). Convergence was
VERIFIED per migration against the live `.control/migrations/` records and the homes, not taken
from the hand-over note — whose line number for `backup.sh` was already stale. Deleted with them:
both modules, both `cli_daemon` call sites, the shell function, three migration-only test files,
two now-impossible monkeypatch sites and six prose corrections. The end-state tests were kept
(`test_disabled_schedule.py` pins the one off switch across 10 assertions), so no new test was
written red-first — for a deletion the end state is the thing that must stay asserted. The boot
test is now STRONGER: with the stale monkeypatches gone, the minimal-stand-in boot runs every
surviving migration with nothing patched out. Proof: zero markers in `src/` or `deploy/` expire
on or before 2026-11-15, with 22 remaining and the earliest 2026-11-30.

**Two defects this release's own first gate exposed, both fixed here.** (1) The new boot
migration resolved its home as `getattr(server, "routines_home", "") or "."`, so a stand-in
server with no home wrote `.control/migrations/assist-stats.json` into the process's current
directory — the REPO CHECKOUT during a test run, which made the gate report its own worktree
dirty. The house pattern has no such fallback: a missing home now means do nothing. The existing
stand-in test asserted the return value and so could never catch it, because the write happens on
the way to that return; the new test asserts the directory is still EMPTY afterwards. (2) The
conversation-ladder BROWSER test waited for "a PATCH to /api/conversations/" and so could match
the PREVIOUS commit's in-flight response, then read `tuning.yaml` before its own write had
landed — green twice on its own, red in the gate's parallel stage AND its serial re-run. Each
commit now waits for the response to ITS OWN request, matched on the field that request's body
carries. No shipped code changed for either half of (2).

## [0.504.0] — 2026-11-06

### A failure is classified from what it DECLARED, a relative path finds its root, and a conversation can be supervised

**Three places decided retry, failover and prompt-window policy by matching ENGLISH SENTENCES in
a provider's error body.** The operator's order (2026-10-17, R3108): *"I want you to replace the
prose checks completely"*. New `endpoints/errorsignal.py` builds an `ErrorSignal` at the raise in
`endpoints/base.raise_for_status` from the WHOLE body — the HTTP status plus the provider's own
`error.code` / `error.type` / `error.param` / `error.metadata.limit_source`, which are a DECLARED
ENUM the vendor documents, where `message` is prose for a human. The crux is that
`EndpointError`'s message keeps only `resp.text[:300]`, so an error object past that cut reached
no decision point at all. `_OVERFLOW_HINTS` (4 phrases), `_NEVER_OVERFLOW_STATUS` (9),
`_FAILOVER_REASONS` (5 reasons / ~20 needles) and the `"in-flight requests"` string check are
**deleted**. Under the enum tables the fallback is structural, not prose: a structured 400/413
stating a token figure is an overflow whatever code it names, and a failure's class falls back to
its HTTP status — so a vendor nobody has met is covered by the SHAPE of its answer, and a new
enum token is an improvement rather than the difference between armed and disarmed. The two
classes that are the ENGINE's own finding (`refusal`, `empty`) are now declared at the raise
instead of written into a sentence and read back out of it.

**What a `no_structured_error` verdict makes the engine DO** — the residue the order leaves
undefined, and the reason this release names it (F843/R3209). A proxy in this instance's chain
(CLIProxy/OpenRouter-style) can relay an upstream failure as a bare body with no error object at
all. Two readings survive that: the HTTP status, and a declared enum token appearing bare in the
text; `structured` stays False so every site can tell the difference, and each site then fails in
a stated direction:
- **the window guard DECLINES** — no overflow is read, the prompt is not re-bounded, and
  `engine/errorclass.note_overflow_declined` files the row with `declined_by:
  no_structured_error`. The false positive would pin the run's window to a figure that was never
  a context size, which is the worse of the two errors.
- **a bare-body 402 is RETRYABLE** — `retryable_402` returns true when nothing is structured.
  Measured asymmetry: fatal cost conversation `c-20261016-075101` two deaths in 30 s at 0 rungs
  while the condition cleared itself; retryable costs two backoff sleeps before it fails exactly
  as before.
- **failover classes it `other`** — an honest bucket, never a guess that makes a new failure mode
  look like a known one.

**A relative action path now resolves in the allowed roots too** (R3233) — the operator, mid-run:
*"paths shouldn't have to be absolute"*. `paths.resolve_rel` consulted `extra_roots` only as a
permission check on an ABSOLUTE path, so a relative one joined the working directory and nowhere
else: it could never land in a granted root and died at `open()` with ENOENT while the file sat
in one — while the ACTION SCHEMA's own prose, mounted in every run's prompt, promises "path
relative to your working directory (or an allowed root)". He chose full symmetry, so the rule
(behind `prefer_existing`, which only `engine/run_context.resolve_action_path` passes — the one
funnel for read_file / write_file / edit_file / view_image / delete / move / mkdir) is: the
working directory first, then the first root HOLDING the path, then the first root holding its
PARENT directory — which is what makes a CREATING write symmetric, decided on evidence already
on disk rather than on which root is writable — else the plain join, exactly as before. **An
ancestor root is not an alternative**: the rule as decided redirected an open JOB's `write_file
state/progress.md` out of its workspace into the routine's own `state/`, caught by a
pre-existing test, so a root containing the working directory is skipped.

**A conversation can now be supervised the way a routine can** — D216 part (d), the last of the
parity campaign he answered *"All six, as one parity campaign"* and ordered *"(c), (b), (a),
(d)"*. The engine never gated the ladder on being a routine (`engine/ladder.ladder_settings`
reads the config object composed at each reply's boot, and a conversation IS a `RoutineConfig`),
so the gap was the surfaces: `ladder` + both turn knobs were absent from
`CONVERSATION_PATCH_FIELDS` and from `ConversationPatch`, which is `extra="forbid"` — naming one
was a 422. The conversation GET now serves the block and its derived rung budget, the header
mounts `ladderPanel` (the routine page's own two controls, composed), and
`web/patch_merge.validate_tuning`/`apply_tuning` are **shared by both PATCHes** so a
conversation's floors are the engine's own rather than a second copy. Found while moving them:
the routine PATCH's MIXED-patch path wrote tuning with the `oversight_turns: 0` → removal
conversion skipped. The header also gains the **supervisor** picker, and had to: `validate_models`
replaces the role block wholesale, so a `supervisor` written into a conversation's `routine.yaml`
by hand was ERASED by the next model save anyone made from that header.

**An empty `tools:` list in a workflow allowed EVERY action** (D242, review item E1.6).
`loopsetup.configure` read the allowlist with a falsy test, and `[]` is falsy — so a workflow
declaring `tools: []` (the always-kinds and nothing else) was given the full `KINDS` surface with
the schema projected from it. A restriction that fails OPEN, and nothing downstream could catch
it: both other readers already test `is None`, so the list reached them already turned into
"unrestricted". Now `is not None`; an ABSENT `tools:` key still means every kind. Guard cost
measured rather than assumed: exactly one routine declares `tools:` at all (a 7-item list) and
nothing in the seed library or any live `main.md` declares an empty one, so no routine's
behaviour changes today — this closes the path before someone writes `tools: []` and is silently
given everything.

**A stale worktree survived a teardown that reported completeness** (F842). `.audit-wt/otari-cost`
on branch `wt-otari-cost` sat six days after the sweep that claimed every one was removed:
`scheduler-builder`'s teardown enumerated `audit/*` on ORIGIN and its own guesses, so a worktree
on a branch outside that namespace was invisible to the sweep AND untearable by name. Its script
now reads `git worktree list` — git's own record, which cannot drift from the disk — and takes a
worktree's branch from it instead of assuming `audit/<item>`.

## [0.503.0] — 2026-11-06

### A run's turns belong to its task — bookkeeping is the engine's, and its absence is measured

The operator's finding, measured by hand over the last ≤4 runs of all 24 active routines (~10,500
turns): **12 routines spent more turns on their own process than on their task** (library-sync
25% task, predatory-publisher-tarpit 38, tv-show-tracker 39, fau 42, llmsectest-weekday 42 —
whose close-out alone was 44% of every turn —, uncensored-model-radar 43, vodafone-outage-claims
43, rules-review 43, self-audit 45 and 17% in its latest run, moltbook-heartbeat 48,
config-optimizer 48, token-lab 49). The operator's standard: that is a critical failure for any
routine, and it must not be able to happen again. The causes were fleet-wide, not per routine,
so the fix is four principles, each with its own enforcement point:

- **P1 — the engine writes the process record; the model never does.** One `LEDGER.md` entry per
  finished routine run leg, built from what the leg already produced (summary, accounting, recipe
  revisions) by `engine/ledgerentry.py`; rotation of whole entries into `.ledger/<YYYY-MM>.md`
  past 32 KiB by `runledger.py`, never touching the preamble above the first engine heading.
  LEDGER.md and `.ledger/` are SEALED in both layers (the action gate via `storeseal`, the jail
  via `ownseal`) and refused by `PUT …/file`. The `ledger-untouched` assist is deleted, the
  `decision-record` rule no longer asks for an entry, every engine voice that asked for one is
  reworded, conversations no longer get a ledger (nothing read 134 of 138), and the engine
  contract's `write_file` example is a task write instead of `state/phase.json`.
- **P2 — a contract names only the task.** `workflows/taskcontract.py` holds the fixed
  vocabulary of "the run's own record". The generator (`generate.FORMAT_SPEC`) no longer imports
  a ledger and forbids recording steps, per-stage phase writes and process-only DONE_WHEN lines;
  the materialization prompts (moved to `workflows/pipeline_prompts.py`) carry the same contract
  and a reply breaking it is retried. The machine checks: a library pattern fails lint for a
  ledger import, a ledger-write instruction or a DONE_WHEN line naming the run's record; a live
  recipe's process-only Done-when line or ledger-write sentence is a `FAIL  recipe ·` in
  `rsched validate` (exit 1). All 14 seed patterns and the live library were rewritten to pass.
- **P3 — a guard whose fix is mechanical applies it.** Consequence reminders hold once per
  REMINDER per run, not once per action string (~39 re-emits in four self-audit runs). The
  memory-note size is advice, not a refusal (`memops.MEMORY_NOTE_ADVISED_LINES`; the
  `frictions.memory_cap` signature now appears only in older transcripts). A clipped report says
  nothing needs re-filing, and the truncation marker no longer names a CLI verb that never
  existed. `wait` covers backgrounded calls (`engine/waitop.py`). A `util` naming the routine's
  own script leads with the exact `script` call, and a failed script carries its `usage:` block.
  An approved recipe-revision card resumes a NARROW edit leg (`engine/reviseleg.py`): recipe edit
  kinds only, a 12-turn backstop, no accounting or pre-finish assists, the proposing leg's verdict
  and `result.md` summary kept (34 measured legs: median 7 turns, longest 26).
- **P4 — overhead is measured every run and held as a fleet invariant.** `engine/overhead.py`
  classifies every landed action TASK / PROCESS / FRICTION, deterministically and from the
  transcript alone; status.json and the usage row carry `overhead`. The median over the last
  runs MADE UNDER THE CURRENT RECIPE (judged from 3) above 0.27 — calibrated against the hand
  classification: 11 of 12 bookkeeping routines flagged, 0 of 8 healthy — files
  `process_overhead_high` (once a day, latch read from the stream itself) on the blocked-work
  surface, a `recipe:overhead` setup-surface row with a `revise_recipe` remedy (also the
  `rsched validate` NOTE), and one `PROCESS OVERHEAD` line beside the boot digest's
  recipe-revision ask. A one-shot boot migration measures the kept runs.

Outside the code: every routine's recipe (all 30) was repaired by hand once, against the same
four principles — ledger, rotation and per-stage phase instructions removed, duplicate and
same-run records removed, repeated state updates moved into one script call each (tested on
copies of live state), script call forms written into each main.md, process-only Done-when lines
rewritten as task outcomes. Memory notes contradicting the repaired recipes were aligned. Two
credentials found in plain text in routine records (a Silk Compute API key in a memory note, the
Moltbook bearer key in a recipe stage and every transcript that read it) now live in the secret
store as `SILK_COMPUTE_API_KEY` / `MOLTBOOK_KEY`, declared by the scripts that use them, with
every record copy redacted.

## [0.502.1] — 2026-11-05

### the screen's `-shared` flag is pinned, because two surfaces now depend on it (R3206)

`deploy/chrome-entrypoint.sh` starts x11vnc with `-shared`, which is what lets the console's
browser dock and the full-screen `#/browser` page hold a viewer connection at the same time:
`web/api_screen_view` opens an independent upstream socket per accepted viewer with no lock,
semaphore or viewer registry, so the RFB server's admission policy is the only thing deciding it.
Nothing in `tests/` asserted the flag. Until 0.501.0 the repo's own prose said the opposite —
"one viewer at a time" — and the dock-seat decision was taken against that belief, so the
property is both load-bearing and newly so. Dropping the flag in a tidy-up would make the two
surfaces contend for the screen, and the symptom (a viewer that hangs up whenever the other opens)
reads as a flaky relay rather than as a deployment change. One offline assertion in
`tests/test_browser_view_relay.py` reads the entrypoint's single `x11vnc` invocation and requires
it — proven to fail with the flag dropped and with it renamed to `-noshared`, and to survive
whitespace jitter.

## [0.502.0] — 2026-11-05

### the nightly backup packs the loose objects and leaves the regenerable caches off-box (R3205)

The operator chose both halves of R3205, verbatim: *"both (a) and (b)"*, after the measurement
showed D235's own decided option cuts only 7.2% of the transferred files. Measured against the
live transfer set: **69,251 loose git objects — 38% of the 170,029 files** the snapshot walks,
each one a round trip over sshfs, against `TimeoutStartSec=4h` where the last SUCCESSFUL run
finished with a 29-minute margin.

- **(a)** Loose objects in every repository under the home are packed before the snapshot walk,
  so the off-box tier transfers packs instead of tens of thousands of single files. `GC_REPOS=0`
  switches it off; it can neither fail the night nor delay it past its own guard.
- **(b)** The `.libreoffice` and per-routine `.secrets` caches — 14,312 + 22,093 files, ~20% of
  the walk's round trips — are excluded from the OFF-BOX tier only. Both regenerate. The LOCAL
  tier stays a complete copy, as R3204 requires.

### the report ledger reads back AS hooks, and the first decision moves onto the stream (D152 step 7)

The hooks campaign's remaining gap. Step 4 made the hook the SOURCE of every new report; what
it left open is what `hooks/store.py`'s own docstring said out loud — *"Nothing reads this stream
to make a decision yet"*. Ten modules fold `reports.jsonl` to answer something; none read the
stream.

- **`hooks/reportinv.hook_of_row`** — the projection read backwards, and EXACT rather than
  approximate: all **5,016 rows of the live ledger round-trip identically** (zero mismatches,
  pinned as a test). Lenient like `from_record`, and deliberately not routed through `Disposal`:
  one live row carries a `closes` shape D196 now refuses, and history must read back as what it
  says.
- **`migrate_report_hooks`** — MIGRATION(expires=2026-12-31). The stream began 2026-10-19, the
  ledger 2026-07-19: **3,952 rows covering 2,209 report ids had no hook**. Without the backfill a
  decision on the stream would answer from three weeks of a four-month history and be wrong while
  looking right. Merged by timestamp, atomic, idempotent, keyed by (id, event).
- **The decision: the OPEN-THREAD CAP** now counts through `open_threads_now`, which reads the
  stream — the first decision in the engine taken from the hooks. Conservative by design: the
  ledger's answer is kept whenever the two disagree and a `hook_stream_diverged` health event is
  filed, because the ledger is what the operator's backlog counts are built on.

### a conversation can pre-expose a shared-store secret (D216 part a)

Part (a) of the operator's fixed order *(c), (b), (a), (d)*. The two existing clear routes can
only remove a row that already EXISTS, so a conversation's secret-exposure decision was
reachable only by the engine asking at first use — while a routine's settings page has carried
the three-state dial since D103. A conversation is the one run type with a human driving it, and
it could not be told in advance.

`GET/PUT /api/conversations/{slug}/secret-exposure`, a new verb in the SAME grants router (same
mapping, same reader, same no-draft constraint), plus `static/components/secret-exposure.js`,
which now serves BOTH pages — the routine page consumes it instead of its own copy.

**Bounded by its key, not by its caller**: the entity id is built server-side from a validated
store NAME, so `util:signal` or `fs-read:~/x` in the body is a 400 — no spelling reaches another
grant class. Operator-token-only: a run exposing a secret to itself is what this surface must
never permit. `expose: null` deletes the row, because undecided IS the absence of one.

### the console names the private-IP OAuth cause instead of handing the user to Google (R3061)

The operator tried to re-authorize a routine's Google connection and got Google's own error page
instead of a consent screen: *"device_id and device_name are required for private IP"*, naming
our app and `192.168.0.128`. It blocks the flow before any consent screen and affects EVERY
routine's Google connection on this instance.

`authorize-start` now returns the URL plus a **caveat** naming the host, the cause and the
remedy, and the Connections card states it in a confirm dialog before opening the tab. A
WARNING, not a refusal, deliberately: whether a redirect target is acceptable is the provider's
call, DNS here need not match DNS at Google, and the operator may be on a tunnel this box cannot
see — a hard refusal would leave him no way through a flow he sometimes genuinely needs.
Confirm proceeds, cancel opens nothing, a public host gets no caveat and no extra click. New
`src/rsched/oauth/redirecturi.py` covers 10/8, 172.16/12, 192.168/16, 127/8, 169.254/16,
fc00::/7 and ::1, for literals and resolved names; an unresolvable name says nothing.

**The `device_id`/`device_name` parameters were NOT added, on evidence**: Google's own
`oauth2/web-server` (415 KB) and `oauth2/native-app` (160 KB) pages, fetched live, contain ZERO
occurrences of either name, while the same grep finds `redirect_uri` and `loopback` — so the
content was real and the absence is too. An undocumented parameter on every routine's consent
request is not worth guessing at.

`docs/oauth-connections.md` is corrected too: its setup step 3 told the operator a
VPN-resolvable name was enough, which is the advice leading straight into this refusal. The
defect is not CURED — the remedy is a public https `public_url`, which only the operator can set.

## [0.501.0] — 2026-11-05

### An unknown settings pattern is loud, and the vocabulary judge moved into the writer (D229, F610)

The operator ordered D229 as a builder campaign — "validate every proposed settings value against
the live vocabulary at FILING time, and refuse the proposal with the field named". 0.493.0 shipped
the first increment; these are the three parts that were still owed.

- **An unknown `pattern` slug now raises at the ACCEPT** (`workflows/scaffold.py`), naming the
  field and the slugs the live library holds. Before, `store.read(...) or {}` answered None,
  `settings` fell to `{}`, the `pattern:` key was omitted from the new `routine.yaml` and the
  recommender got `""`: the click built a routine following NOTHING and no surface said so.
- **Only then does filing judge `pattern` too.** That order is the doctrine, not a preference —
  refusing at filing what the accept quietly tolerates would make filing stricter than accepting.
- **The judge now sits inside `pending.queue`**, the one writer every filing path must pass, and
  raises `pendingvocab.Refused` before writing. It guarded the two filing observations that
  existed, so a third path added later would have written an unjudged record and handed the
  refusal to the operator's click — the defect F610 is about. `queue` takes the `ServerConfig`
  because the vocabulary is live (the library on disk, the registry); eight writers threaded
  through. **The two existing paths' refusals are byte-identical**, pinned by a test asserting the
  literal refusal head, the verbatim re-file tail and the exact observation key sets.
- **Four other filing paths reviewed, none rebuilt.** `recipeproposal`, `candidate` and
  `goalreached` pass through the writer and carry no judgeable vocabulary — their accept-side 4xx
  are time-of-click run-state facts, which filing must not pre-empt. `autoapply` uses a different
  store: its patch KEYS are already judged at filing, and the residue is field VALUES needing live
  server state, left to the apply where a refusal becomes an ordinary decision card.
- Known and deliberately left: `api_pending._materialize_lane` still lets an accept build a lane
  naming a ghost routine — the one stricter-than-accept exception left, wanting an accept-side
  loudness increment of exactly the shape above.

**Behaviour change worth knowing:** a proposal filed before this release carrying an unknown
pattern slug now fails the accept with 400 where it previously built a pattern-less routine
silently.

### The nightly backup gets a local first tier, and both tiers are judged apart (D235, F830, R3204)

No off-box snapshot completed between 2026-11-01T07:04:34 and this release — `backup_stale` fired
three nights running. The target is sshfs at one round trip per FILE, ~167k files, against the
unit's `TimeoutStartSec=4h` whose last success had a 29-minute margin.

- **`deploy/backup.sh` snapshots to `/mnt/usb500/rsched-backup` FIRST**, then brings the off-box
  share up to date *from that snapshot* as a separate step that can neither fail nor delay the
  local one. A disk in this box does not survive losing the box, so the off-box copy is not
  optional — it is second. The operator's order, R3204.
- **Run directories older than `KEEP_RUNS_DAYS` (7) are dropped from the OFF-BOX tier only** —
  D235's answer, verbatim "Exclude runs/ older than N days from the snapshot (keep state/,
  .memory/, LEDGER, audit/)", with N stated here because his answer left it open. The local tier
  keeps every run directory: that is where the per-file cost does not exist. `state/`, `.memory/`,
  `LEDGER.md`, `audit/`, `questions/`, `artifacts/`, `conversations/` and `background/` are carried
  whole on both tiers at any age. The migration tarball still carries everything.
- **MEASURED, and it undercuts the premise of the cut**: of 170,029 transferred files only 18,600
  live under `routines/*/runs/`, and at N=7 the exclusion drops 12,268 — **7.2% of the set**. That
  alone does not restore the 4h margin; the local tier is what does. Where the bulk actually is is
  reported separately, because cutting it was not what he decided.
- **Both tiers refuse a target that is not on a mounted filesystem of its own**, and a refusal is a
  failed backup. Built stronger than `mountpoint -q`: the guard resolves the mount the target
  actually lives on (`stat -c %m`) and refuses the root filesystem, `$HOME`'s mount or `$HOME`'s
  device — because a root one level below the mountpoint passes `mountpoint` while being just as
  wrong, and because `/dev/shm` roots are the only way this script can be tested offline.
- **`daemon/backup_watch` judges each tier on its own stamps and names the stale one.** It took the
  NEWEST stamp across roots, so tonight's local snapshot would have reported `ok` for an off-box
  copy a week behind — the F602 silence with a fresh number in front of it. `unknown` still claims
  nothing (F480). One root is still one tier, judged exactly as before.
- **A defect introduced and fixed inside this release, now pinned**: `usage()` prints through an
  unquoted heredoc, so backticks in its prose are command-substituted — a help line mentioning
  `rsched postdeploy` made `backup.sh --help` RUN it and append a row to the live postdeploy
  ledger. Two tests scan every `deploy/*.sh` for that shape and check `--help` writes nothing.

Not verified in production: the next night's run is the first real evidence, and `/mnt/usb500`
still needs its permanent `fstab` entry (by UUID, `nofail`, mode 700 — the operator's sudo).

### The browser test container's memory is finally attributed to the stage (D226, goal b2)

The instrument shipped and ran for a week and produced no usable number. `cgroupmem.Watch` sampled
`memory.current` only when `memory.peak` was ABSENT — but the `uitest` container mounts
`/sys/fs/cgroup` read-only, so the reset it needs fails `[Errno 30]` on every job, and the
refused-reset branch reported the CONTAINER's LIFETIME mark with `samples: 0`. Every reading the
gate ever received was that one, which is why `memory-peaks.jsonl` held engine rows only while the
browser container — where the pressure plainly is (F834: four Chromiums under a 4 GiB ceiling
against a suite peaking ~4.8 GB) — stayed unmeasured.

- Now it **samples whenever the reset did not actually happen** and reports `bound: "lower"` with
  the sample count; the kernel's unreset mark travels alongside as `cgroup_lifetime_peak_bytes`,
  an upper bound beside the job's own lower one. The `exact` path is untouched.
- Proven in the real container: a live job touching 300 MB came back `samples 11, peak 420,364,288
  (9.8% of the 4 GiB ceiling)` where the live runner's line for the same job read `samples 0`.
- It still cannot say "no OOM occurred" — only the host's journal can.

### The browser dock hangs up on `#/browser` — and the "one seat" it was framed against does not exist

`docs/designs.md`'s entry, decided 2026-11-05, option (b), with the check it ordered first.

- **The check answers NO.** `x11vnc` runs `-shared` and the relay opens an independent upstream
  socket per viewer with no seat accounting, so `#/browser` was never refused a screen while the
  dock held one. The "fixed regardless" half had nothing to bite on; what the release buys is one
  less duplicate stream. The one-seat belief lived only in prose, now corrected in
  `docs/browser-sessions.md`, `CLAUDE.md` and `components/screen.js`.
- `mountScreenDock` gains `releaseOnPage`: on `#/browser` the dock empties its body (the iframe IS
  the connection) and re-dials on leaving, with a generation counter so an in-flight probe cannot
  paint into the released dock. **Folding still keeps the connection** — he chose that half.
- 4 browser journeys; the desktop dock is untouched.

### A conversation's bound machines are in its prompt — the pin that proves it (D216 part b)

Part (b) of the parity campaign was briefed as a defect and is not one: `capabilities._machine_notes`
keys on `ctx.routine.machines` with no conversation gate, the composer mounts it through
`capabilities_digest`, and `runtime.run_routine` mounts machine shares for every run it boots. What
was missing is the pin — both sides were tested separately and nothing joined them, so a refactor
could have dropped the machine rows from a conversation's prompt silently. Two tests now assert on
the COMPOSED TEXT, driven from the YAML on disk; non-vacuous by mutation. No production change.

## [0.500.0] — 2026-11-05

### Changed — a missed one-shot is skipped, like a missed cron fire (D237)

The operator, 2026-11-05: *"yes, skip missed one-shots too."* Until now a one-shot whose
`fire_at` passed while the daemon was down fired on the first tick however stale, and one due
while scheduling was paused waited out the pause and fired at the lift — the one self-starting
path D237's "a missed fire is skipped, whatever the cause" did not reach. `daemon/schedule_once`
now consumes UNFIRED every request due before the daemon process started (`reason: down`) and
every request that comes due while paused (`reason: paused`), filing `oneshot_skipped` on the
blocked-work surface (18 events now). A request that is merely WAITING — its routine still
running, the daemon draining — is not missed and fires as before. `daemon/pause.py`'s contract,
docs/schedule-once.md and the tests say so.

### Fixed — test sessions no longer start into a host that cannot hold them

Found 2026-11-05: the host (14.7 GB, a 4 GB swap that was full) ran a full browser gate with ~2 GB
available, and the kernel's GLOBAL OOM killed a live routine run (grant-hunter, rc=-9), the gate's
own ssh sessions and an xdist worker. The `uitest` container's cap does not protect the host. Every
session now waits in `pytest_configure` until `MemAvailable` covers its selection
(`tests/memgate.py`: 2 GB fast, 3.5 GB browser), up to `RSCHED_TEST_MEM_WAIT_S` (20 min), then
exits 3 without starting — a refused gate is retried, an OOM is not undone. The fast suite's
`-n auto` is capped at `browsersource.FAST_WORKERS` = 6 (it was 11 on this 12-core box, ~250 MB
each inside the daemon's own container): 110 s instead of 72 s, ~1.3 GB less at peak.

Outside this repo, the same day (deploy/DOCKER.md records both): tagfs.service — the
folder-reorg worker, unbounded, 8.1 GB at peak and the main driver of the global OOMs — now runs
under `MemoryHigh=3G`/`MemoryMax=4G` (operator's choice: throttled above 3 GB, confined above 4),
and the llm-interfaces cliproxy (OOM-killed 29× at 384 MB) and cache-marker (24× at 128 MB)
sidecars were raised to 768 MB and 384 MB, live and in their compose file.

## [0.499.2] — 2026-11-05

### Fixed — the browser screen took no clicks: every press opened Chrome's "Einfügen" prompt (F831)

The operator, 2026-11-05: *"i cannot click anything in the sidecar browser frame anymore. it just
shows an "Einfügen" context menu."* 0.499.0's clipboard bridge read this machine's clipboard on
every press and focus of the screen, and Chrome answers a clipboard read with its own Paste
prompt, which took the click. The host → guest direction is now ON REQUEST: a *paste my clipboard
into the screen* button beside the switch (through `act()`), so a prompt can only follow a press
made for exactly that. Guest → host stays automatic — a clipboard WRITE asks nothing.
`tests/ui/test_browser_clipboard_bridge.py` now asserts that a click on the screen reads nothing.

## [0.499.1] — 2026-11-05

### Fixed — a recorded row with no status no longer demotes a changelog-addressed item to `unknown`

Found deploying 0.499.0, measured on the live index: 177 items read `unknown` where none had. 111
are July rows that never carried a `status` and were invisible before the item records — honestly
`unknown` now, and not counted as active. The other 66 had read `addressed` through their
changelog rows; the record, built ahead of the archive rule, outranked it with an empty status.
`item_rows.recorded_item` now applies the archive rule to a record with no status: `addressed`
when a changelog row names the item. A recorded status still wins.

## [0.499.0] — 2026-11-05

### Fixed — no finding or decision can vanish from the item index any more, and the ones that did are back

The operator, 2026-11-05: *"the number of active items increases instead of decreasing … do some
items fall out of the backlog to remain active forever?! make sure nothing is lost."* Walking all
239 committed versions of self-audit's `audit/report.json` answered both halves. The index treated
whatever the latest WHOLE rewrite held as the whole set, and a row the rewrite left out survived only
if some changelog row named it — so **F216, F217, F322, F355 and F445 left the file while open or
in_progress, named by no changelog row, and disappeared from every surface** (D42 and D58 left live
too and survived only through answer markers); 200 more left closed and kept no record. The recipe's
own sentence — "items that drop out of the report keep their history through the changelog" — was
true only for the items a release happened to name.

`rsched/item_records.py` keeps the newest version of every F/D row the file has ever carried in
`.control/item-records.json`, observed by ONE writer, `daemon/item_record_watch.py` (a stat per tick,
a parse only on change). The index builds a dropped row from its last record
(`readmodels/item_rows.recorded_item`) with its last STATUS and its own prose, plus `left_report`:
a row dropped while live stays live in the index and every count, ended only by a recorded status
or a changelog row newer than its departure. The departure is filed as `item_dropped_live` on the
blocked-work surface. The store was seeded from the 239 versions; a history search recovered 20 of
the 31 ids no committed version held (F36–F49 lived in the scheduler repo's own `audit/report.json`,
deleted in 0.29.0; the rest were renumbered or folded), and the other 11 are numbering gaps that
appear nowhere. The row shapes moved out of `readmodels/items.py` into `readmodels/item_rows.py`.

### Fixed — a row only triage could close was closable by nobody (F840)

An unaddressed report's recipient is triage — self-audit — so `report_threads.disposable` now lets
triage dispose of it. Before, an unaddressed row about self-audit's OWN subject had no disposer at
all: settling was refused as "not routed yet" and routing was refused because "a routine cannot
address a report to itself", so R3173 sat in triage and in the live count with its defect fixed.
D182's specimen is unchanged — a SENDER still cannot retire its own unread report.
`test_every_live_row_shape_has_someone_who_can_dispose_of_it` holds the invariant.

### Fixed — the browser test stage fits its container (D238)

`tests/browsersource.BROWSER_WORKERS` 4 → 2. Four workers peaked at ~4.8 GB in a `uitest` cgroup
capped at 4 GiB on a 14 GB host with ~2 GB free: the kernel OOM-killed chrome-headless there 153
times in the week to 2026-11-05, each kill a test parked on a browser that never answered, and the
stage ran 95–113 minutes or timed out — no release shipped from 11-02. Raising the ceiling was
declined (the host has no memory to give). Measured on this release: 697 browser tests in 19 min.

### Added — designs.md holds orders, never open questions, and an order no row records is bannered

Five "Decide: …" entries sat in docs/designs.md from 2026-10-01 — a file only the builder reads —
so no surface the operator reads ever asked them. All five were answered 2026-11-05; three are built
here: CHANGELOG versions may never repeat or go backwards (`tests/test_policy.py`), the pattern
recommender falls back to `one-job` instead of whichever sorts first (`patterns/recommend.NEUTRAL_PATTERN`),
and new conversations start with the reminder layer at `local` and run history at `last`, like
routines (`conversations.starting_capabilities`, read by the create path and the composer's
preview). The other two are decided entries now. `tests/test_policy.py` refuses a question heading
in the file, and `readmodels/design_orders.py` gives the Messages banner a fourth group — a
designs.md order no finding or decision row quotes ("decided, never recorded"; 32 of 33 today).

### Shipped — the builder's eight stranded branches

D163's cost panel, D216 (c), D230, D231, F816 and D152 step 6 were built 2026-11-02 and never gated
(the gate's browser stage timed out — D238). They ship here as 0.498.0, renumbered (main had spent
0.496.0 and 0.497.0 since), with two test fixes the gate found: D216 (c)'s tests needed a seeded
library, and a settings test matched D230's new panel by accident.

### Removed — every catch-up: a fire missed for any reason is skipped (D237, F825)

The operator's answer, asked what lifting a pause should do: *"we don't want this at all. no
catch-ups. why do we need it for restarts? do we need it at all?"* — and, told what each
mechanism was for, *"Remove all catch-up: a fire missed for any reason (pause, restart, crash)
is skipped."* That reverses D183 (his own 2026-10-15 order) and D156, and the 0.329.0 lane boot
make-up. Three mechanisms are gone, not switched off:

- the boot make-up behind a routine's `schedule.catchup: run_once` (`registry.missed_fire`,
  `Scheduler.boot_catchup`) — a field every live routine already set to `skip`;
- the lane boot make-up (`daemon/lane_catchup.py`) and the watermark it compared against,
  `.control/lane-fires.json` (`rsched/lane_fires.py`), which every `lane_runs.arm` and every
  paused lane skip stamped for no other reader;
- the pause-lift make-ups for lanes (D156) and routines (D183: `daemon/routine_catchup.py`,
  `rsched/routine_fires.py`, `.control/routine-fires.json`). `DELETE /api/settings/pause` now
  answers `{"ok": true, "paused": false}` and arms nothing; the dashboard's resume toast and
  pause banner say so.

The lane's `catchup` field, `SchedulePatch.catchup`, the routine page's "if a run was missed"
select, the lane editor's "if the daemon was down at fire time" select, the settings-digest
suffix and `catchup` in every seed settings pattern go with them; a PATCH naming `catchup` is a
422 on both models. A skipped fire is still RECORDED: `fire_paused` / `lane_fire_paused` keep
the subject's cron and next fire, and their wording now says the fire is not made up. The four
make-up events (`fire_catchup`, `lane_fire_catchup`, `lane_catchup_declined`,
`routine_catchup_declined`) are retired from the vocabulary and the blocked-work fold; rows
already in the stream stay as history that no reader keys on. A one-shot is a request, not a
cron fire, so one whose `fire_at` passed during downtime still fires (docs/schedule-once.md).

A `routine.yaml` still carrying `schedule.catchup` loads as before with a problem line, like
every unknown key, that names the decision instead of "check the spelling". The one-shot
migration (`/tmp/claude-migrate-catchup.py`) strips it from 169 live files — 30 routines, 139
conversations — each committed in its own repo; drops `catchup` from all seven lanes; deletes
the two stores and their locks; and strips `settings.schedule.catchup` from the 14 live library
patterns, without which every routine following a pattern would read its schedule as an
override.

**F825** ends with it: the six deleted lanes' stale fire stamps vanish with `lane-fires.json`,
`lanes.delete` now removes the lane's `lane-runs/<id>.lock`, and the migration sweeps the six
stale locks (grp-80fd7080, grp-8bfd2aa6, grp-a675e218, lane-13fa195f, lane-c8888845,
lane-d7d7aae7), so the lane-runs directory counts the lanes that exist.
`tests/test_no_catchup.py` pins the reversal from outside: a weekly routine and a scheduled lane
whose last due fire nobody ran fire nothing at boot, and a fire skipped during a pause fires
nothing once the pause is lifted through the API.

### Fixed — the kickoff that taught every run one action per turn, a run that died on its model can be re-pointed, and a lane's heartbeat whose member count reconciles (F836, F837, F834, F826)

**Nobody chained, because the engine's own kickoff said not to.** The operator, 2026-11-05: *"i
have never seen a run use the ability to run actions in parallel with the && syntax. why is
that?"* Measured over the 60 most recent fleet transcripts: **1 turn of 8,084 carried more than
one action (0.01%)**, and 16 of 17 routines had never chained once — while `engine/chain.py` has
been on for every routine since 0.446.0 and reads actions from a reply's text AND from
`completion.parsed_rest`. `composer.kickoff_message` said *"Reply with ONE JSON action object"* —
the last instruction a run reads before its first action, contradicting the harness paragraph
that says a reply may carry several. It now names the first turn's shape (a step, not a plan, not
a finish) and the option to chain the first few independent steps in that one reply. Per D236
(*"Free fix, re-measure"*) this is the kickoff ALONE: the kickoff is a one-shot boot message
outside the ratcheted engine contract, so it costs the always-on budget nothing, while the
candidate's +297-character when-to-chain paragraph for the harness was declined —
`test_the_engine_contract_never_grows` keeps its ceiling, `say` stays required on every action,
and the chaining rate is re-measured on this fix alone. Pinned off the message the first
completion is actually SENT (`tests/test_kickoff_chaining.py`); `docs/prompt-anatomy.md`,
README, `docs/getting-started.md`, `docs/curated-rules.md` and CLAUDE.md stopped saying "exactly
one action per turn".

**A run that died BECAUSE of its model could only be restarted on that model.** The operator:
*"you cannot switch the model if the run is paused or aborted. but sometimes i have to"*. The
paused half did not reproduce — `paused` is an active state and the pause gate runs before
`apply_model_switch`. The other half was real at both layers: `require_active` returned 409 for
`finished`/`failed`/`aborted` and `run.js` hid the control on exactly those states, while neither
`resume-run` nor `rewind` takes a model. `deficit-coach` died three times on 2026-11-04 with
`model_chain_exhausted`, every one an Opus 429, and its `control.json` shows a person at that
switch after the run had died. The switch is now accepted on a terminal run and armed for its
resume; the reply carries `armed_for_resume` and `when`, and the toast says "at this run's
resume" instead of promising a turn that will never come. Nothing new honours it —
`switches._fresh` edge-triggers against `control-applied.json` and the resumed leg's first turn
boundary precedes its first completion — and `tests/test_switch_after_death.py` now proves that
end to end (the resumed leg's first completion resolves the new model), beside a real-browser
test of the control.

**A hung browser test no longer costs five attempts.** Two release gates in two days returned
`timeout` (ui stages of 6,779 s and 5,723 s); of 794 executions in one, 39 hit the per-test 300 s
ceiling and cost 201 of 448 worker-minutes, with `test_toc`/`test_token_gate` nodes repeating
three and four times each. A node pytest-timeout KILLED never finished, so `reruns=4` could only
spend twenty minutes per hung node; `rerun_except` now exempts `from pytest-timeout`, as it
already exempted a wedged sidecar. This is the amplifier only — the cause, four Chromiums inside
`uitest`'s 4 GiB against a suite measured at ~4.8 GB, is what D238 halved to two workers.

**A lane's heartbeat counted log rows and called them runs.** `lane_chain_done` printed
`len(rec["log"])` as "<n> member runs", and a switched-off or retired member is a log row with no
run: `Professional · Daily` read "5 member runs" on every fire from 10-25 to 11-01 while two of
its five members were `enabled: false` and three runs existed. The test of exactly that shape
asserted "2 member runs" for ONE run — the suite pinned the defect. The detail
(`daemon/lane_runs.chain_detail`) now reads `3 of 5 members ran, 2 skipped (a, b), 0 not-ok`,
plus `N not reached` for a stopped chain, against the chain's arm-time member list — so members =
ran + skipped + missing + not reached for every row (`tests/test_lane_chain_detail.py`). The
"Morning Brief: 3 member runs" counter-example on today's 2-member lane is the same defect read
against the arm-time snapshot: a chain logs one row per member it was armed with, so the 10-29
chain held three; only two runs exist (`grant-hunter:20261029-080752`,
`predatory-publisher-tarpit:20261029-085751`) and it logged 0 not-ok, so the third was a skip —
the new detail would have read `2 of 3 members ran, 1 skipped (…)`.

### Fixed — `/api/questions` no longer ships the fleet's whole decision history to a badge every 30 s (F810)

MEASURED against this instance's stores on 2026-11-05 (read from `~/routines/*/questions/` and
`~/conversations/*/questions/`, not from the live API): the response carried 392 SETTLED rows —
every routine's and conversation's consumed-answer archive up to 25 per dir — at 1.08 MB, beside
~33 KB of open asks and ~7 KB for the 3 open audit decisions. `static/app.js` refetches it every
30 s in every open tab, for a header badge, a notifier and a run view that count only OPEN rows.
F809's gzip cut the wire bytes; the payload was still built, serialized and parsed whole.

The route now bounds what one read carries (`web/decisions_window.py`): open decisions and answers
still in flight are sent whole, always; the settled archive only as `?settled=<n>` — the newest
n across the whole instance, ordered by when a run took them as INSTANTS (F771's DST trap), and
NONE by default — projected to the fields a receipt card reads (`SETTLED_FIELDS`: options,
defaults, batch ids and request entity lists are an ask's machinery). `?routine=<slug>` narrows
every row to one routine's own. The response is now `{"items": [...], "settled_total": N}`, plus a
`settled_note` naming the parameters whenever rows were left out — a routine reads this API with no
console in front of it (self-audit's orient, config-optimizer), and a cut it is not told about
reads exactly like "nothing was ever answered". Estimated from the same stores: the badge's read
drops from ~1.12 MB to ~40 KB; the Decisions page's first load to ~75 KB.

The Decisions page is the one console reader that asks for history (`questions-store.js`
`wantSettled`): the newest 25, a footer "settled: showing the newest k of N" with a **show more**
button (`components/settled-more.js`, through `act()`), and the window released when the page
unmounts so the 30 s poll goes back to carrying none. The store now lets two reads overlap only
when the newer one asks for a wider window, and only the read begun LAST may publish. The
Decisions page, `api_questions.py` and the size ratchet each SHRANK in the process.

### Fixed — a conversation's scripts rail was empty: conversations hold scripts, and now show them (F833)

The operator, 2026-11-02: *"why do conversations not show the routine's scripts in the right side
bar? routine runs seem to do it"*; decided 2026-11-05: conversations hold scripts. The engine
already let them — a conversation run's `script` resolves `<conversation>/scripts/<name>.py`
(`executor._script_homes` starts at the run's own dir), `scripts/` is neither sealed nor recipe,
and on this instance 33 of 139 conversations hold 144 scripts between them (the finding's "no
conversation has a scripts dir" was not so). What was missing was the READ: the only scripts
route resolved slugs through the routines home, so every conversation's list 404'd and
`components/runscripts.js` swallowed the 404 by design.

`GET /api/conversations/{slug}/scripts` is the routine route's twin on the same reader
(`scripts.list_scripts`, the catalog the run's own capabilities digest names) — its own route,
like `artifacts`, because a slug is unique only within a home. The section now mounts in the
conversation view's rail (re-read when a reply ends, since a reply is what writes scripts) and in
the run page's rail for either home, once the run detail names it (a detached task's scripts are
its owner's, so it gets none). The deliberate 404 swallow is gone: every home that holds scripts
has the route now, so a failed read says so in the section instead of leaving it silently empty.

### Fixed — the browser screen's clipboard switch now syncs the clipboard, both ways (F831)

The operator, 2026-11-02: *"the host to client clipboard sync for the sidecar browser does not
work"*. D221's switch set the iframe's `allow="clipboard-read; clipboard-write"`, on the reading
that the embedded noVNC page would reach the host clipboard once permitted. It never does: read at
source, neither noVNC the sidecars serve (Debian bookworm's 1.3.0 for the browser, 1.6.0 for the
desktops) calls `navigator.clipboard` anywhere. Their clipboard is a manual side panel — a
textarea whose `change` event sends its text to the guest (`UI.clipboardSend` →
`rfb.clipboardPasteFrom`) and whose value noVNC overwrites with a plain assignment when the guest
copies. A permission grant only removes a block; nothing made the call, and the four toggle tests
asserted the control against a dead screen, so the suite could not see it.

`static/components/clipboard-bridge.js` makes the calls from the embedder, through that panel —
the one contract both versions share, reachable because the screen is relayed same-origin. When
the screen is clicked or focused it reads this machine's clipboard and, if new, puts it in the
panel and fires `change`, so noVNC's own send path hands it to x11vnc; the panel's `value`
setter is wrapped on that element, so what the guest copies is written to this machine's
clipboard the moment noVNC receives it. Both go through the FRAME's own `navigator.clipboard`
under the explicit grant; the switch is now the bridge plus the grant, and OFF is neither — and
it is the bridge that stops the sync, since a same-origin frame may hold the clipboard by default.
The agent desktops keep the permission without a bridge — automatically reading the operator's
clipboard into a routine's VM is a choice he has not made — and docs/browser-sessions.md says
so. `tests/ui/test_browser_clipboard_bridge.py` proves both directions and the off state against a
page carrying noVNC's exact clipboard contract and the browser's real clipboard; it runs no RFB
stream, since the uitest container has no VNC server.

### Fixed — a reminder link lands on the reminder, not on the top of the page (F832)

His words: *"clicking the reminder link to the routine's health page should take us straight to
the reminder's anchor instead of the top of the healtzh page"*. `reminderLink` built
`#/changes/<slug>` and the caution rows carried no address, so with tens of reminders (169 across
the instance, 164 of them local) the reader scrolled past five sections and found the row by
eye. The link now carries `?rem=<id>`, each caution row carries `data-rem`, and Recipe health's
first paint lands on that row through the house's own landing (`landOn`: folds opened, centred,
flashed, held while the sections above fill in). It lands once — a live re-read never scrolls the
reader again — and a reminder the routine no longer holds says so in a toast.
`tests/ui/test_reminder_landing.py`.

### Fixed — a mid-run filesystem grant's note no longer says it waits for the next run (F829)

R3164 (`llmsectest-weekday`): the operator granted an `fs-write` root permanently while the run
was live, the engine's note said it *"takes effect at your NEXT RUN … widening it mid-run would not
reach the jails already created"*, and the very next identical util call succeeded with the new
root. The behaviour was right — `sandbox.policy_for_ctx` builds every jail per call from
`ctx.write_roots()`, which folds in the run's grant overlay — and the words were wrong:
`_tell_live_run` hands every changed config key to the change note, and
`configflow.CLASSIFICATION['fs_write_roots']` was the one fs-root entry never corrected (its
sibling `fs_read_roots` always drew the request/edit line). It now carries the same sentence — an
access request decided mid-run reaches the run, a config edit does not — and
`tests/test_configflow_fsroots.py` pins the behaviour against the prose and both fields to one
wording, the same shape F802 gave `machines`.

## [0.498.0] — 2026-11-05

### Added — an instance default for the `tool_call` role, so `role_swap: cheap` can save something (D230)

The operator's answer: *"Set the same tool_call binding fleet-wide as an instance default, so
every routine's cheap turns actually go to a cheaper model unless it overrides."* Of 32 routines
only one had a distinct main/tool_call pair, and only because he had applied a `config_patch` for
it by hand — so for every other routine a run swapping down to save tokens saved exactly nothing
while believing it did. There was no instance default for the role at all.

`ServerConfig` gains `system_tool_call_model` + `system_tool_call_effort`, the established shape
of its four existing per-purpose pairs, and ONE resolver — `config.base.role_default_model` —
threaded through every reader (`endpoints.for_model_chain`, `readmodels/stats.py`,
`verifier_model.py`, `conformance.py`, `cli_validate.py`), the patch surface, a new
`PUT /api/settings/tool-call-model`, and four console surfaces.

Two things building it corrected in the measured seam, both load-bearing. **Neither swap layer
resolved through the registry**: `engine/roleswap.py` and `engine/autoswap.py` read
`routine.models['tool_call']` directly and stood down when it was empty, so binding the role in
the model chain alone would have changed nothing for `role_swap: cheap` — the one thing this
decision exists to fix; both now share `roleswap.cheap_model`. And **`readmodels/stats.py`
resolved `main` alone**, which is exactly why his proof predicate (*"a distinct main/tool_call
pair for every routine"*) could not have been met by the chain binding: the readout would still
have printed the pair as identical.

Precedence is `models.tool_call` → `system_tool_call_model` → `system_model`, deliberately NOT
D164's fallback-to-`main`: that reasoning is about an overseer JUDGING a run, while `tool_call` is
the cheap mechanical half whose whole purpose is to be cheaper than main, so resolving it through
main would make the swap a no-op by construction.

**It ships EMPTY.** Unset, nothing changes for any routine, and both halves are pinned by test.
Which catalog model the default should name is not in his answer and is not a run's to guess — a
wrong default is a fleet-wide cost change — so the second half of his proof (*"a cheap turn shows
a lower per-turn cost"*) waits on him setting a value. Also named rather than silently altered:
the default does not reach a conversation whose main model is set, because the conversation header
seeds `tool_call = main` as an explicit override, and changing that is a conversation design
question outside the answer.

### Added — a curator may retire a reminder row in another routine's store (D231)

The operator's answer: *"Give rules-review a way to delete/annotate a reminder row in a DISABLED
routine's store (an engine capability or a census verb), since no report can reach a routine that
never runs."* Four consecutive `rules-review` passes had recorded the same four undeletable rows:
a reminder store belongs to its routine, so the only channel was an addressed report — read on the
owner's next scheduled run, which for a disabled routine never comes.

`remind {op: dead, target: <slug>, id, description}` retires a row in another routine's store. The
row **keeps its whole tally**, stops firing, and carries who retired it, when and why — his stated
preference, because the tally is the evidence that a pattern was too broad and a deletion destroys
exactly that (one acceptance row has 22 holds of which 8 are `could_not`). `op=delete` with a
target remains the fallback for a row with no evidence worth keeping.

The marker is a SIDECAR key in the holder's own store file, so the row itself is never rewritten
and no path through this change can lose a tally even in principle. Authorization is the existing
`capabilities.reminders: global` dial asked once through `GrantPolicy.reminder_denial` — no second
gate over a grant the operator issued — and whether the target routine is `enabled` is read
nowhere, which is the whole point. `reminders.py` (574/574), `engine/remind.py` (605/605) and
`actionschema.py` (629) are all at zero ratchet slack, so every edit to them is line-neutral.

`tests/prompt_budget.json` rises 64,026 → 64,454 (+428, all in `schema`), measured against the
tree, for the three things a run cannot use the op safely without: that `op=dead` exists and is
preferred over `delete`, that `target` now means the disposal ops too, and that `description`
carries the REASON on a disposal. The first draft cost +869.

### Added — the finish gate's ladder goes through the hook-point registry, which gives it the ENGINE's own face (D152 step 6)

The harness-squared half of the operator's 2026-10-26 brief: *"The hook system also should allow
to extend the implementation of parts of the harness^2 features we already started."*
`hooks/points.py`'s own docstring had named what stood in the way — *"a new point is an edit in
five files, so the cheap thing to do is hard-code a sixth call site, which is what every harness²
feature did"*. Step 4 moved the reminder layer onto the table; this moves the finish gate, the
other mechanism firing at `pre-finalize`, and `points.MECHANISMS` gains **`guard`** — the first
mechanism in the registry that is not an authored hook. That distinction is the point: "extend
the features we already started" means a FEATURE registering at a point, not only a routine's
reminder or a library rule's assist.

`engine/finish_rungs.py` declares the ladder's nine rungs, each keyed by the name it already
records on its deferral, with the access policy it carries — top-level only, skipped on the
reserved finish turn, once per run — and `finishgate.check_finish` asks
`eligible(loop, ctx, key)` at all nine sites instead of writing that policy out again.

What it removed, measured by its own red-first run: **eight** hand-written reads of
`_finish_reserved` in the gate, down to one (the unbacked-claim rung's DISPOSAL, which is asked on
the reserved turn and records an `error` event rather than deferring, because a rejection there
would lose the authored summary the reserve exists for). And a second vocabulary:
`engine/frictions.RUNGS` was a hand-copied EIGHT-key list beside a NINE-rung ladder, so a rung it
had never heard of fell into the `no_action` bucket — it reads the declaration now, and a tenth
rung is counted the day it is declared.

Two asymmetries become readable rather than inferable from which `if` a check sits outside of:
`jobs_owed` is asked at any depth and on the reserved turn (a child can hold jobs, and one action
settles it), and `unbacked_claims` is asked there with a different disposal. The gate also
inherits `dispatch.at`'s two invariants identically with the rule and reminder layers — a payload
the point cannot carry never fires, and a predicate that raises means "did not fire" rather than a
failed turn, which matters most on the finish path, where an exception costs a run its summary.

### Added — the per-run cost profile reaches a surface (D163)

The operator answered D163 with *"extend what exists … into a real cost profile per routine"*.
0.495.0 built the reader; it was then measured to be imported by nothing but its own test, and a
profile he cannot see does not satisfy that answer.

`GET /api/changes/{slug}/cost` serves `readmodels.cost_profile` unchanged behind the same
`_routine_home` guard its siblings use — added to `api_changes.py` rather than a new module
because that module is already registered, so `web/appwiring.py` (18 lines of slack) needed no
edit at all. `static/components/cost-panel.js` renders it as a new **Per-run cost** section
directly beneath Outcome on the routine's DEVELOPMENT page, so the page reads "what it produces,
then what a run of it costs"; the production page is untouched.

The integrity verdict is the substance, not the decoration. A window the read model itself calls
understated (the R3112 accounting defect closed in 0.494.0, detected by arithmetic rather than by
a release date) leads with a warn panel printing `integrity.detail` VERBATIM, names which side is
affected, marks THAT window's median "· mixed" where it is read, and appends to a flagged trend
that the comparison straddles the fix. The sound side is deliberately unmarked — the mark names a
side rather than disclaiming the panel. No verdict is re-derived in the browser.

### Fixed — the websocket teardown race, fixed at its cause, with a bounded net under the class (F816)

A test had flaked two of five release gates, and the operator asked for the one thing a gate log
cannot answer: *"figure out if it is a problem with the test or if we need to assign more memory
persistently"*. MEASURED rather than read: those four files, run together under the gate's own
shape (`-n auto --dist worksteal`), failed **8 of 12** invocations while the engine's memory
cgroup sat at **37.2 % of its 5 GiB ceiling**. So it is the test; more memory would not have
prevented one of the eight.

The cause is in `starlette.testclient`: `WebSocketTestSession.__enter__` builds an exit stack
registering `fut.result`, then the portal's `cs.cancel`, then `close(1000)`. Unwinding LIFO, the
close is sent and the task scope cancelled immediately after, so a handler not yet back from its
own close leaves `fut.result()` re-raising the cancellation into the test. The tell: of 16
`websocket_connect` sites in the fast suite only three flake, and they are exactly the ones that
open an IDLE socket — every site that does not flake receives frames or waits on a server-side
event, and `test_ioload._close_and_wait`'s own docstring already named this race.

One shared `tests/helpers.idle_socket` is used at both flaking sites. It suppresses the TEARDOWN
artefact and only that: the cancellation is raised after the assertions these tests make have
succeeded, and a socket the server REFUSES still raises `WebSocketDisconnect` with its close code,
which every refusal test asserts on and which is not suppressed. (Draining to the disconnect does
NOT work and the helper records why: `/api/events` is a live bus with nothing queued, so a
post-close `receive()` blocks — twelve iterations went from ~7 s to minutes and still failed.)

His second instruction in the same answer — *"also extend the rerun shield to the fast suite as a
belt-and-braces net"* — is `reruns = 2` / `reruns_delay = 1` in `pyproject.toml`: the whole suite,
bounded at two because the fast tests are in-process, so a failure surviving three attempts is a
failure. The browser directory is unaffected, because `pytest-rerunfailures` takes a `flaky`
marker as priority over the global ini setting and that directory declares its own four with an
exemption pattern — a CLI `--reruns` would have won over markers and stripped it. Every rerun
stays printed by the existing `-rR`, so the net is evidence in the log rather than a hiding place.
With the fix and the net, 10 of 10 parallel invocations green and **zero reruns consumed** — the
fix alone closed it.

## [0.497.0] — 2026-11-03

### Fixed — an archival candidate is judged against the window the run MEASURED, and a no-op rule revision no longer commits to every holder

**The operator's background archive died on a 400 the fit test should have prevented.** His
message, 2026-11-02: *"— background archive did not land — (history archival degraded: otari:
HTTP 400: prompt is too long: 1028908 tokens > 1000000 maximum)"*.

`estimate_input_tokens` is 3.5 UTF-8 bytes per token — a packing guess, not a tokenizer — so a
run whose content packs denser reads LIGHT. `window.note_prompt_size` measures exactly that gap
on every completion and keeps it as `loop._token_ratio`, and `compact_if_needed` restates the
foreground model's window in those measured units (`_calibrated_window`) before any gate sees
it. But `_pick_archival_model` built its own candidates — the dedicated compaction model and
the tool-call model — FRESH from the registry, which serves the CATALOG figure, and those are
the PREFERRED candidates (machine work belongs on the cheaper tier). So `archival_fits`
(prompt + schema + output reserve ≤ 0.7 × window) was evaluated against a window the run had
already measured as smaller, and a run reading ≥1.43× light posted an archival over the
provider's hard limit. One generator expression in the candidate loop now calibrates every
candidate, not just the one passed in.

Why that 400 is terminal where a foreground turn's is not: `overflow._recover_oversize_prompt`
shrinks and retries the TURN, and the archival runs in a thread with no such net
(`engine/archival.py` treats any failure as a designed degrade). So the middle's navigable
history is simply lost and the run is told the archive degraded — which is the message he saw.
`tests/test_archival_window_calibration.py` asserts both directions through
`compact_if_needed`: unmeasured, the same middle and models still archive; measured 1.6× light,
every candidate is declined and the deterministic digest stands.

Context for how often this bites: the health stream carries 32 `prompt_oversize_shrunk` events,
7 of them on 2026-11-02 in one conversation (`c-20261014-134208`, real prompts 1,090,248 →
1,332,781 tokens against a 1,000,000 window). Those are the FOREGROUND rescues working. The
archival had no equivalent, and 7 `archival_degraded` rows in 19 days carried no oversize case
before this one — it is a new mode, not a known one.

**A rule revision that changes nothing no longer commits** (R3148, `rules-review`).
`write_rule` committed on every call, even when `replacement` was byte-identical to `anchor`, so
a no-op revision landed as a real commit on every holder of that rule — 100+ routines and
conversations for the big ones — and showed in the library's history as a change that changed
nothing. `handle_write_rule` now refuses a revision whose resolved text equals the rule already
in the library, before the lint, the approval ask and the commit. `rules-review` had carried a
`^write_rule` reminder for this hazard since 2026-10-26 and retired it on 2026-11-02 with
`didnt` (9) past `would_have` (3): the predicate — *is the replacement identical to the anchor?*
— is invisible to any regex, so the caution had to fire on every `write_rule` whether the
hazard was present or not. A refusal at the seam costs nobody a turn.

## [0.496.0] — 2026-11-02

### Fixed — a boot migration can no longer kill the daemon's boot, and a malformed usage row can no longer raise out of a read model (R3132, R3138)

Two defects raised by `scheduler-builder` from its own worktrees, both latent in a green suite,
both the same shape: an unvalidated read of something the rest of the system treats as optional.

**A boot migration reading an extra home as an attribute kills the WHOLE boot** (R3132).
`migrate_enabled.run_migration` read `server.conversations_home` and `server.background_home` as
hard attributes, and it runs FOURTH in `cli_daemon.cmd_daemon` — so on a server object without
them (what `load_server_config` may return, and what the fresh-container boot test hands it) the
daemon died at `cli_daemon.py:40` with an `AttributeError` raised inside a migration that had
nothing to migrate, before the library bootstrap, every seed sync, every later migration, the web
app and the scheduler. Both extra homes are now read with `getattr`, the shape
`migrate_total_turns` learned from gate 1792680349875750345 and `migrate_dead_reserved_utils`
already carries. The same sweep found a SECOND live instance on the boot path —
`migrate_usage_volume._run_dir` — fixed identically.

It stayed invisible because `tests/test_bootstrap.py`'s fresh-container boot patched
`migrate_enabled.run_migration` out, so the only test driving the real `cmd_daemon` never
executed a migration against its own stand-in. That is now closed as a class rather than as an
instance: `test_the_boot_survives_every_migration_on_a_minimal_server` drives the real boot with
NOTHING patched out of the migration sequence, so the next migration to read a home hard fails
its own test instead of a container's first start.

**A malformed number in the durable usage stream raised out of `run_health`** (R3138).
`regression_flag.profile` and `_by_version` both did `float(rec["volume"])` behind an
`is not None` guard, so a row whose `volume` was written as a dict or a stray string raised
`TypeError` out of three production surfaces: the routine page's development line
(`recipe_regression` → `api_changes.routine_summary`), `GET /routines/{slug}/health`, and run-end
telemetry (`engine/runtime._log_cost_trend` → `recent_trend`). `fold_legs` sanitizes only runs
with SEVERAL legs — "a run with one leg keeps its raw values" — so a single-leg row arrived
exactly as written. Both sites now take the value through `usage_stream.reading`, the stream's one
definition of a number beside `_number`: an unusable value is **no reading**, skipped rather than
coerced, and never a 0 — a 0 is itself a reading and one of them drags a median far enough to
fire a regression flag on its own. The two `turns` coercions raised on the same shapes and keep
their established absent-is-0 contract without raising. The fix sits in the shared read model
because four readers fold this stream; `cost_profile._sanitized` was a local shield for one.

9 tests (`tests/test_run_health_malformed.py` new, `test_migrate_enabled.py`,
`test_bootstrap.py`). Red-first by reverting all four source files with the tests kept: 2 failed
and 1 error, the error being the real `cmd_daemon` dying at `migrate_enabled.py:45` — R3132's
mechanism reproduced end to end.

## [0.495.0] — 2026-11-01

### Added — a per-RUN cost profile per routine, and the integrity of the figures it reports (D163)

Three cost readings already existed and none of them answered *"what does a run of this cost"*:
monthly spend rolls a daily routine's thirty runs into one calendar cell, the Stats aggregate
slices the fleet, and the trend check reduces two five-run windows to a boolean flag with its
medians buried in a reasons string. So the routine page could say a routine had ballooned without
ever saying what it costs per run — which is the figure a budget is set in. The operator's answer
was to *"extend what exists … into a real cost profile per routine"*, and this is a READER ONLY:
it introduces no cost quantity of its own, takes `volume` and `notional_cost` as the usage log
already defines them, the runs as the usage stream already folds them, the incarnation filter from
the existing reader, and the trend verdict from the existing check — called over the same window
pair and reported verbatim, pinned by a test that its median agrees with the flag's, so this
module and the routine page can never disagree about whether a routine is flagged.

It also reports **accounting integrity**, which is what makes a per-run figure honest. The two
usage losses fixed in 0.494.0 understated any run that spawned children, and the stream on disk
still carries those older rows — so a five-run window straddling the fix compares two different
quantities and would read as a cost improvement nobody made. The detection is arithmetic rather
than a hard-coded release date: a top-level run reporting less volume than its own child rows sum
to is understated whatever wrote it. The profile counts those runs, marks each window
trustworthy or not, and names which side is contaminated, so no consumer can render a mixture as
one quantity.

Deliberately no UI and no new health event in this increment: a correct reader with no view is
worth more than a view over a quantity nobody checked. The next increments are the smallest
consumer that renders the mixed-accounting flag, then folding integrity into the existing
cost-trend row — a vocabulary change that needs every registration surface at once.

## [0.494.0] — 2026-11-01

### Fixed — a child run's spend survives its parent's resume, and an orphan's is recovered (R3112)

`token-lab` measured that `status.json`'s `usage.out` understates a run that spawned children —
one by 40 %, 1,099,621 reported against a 1,542,978 tree — and read the cause as a fold that only
happens at parent reap, so a child nobody waited for would be attributed nowhere. That reading was
the plausible one and it was wrong: a child is folded into the parent's live counter the moment it
is COLLECTED, and the run's close-out collects every uncollected child before the finish event is
written. Within one leg the accounting was already right. Re-measuring found two narrower causes,
and this fixes both.

**A resume dropped every child an earlier leg had collected.** `history.prior_usage` rebuilds the
cumulative base from the transcript and summed only assistant actions, llm/decide observations and
compactions — never `subrun_end`. Measured: a run whose two children exited carrying 420,211
output tokens between them, both before that leg's finish, then resumed and reported 343,918 for
the whole tree. That is the 40 %, and it was not a missing fold but a missing REPLAY of one.

**An orphaned child's spend was written as `{}`.** A child that does not survive an interruption
is never collected, and the resumed leg's marker recorded an empty usage dict while the real
figure sat in the child's own transcript — 193,930 output tokens for one such orphan.
`subsalvage.spend_reached` now recovers it from there, summed by the same definition the parent
uses so a recovered total cannot mean something different from a live one, and the same marker
carries the child's real turn count instead of 0. Both stay "what the disk records, never an
estimate": an empty result still means nothing was recorded, a pruned child directory is not an
error, and an unparseable line is skipped. No second usage field was added, and nothing changed
about when a live child is folded.

### Added — a conversation's HELD grants reach a surface, through the writer its deny half uses (D216 rank 1)

The operator ordered conversation-settings parity as one campaign — *"All six, as one parity
campaign"* — with the grants surface first. F783 had already shipped the DENY half (a conversation
can show and clear its deny tombstones); what was still on no surface was the ALLOW half: a
`grants:` TRUE row, which is what a Decisions-page *allow forever* writes for the one class with
no native switch, and which the engine honours for a conversation exactly as for a routine. So the
operator could see what a conversation had been DENIED and not what it had been GRANTED, and the
deny clear deliberately refuses an allow row.

The same router now serves and clears both, and — this being a parity campaign rather than a
second copy — BOTH clears go through ONE writer, with the existing deny clear rewired through it
and each route sign-locked out of the other's rows, so an allow clear can never reach a
tombstone. The card is one renderer with two mounts rather than two components, and it reuses
existing classes, so no stylesheet changed. Ranks 2-6 are untouched; the next rank needs
re-measuring first, because two modules in the tree now label themselves "rank 1" with different
meanings.

## [0.493.0] — 2026-11-01

### Fixed — a queued proposal's field values are judged at FILING, not when you click accept (D229, ordering F610)

A scheduled run cannot write config, so `create_routine` and `manage_lane` QUEUE a proposal and
the operator materializes it with a click. Its field values were judged only at that click — so an
invalid one surfaced to HIM, at the worst possible moment, instead of to the run that filed it and
was the only party able to correct it. F610 recorded three different fields failing exactly that
way in one day, and sat 29 days; the operator ordered it as a campaign: *"validate every proposed
settings value against the live vocabulary at FILING time, and refuse the proposal with the field
named."*

`rsched/pendingvocab.py` is the judge, and both filing paths now reach the queue only through it,
so the refusal arrives in the observation the filing run reads — naming the field and the
correction it can act on that same turn, the shape `engine/config_bridge.py` already uses for a
`config_patch`'s keys. Every check reads the LIVE source rather than a second list that could
drift: the slug's shape, `workflow` against the library's own listing (which catches `generate`, a
value that passed the existing check and raised on the click), the lane's `verb`, `target` and
`name`, `on_failure` against the lane vocabulary, `cron` through the lane module's own validator,
and `members` against the live registry — pinned by a test that the member check answers to the
registry and not to a fixture. The accept path is byte-for-byte unchanged, so nothing already
queued becomes unacceptable. `engine/create_routine.py` was at its size ceiling with no slack,
which is what the extraction paid for: 432 → 410, its ratchet ceiling re-tightened with the
measurement.

Two things this increment deliberately does NOT do, because the campaign's next steps are the
honest place for them: the settings-`pattern` slug stays unjudged, since an unknown pattern does
not raise at the accept — it silently omits the key — and refusing it at filing would make the
filing stricter than the accept, which this repo's own doctrine forbids (an accept-side loudness
increment comes first); and the judge sits in the two filing observations rather than inside the
queue writer, so a third filing path added later would bypass it. Four other filing paths
(`autoapply`, `recipeproposal`, `candidate`, `goalreached`) have not been reviewed for this defect
class.

## [0.492.0] — 2026-11-01

### Added — a run's CLOSING asks ride its finish banner, answerable there (D212 (b), hooks step 5)

Increment (a) (0.465.0) made every decision record carry its CLASS — `blocking` or `closing`,
from the operator's answer *"Both axes: subject decides the default, the run may override it when
the work is genuinely blocked"* — and nothing read it: a grep for `"class"` over `src/` and
`static/` found only the writers. This is its first reader, and it follows the axis rather than
describing it. A `closing` ask is one the run finished its work WITHOUT, so its reader is the
person reading what the run DID, with the answer one click away — not a card on a page he visits
separately, where it competes for attention with the decisions that ARE holding work up.

`engine/closingasks.py` selects a run's own open closing asks and the finish event carries them as
`closing_asks`; `static/components/closing-asks.js` renders them on the finish banner with their
options as buttons, posting through the ONE answer route every other surface uses. Three bounds,
each from something already true of the store: this run's own qids only (`q-<run-ts>-<turn>`, the
prefix the answer taker already attributes by), nothing whose answer is already waiting in the
inbox, and a record written before increment (a) — which has no `class` key — reading as
`closing`. Selection only: nothing is filed, answered, re-asked or expired here, and a store that
cannot be read yields nothing, because a surface that only adds something to read must never fail
a run. Increment (c), the Decisions page keeping only the blocking class, is not in this release:
it belongs with the lifecycle rebuild that is rewriting that page anyway.

### Added — the engine serves a REPEAT POLL on the cheap model, unasked (F785, D217's engine half)

D185 gave a run `role_swap` and nothing in the fleet used it, so the turns F785 measured kept
paying main-model prices to read a one-line answer. `engine/autoswap.py` is the engine's own half
of that slot, hung off `sidefields.land` — the seam the turn loop already calls, so `engine/loop.py`
gained no line. A turn qualifies only when the previous action was poll-class and the new one
repeats that class, served from the SECOND consecutive repeat so the models never change under a
single re-check, restored on the first action of a different class. Three stand-downs: a routine
with no distinct `tool_call` gets nothing and is told nothing; the stuck-escape turn — where the
loop sheds the provider schema and the next reply must re-plan without a grammar — stays on the
main model; and an action carrying `role_swap` latches the engine off for the rest of the run,
because a run steering its own model outranks the engine guessing for it. The operator's console
switch is never clobbered: this layer only ever undoes a swap it made itself.

**Its reach is measured, and it is ~zero today.** Over 401 retained transcripts / 32,255 landed
turns the shipped rule would have served ONE turn cheap (0.003 %), not the 2,651 (11.5 %) the
finding projects. The gap is structural: a run ends at five byte-identical actions, so a long
ADJACENT poll streak cannot exist — a real poll loop is `poll → read → poll`, whose repeats are
not adjacent — and the 11.5 % is a count by KIND (44.63 % of all turns are poll-class by kind).
Worse, for a poll whose action never varies (`subruns`, an identical gate `status` call) the class
depth equals the byte-identical streak, so the serve threshold always coincides with the
stuck-escape guard: that band is empty, and it is pinned as a test with its reasoning. Nothing
fires at all until a distinct `tool_call` model is bound fleet-wide, which is config and not code.
Put to the operator with the three widenings and their measured reach, his answer was **"leave it
as shipped: adjacency only — the mechanism exists and waits for D230"**, so this is the settled
rule and not a first cut.

### Changed — the `gu` dispatcher is tracked repo code, with a selftest and a migration (D225)

`gu` is the dispatcher every `util` action ultimately invokes and the reader of a util's `net:` /
`secrets:` headers — the declarations that decide which network access and which credentials fold
into its sandbox. It lived as an untracked executable in the library root, reachable by no routine
and gated by nothing. The operator's answer: *"`gu` becomes a tracked file in the scheduler repo,
seeded and synced into the library like util-seed/ content, so it acquires a test suite and a
release gate… Not a one-line move — it needs a seed path, a selftest and a migration for the live
copy."*

It is now `util-seed/gu`, byte-identical to the live copy (sha256 verified at both ends), where
the library's own `utils/` already comes from. `tests/test_dispatcher_seed.py` (17 tests) pins
that it parses and answers `--help` as an executable, that it is the rich dispatcher the embedded
stub is not, and — from the outside, through `gu lint` against fixtures — the sandbox decision the
operator named: an undeclared credential read is refused BY NAME, a declared one passes in all
three spellings, and an undeclared or invalid `net:` is refused naming "denies ALL TCP".
`migrate_dispatcher_seed.py` carries it to a live instance, because `ensure_library` installs a
minimal stub and never overwrites an existing dispatcher: it replaces the live copy ONLY while
that copy is the stub byte for byte, keeps the stub beside it as `gu.stub.bak`, and leaves
anything else untouched and named in its record — a dispatcher is an executable the operator may
have edited, and a migration cannot tell an edited stub from an edited anything-else. `gu`'s own
AST/lint defect (a dynamically-built argv is invisible to its call graph) is unchanged and is now
a separate increment against a gated file.

## [0.491.0] — 2026-11-01

### Added — `create_routine revises:`, a recipe revision for ANOTHER routine, gated by the TARGET's dial (D198)

A routine holding `write_recipe` can propose a revision of a *different* routine's recipe:
`create_routine` with `revises: true`, its `prompt` carrying the change one line per change as
prose — never a diff, because only a run that can read what a recipe is FOR can turn prose into
an edit (`engine/recipeproposal.py`'s reason, D200). The approval dial belongs to the routine
whose instructions would change, never to the proposer: `revise_confirm` (`rsched/revisedial.py`,
`always | invited`) on its *Permissions & capabilities* panel. `always` — every routine's default
— files ONE `revise-routine` decision card; `invited` arms it at once. Either way the WRITE is
made by a run of the routine that owns the recipe, under the one-leg unlock `engine/revise.py`
already implements, handed the lines as its instruction through an invitation spool
(`rsched/reciperevise.py`) consumed at that run's INIT — so no run is ever rewritten mid-flight,
and the run may refuse a line that is wrong about what its own runs do. `routine.yaml` stays the
operator's at every level.

**This is not a security fix, and says so.** Building it established that the cross-routine
recipe write is already possible and ungated: `write_recipe` together with a write root over
`~/routines` rewrites any `main.md` with `edit_file`, because the recipe seal is scoped to the
run's own dirs (`engine/fileops._write_gate` → `engine/reach.routine_dirs`). Asked to rule on
that, the operator answered: *"leave it: a routine granted write_recipe AND ~/routines is trusted by
construction, and the grant pair is the gate."* So the open route stays open by his decision, and
this path's value is the RECORD and the target routine's visibility — who proposed what, what
authorized it, and a run of the owning routine that can say no.

`engine/actionschema.py` was at its size ceiling, so the seven `create_routine`-only schema
fields moved to `engine/schema_routine.py` first (661 → 629); `web/api_recipe_revision.py` carries
the card's approval beside D200's, since `web/api_pending.py` has no room. Console: a `recipe
approval` dial row and its settings-digest line, with a browser test proven red by stripping the
row.

### Changed — the reminder layer's trigger kinds go through the hook-point registry (D152 step 4)

`hooks/points.py` has said since 0.458.0 that the `reminder` mechanism fires at eight points, and
`points.TRIGGER_POINTS` mapped every one of `reminders.KINDS` to its point. Nothing read either:
`engine/remind.py` matched directly at four sites, so the registry described the layer without
governing it. `engine/remind_points.matching_at` now resolves the kind to its point and asks
`hooks/dispatch.at`, so the reminder layer inherits what the rule layer has had since 0.458.0: a
candidate whose payload the point cannot carry never fires, and **a pattern that raises while
being applied is one reminder that did not fire rather than a broken turn** — new to this layer,
and the reason the step matters: a reminder regex is user-authored and was applied inside the turn
loop with nothing between a pathological match and the run.

Routing it exposed two defects in the table nobody had been consulting, each of which would have
silently dropped the only mechanism firing at its point: `run-start` declared an `engine_note`
carrier with payloads `("note","action")`, while a `turn:first` reminder is delivered on the first
turn's OBSERVATION TAIL — so `points.allows` would have refused every one of them; and `prose`
mapped to `turn-boundary` though the kind is evaluated and delivered in the same pass as `result`.
Both corrected to what the engine does, both pinned by regression tests, and `turn-boundary` no
longer claims a mechanism it does not reach.

### Fixed — six routines no longer open every run with two FAIL rows about utils that do not exist (D228)

The `fau-mail`/`gmail` → `mail` consolidation reached every caller's code and nothing reached the
stores that hold util names as STRINGS: six installed routines still list `fau-mail:send` and
`gmail:send` in `capabilities.utils` beside the live `mail:send`, so every one of their runs opens
with two *"no util by that name is in the library"* FAIL rows — six routines teaching their own
runs to skim their preflight. `routine.yaml` is the operator's config and no run may write it, so
the only writer that can reach those files is the daemon's own boot:
`src/rsched/migrate_dead_reserved_utils.py`, `MIGRATION(expires=2026-12-31)`, beside the other two
routine.yaml migrations. It walks EVERY `*/routine.yaml` under all three homes rather than a list
of six, drops only (a file that would be left without `mail:send` is recorded under
`no_live_mail`, never granted one), leaves a file naming no dead util byte for byte, and records
what it did in `.control/migrations/dead-reserved-utils.json`.

### Added — a browser-stage job carries the peak of its OWN container's memory cgroup (D226, the `uitest` half)

Since 0.478.0 the browser stage runs in the `uitest` container, whose cgroup the release gate —
in the engine container — cannot read; its ui-stage memory reading therefore carries
`covers_stage_work: false`. `deploy/uitest/cgroupmem.py` reads the peak INSIDE the container that
does the work and hands it back through the job spool: `memory.peak` preferred and reset per job
(`bound: exact`), unresettable or a concurrent job → `upper`, no `memory.peak` → sampled
`memory.current` → `lower`; the ceiling from `memory.max`, where a literal `max` yields no
percentage rather than a guess. Every reading carries `proves_no_oom: false`, stated in each
docstring: a sample can say *"this stage peaked at N GB of its M GB ceiling"* and can never say
*"no OOM occurred"* — a restart starts a fresh cgroup, so only the host's `journalctl -k` settles
that. No caps are set; that is D226's measure-then-decide second half.

## [0.490.1] — 2026-11-01

### Removed — the `headroom` extra, and the 29 packages it baked into the engine image

Nothing has imported `headroom-ai` since output compression became stdlib-only (see
docs/output-compression.md), yet the image still installed it — litellm, openai, boto3,
botocore, huggingface-hub, tokenizers, tiktoken and 22 more — and both the daemon's `CMD` and
the `uitest` entrypoint still asked for it, on a box whose daemon the OOM killer has taken
before. The Dockerfile, the compose file and the lock no longer name it.

The extra itself stays DECLARED, empty, for one transition: an image built before this release
starts with `uv run --extra headroom`, and uv refuses to start on an extra the project does not
define, so removing the name outright would have left the next restart on the old image unable
to come up. With the extra empty, that restart simply syncs the venv down to the new lock. Delete
the empty `headroom = []` from pyproject.toml once the image has been rebuilt.
(Rebuilt and deleted the same day, once no container started with it.)

## [0.490.0] — 2026-11-01

### A script without the util-library handle is told why — and told it about the right file

Reported from outside by `llmsectest-weekday` (R3052, F801): a helper shelling out to
`gu tts say` died with a bare `FileNotFoundError: 'gu'`, and the reporter concluded that this
host has no util front end at all. A reasonable reading of the only evidence it had — and wrong.

The helper HAD declared `calls: tts`. The engine reads the header of the script it RUNS, and
what ran was a bridge that declared nothing. Two files were plausible and only one was
consulted: that is the whole failure mode, so `run_script` now appends a note naming **the file
whose header it read**. *"This script declares no callees"* would send the author back to the
file they are already looking at, which in a bridge topology is the wrong one.

**The test written to confirm that found a second shape of the same failure, and it is the one
this host produces.** Which error a caller meets depends on the host:

1. `gu` is not on PATH at all — a bare OSError naming the handle. What R3052 reported.
2. `gu` IS on some inherited PATH but no `GLOBAL_UTILS_HOME` was folded in, so the dispatcher
   itself exits 2 with *"gu: library home … does not exist — run deploy.sh from the
   claude-utils-library repo"* — which reads as a broken INSTALL and sends the author off to
   deploy a library that is already deployed.

A note covering only shape 1 would have missed every real caller here. Both are the same cause
(no `calls:` line, so no handle), and the note says so, including that the message above it is
not a broken install.

The note is appended to stderr by `scripts.run_script`, where both facts are known, and only on
a non-zero exit whose output shows one of the two shapes — a script that declares its calls, or
one that succeeds, never carries it (both pinned). `engine/executor.py` sits at its size-ratchet
ceiling and is untouched. Tests in their own file for the same reason:
`tests/test_script_gu_note.py`, proven in both directions (without the fix, the assertion fails
on the bare dispatcher error).

## [0.489.0] — 2026-11-01

### The `outbound-mail` migration actually lands — pass 1 was a silent no-op

0.482.0 shipped `migrate_permission_requires` to take the two dead util names (`gmail:send`,
`fau-mail:send`) out of the `outbound-mail` permission's `requires:`, because a routine cannot
edit the library and the seed sync is add-only. **It never ran on the instance it was written
for.** It matched the dead list as a WHOLE LITERAL — `[gmail:send, fau-mail:send]` — while the
live file read `[mail:send, gmail:send, fau-mail:send]`: the live verb FIRST, which is exactly
what a hand-edit that ADDED the live name leaves behind. The literal missed, the migration
recorded *"already corrected by hand"*, and six routines (`birthday-admin`, `fau`,
`freelance-hunter`, `grant-hunter`, `kaution-leipzig`, `predatory-publisher-tarpit`) kept
opening every single run with two FAIL rows for utils that do not exist. Reported from outside
by `freelance-hunter` (R3113), carrying the operator's own ruling that the old mail utils must
give way to the general `mail` util.

Two changes, and the second is the one that matters beyond this file:

- **`_carry` is item-wise.** It drops the dead names from whatever the list holds and keeps the
  live one, in any order. A list naming no dead util is still left completely alone, which is
  the hand-correction guarantee this family of migrations rests on.
- **The record now carries a `PASS` number.** Every migration in this family guards on the
  record's mere existence, which is right for one that worked. This one shipped broken, and its
  own record — written by the pass that did nothing — then outlived the bug: no correction could
  ever run again. A record of the CURRENT pass is still a hard stop (pinned by its own test),
  and an unreadable record fails CLOSED rather than licensing a re-run over operator-edited
  files.

Proven against the real live file in a sandbox copy before shipping:
`[mail:send, gmail:send, fau-mail:send]` → `[mail:send]`, the operator's prose byte-identical,
a second pass a no-op. The FAIL rows themselves are emitted from each ROUTINE's utils list
(`readmodels/surface_caps.py:47-53`), and `floor_capabilities` keeps a reserved verb only while
a held doc requires it — so this release makes the remedy performable and the floor correct;
taking the dead names out of the six `routine.yaml` files is the operator's config and is asked
separately.

## [0.488.0] — 2026-11-01

### A routine's scripts have their own section in the run page's rail (operator order)

*"a routine's scripts should have a section in the right routine run sidebar"* (2026-11-01). A
routine's `scripts/` are the tooling its runs call every fire, and the run page — the surface a
run is actually watched on — named none of them: the only way to see what a run could call was
to open the recipe browser and list a directory.

- **`GET /api/routines/{slug}/scripts`** (`web/api_routine_files.py`) serves
  `scripts.list_scripts` — the SAME catalog the engine puts in a run's own capabilities digest,
  so the rail shows what the run was told it could call and a second description cannot drift
  from it. A script whose docstring header will not parse still lists, with an empty summary:
  discoverability must not depend on hygiene.
- **`static/components/runscripts.js`** renders it as a foldable rail section, mounted from
  `components/runrail.js` (which owns "which sections the rail has"). ONE read, no poller — a
  routine's script set is a property of the recipe, not of the run, and the console queues
  recipe edits to run end. The section is ADDED only when the read finds a script, like the
  jobs and oversight sections, so a routine with no `scripts/` gains no caption.
- The summary's leading `<name> —` is stripped client-side: `parse_header` keeps the
  docstring's whole first line, and repeating the name under its own heading is noise in a
  narrow rail. The route serves the header exactly as a run is shown it.
- `static/rail.css`'s per-block hairline now covers `[data-run-script]` as well: the card reuses
  `.runjob`, and without the second selector a routine's scripts read as one run-on paragraph —
  the defect that rule was added for (F646).
- `tests/ui/test_finish_line.py`'s exact rail-caption list gains `scripts`: the UI fixture's
  routine carries `scripts/outcome.py`, so its run page legitimately has one.

## [0.487.0] — 2026-10-31

### A classifier that cannot decide STRUCTURALLY now says so (R3108, increment 1)

Three places decide retry, failover and prompt-window policy by matching ENGLISH SENTENCES in a
provider's error body: `endpoints/base.py:417` (`"in-flight requests" in resp.text` → is this 402
retryable), `engine/overflow.py`'s `_OVERFLOW_HINTS` (4 phrases → shrink the run's window) and
`engine/degrade.py`'s `_FAILOVER_REASONS` (~20 needles → the health stream's failure class). The
operator's ruling on it was stronger than the proposal: *"Good idea! I want you to replace the
prose checks completely"*.

**This increment deliberately changes NO policy** — it builds the measurement that makes the
replacement safe, because nobody has the number it depends on: how often a structured decision is
actually unavailable on this instance. A new `error_class_undecided` health event fires in exactly
two places — where `degrade` would classify a failover reason as `other`, and where
`overflow._is_overflow_text` DECLINES on a 400 whose body quotes token counts. Every existing
retry, failover and shrink decision comes out identical, which is what
`test_no_existing_decision_changed` pins.

Two judgements worth recording. The failover hook sits in `_switch_to_fallback`, **not** in
`_log_failover`: the item's own motivating specimen died as `model_chain_exhausted … after 0
rungs`, a branch that never reaches the log, so the obvious site would have missed the case that
prompted the work. And the rationale lives in a new `engine/errorclass.py` because
`health_events.py` is EXACTLY at the 350-line bar — the first attempt put the event name on a
continuation line, 351, a breach.

What the increment does NOT yet do, so the next one is scoped honestly: **no measurement exists
yet** (the event only fires on a real provider failure); hook (b) misses 400s absorbed inside the
adapters (`openai_compat.py:198`, `anthropic_api.py:274`), so the count is engine-seam only; and
the event is deliberately kept OFF `health_stream.BLOCKED_EVENTS`, so the number is reachable
through `/api/health/events?event=error_class_undecided` but has no console card.

And the finding that shapes the replacement: the structured data is not merely unparsed, it is
**discarded**. `base.py:259` renders the body to `resp.text[:300]` and `EndpointError` carries
only that string — no status field, no decoded body — so `error.code` / `error.type` /
`error.param` reach no decision point at all. Two paths genuinely have nothing structured (a
transport timeout via `_transport_cause` has no response; non-JSON bodies), which is a constraint
on the replacement, not a reason to keep a needle list.

## [0.486.0] — 2026-10-31

### The inbox's SECOND record shape gets its one writer, and the policy scan polices both (D143)

The operator's answer: *"Build the seams the review named but could not reach, starting with the
two remaining `msg-*` writers plus the policy scan."*

**One scope correction, from reading the tree rather than the brief: the `msg-*` half was already
done.** `tests/test_policy_inbox_writer.py` exists and its `msg-*` checks pass unmodified. Every
direct inbox write left in the two modules the brief called "the two remaining `msg-*` writers"
is the OTHER shape, `answer-<qid>.json`, which had no writer at all — SEVEN sites in FOUR modules
(`web/api_questions` for the answer, the answer-and-run-now rewrite and the defer marker,
`web/api_question_decline`, `web/api_patch_applied`, `engine/inbox_questions.revise_answer`),
each spelling the filename itself and stamping its own `ts`. Exactly the state `msg-*` was in
before F499 unified it.

So the answer shape gets its own writer — `src/rsched/answerfile.py` — rather than being forced
through `file_message`, whose invariants it does not share: that seam validates `via` against the
closed `VIAS` set, and freight BUYS a run while an answer must never (`daemon/gate_prepare`, the
operator's reversal of 2026-09-12: an answer is work for a fire that already happened, never a
reason to fire). All seven sites routed through it, and the patch-applied route through its
`update`, which refuses to resurrect an answer a run has already consumed — a plain write
re-created the record and the next run read a settled decision as fresh, the answer-side twin of
the race `inbox.rewrite_message` holds a lock for. The writer also refuses a record neither
reader would consume (both require a `text` key or a truthy `defer`), which is the one catchable
moment: such a file is never read, never drained, and by the gate rule above does not even admit
the run that might have noticed it.

**The durable half** is the policy scan: `SANCTIONED_WRITERS` names the owner of each shape WITH
the reason it is allowed, and any other direct write into an `inbox/` is a test failure reporting
`path:line` plus the seam to call. It tracks a path bound to a NAME across the lines between (a
write sits up to a dozen lines below the expression that built it) and drops bindings at every
`def`, because `path` names an inbox answer in one function and the archive record two functions
below. An allow-list integrity check keeps it from going vacuously green.

`web/api_questions.py` SHRANK 394 → 393 and its ceiling is tightened to match. Docs:
`docs/messages.md` (both shapes and their writers), `docs/architecture.md`, `CLAUDE.md`.

### A closure claimed in prose over a row that is still open now has a reader (F793)

`report_threads.undisposed_claims` has existed since the thread model: a settled report whose
PROSE says it disposed of a row the ledger still carries open. It had exactly one reader — the
authoring run's own refusal — so a claim that slipped through was never seen again by anybody.

The second reader is `GET /api/items/orphans`, which already serves the two other "work lost from
this ledger" kinds as banners above the Messages list; this is a third group, *claimed closed in
prose, still open*. The logic is a new `readmodels/prose_closures.py` that BORROWS both halves
rather than re-deriving them — the claims from `undisposed_claims`, the liveness from
`report_threads.still_owed` — so the console and the refusal can never disagree about what a
claim is. It flags only a SETTLED, non-retracted claimant, and names the `owner` whose `settles`
can actually end the row. Reading costs no extra parse: `reports.read_reports` is memoized behind
the ledger file's stat fingerprint and `load` memoizes on the same fingerprint, because the
endpoint is console-polled.

`static/views/messages.js` was AT its 425-line ceiling, so the banner came OUT first as
`static/components/orphan-banner.js` and the group went in second (425 → 374, ceiling tightened).
That extraction fixed a latent defect of the existing banner: its deferral group filtered
`kind !== "undelivered"`, so ANY third kind landed in it and threw on `undefined.join`. Each
group now filters positively.

The surface reaches the CONSOLE only — the claimant's routine is not told on its next run, and the
card carries no action button, because letting the console settle a row on a routine's behalf is
an operator decision (D182, which `disposable()` refuses by design).

### A chat message's corner controls are no longer part of the text you select (R3085)

The operator, from his own conversation: the copy button on a message's code block "copies the
glyph, not the code" — he clicked it and got `⧉`.

**The reported cause is not reproducible, and that is recorded rather than quietly fixed around.**
`static/md.js` emits every fence as `<pre><code>…</code></pre>` (its only two `<pre>` producers),
`chat.js`'s `fenceBtns` reads `code.textContent` with `pre.textContent` only as a fallback for a
`<pre>` without a `<code>` child — which that renderer never produces — and
`tests/ui/test_flows.py::test_code_fence_copy_button` already asserts the clipboard equals exactly
the fence's source after a CLICK. It passes. Only three clipboard writers exist in all of
`static/`; the other two copy a URL.

What CAN put the glyph in his clipboard is the other way of copying: every one of these controls
is a CHILD of the thing it acts on (deliberately — each is pinned to a corner), with no
`user-select` rule, so dragging across the code block and pressing ⌘/Ctrl-C takes the button's
text with the code. The same was true of the bubble's own corner controls: selecting a whole
message took ⧉ ⑂ ⟲ ⚑ with it — **and the ↩ refer glyph, which the red-first run found** and which
run turns and finish banners share. `user-select: none` on all three families — still clickable,
still focusable, simply no longer text a selection can carry. Line-NEUTRAL in
`static/views.css`, which is at its 2024-line ceiling.

Five browser tests in a new file, each over a seeded REPLY — the only bubble that carries all six
controls, because ⑂ ⟲ ⚑ return null without a callback and an integer turn. Every check asserts
the control is PRESENT before reading its style, which the first version of this file did not: it
looked for `.branch-msg` on a user message, matched nothing, and so reddened one release gate
while its sibling glyph check had been passing vacuously. Proven red-first by stripping the
declarations: 4 of 5 fail, and the 5th — the click path, repeated here so the file fails if the
fix is ever made by moving the button out of the `<pre>` — correctly passes either way.

## [0.485.0] — 2026-10-31

### Hooks: the LAST dead point fires, and a declaration can reach the live library again (D152 step 9)

`hooks/points.py` has carried `restart` with an empty `fires` since the registry shipped in
0.458.0, and after steps 6-8 wired `child-exit`, `spawn` and `run-end` it was the last moment the
engine passed through with nothing firing at it. **All four points that shipped dead now fire.**

`restart` is the only one fired from OUTSIDE a run, and that is what made it different in kind:
the daemon is exiting, the engine is a separate process about to be killed, and the party that
knows a restart is happening is the one on its way out. So the site hands each live run's own
directory to the carrier instead of reaching a turn loop — `daemon/restart_hook.at_daemon_exit`,
called from `web/appwiring`'s lifespan shutdown half beside F746's breadcrumb
(`restart.mark_graceful_exit`), the one seam every graceful exit reaches: a deploy, a
`systemctl restart`, a container replacement, and the daemon's own self-update path.

The carrier is `run-end`'s: `state/notes.md`, whose tail the next state digest renders. A run
killed by a restart has no next prompt of its own, so the line is for whichever leg reads that
file next — the automatic resume (`daemon/reap_resume`) or the next scheduled run. It is NOT a
second copy of the resume note: `reap_resume` files an inbox message and is the better channel
where it applies, but only for a run the next boot RE-OPENS, and a leg whose resume is refused
(cap spent, routine paused, cause never established, run gone) gets nothing — which is exactly
where F746's measurement sits: 79 orphans over 50 boots, 15 of the 20 carrying a cause at all
reading `unknown`, and not one ever reading `daemon_restart`.

New predicate `work-cut-by-daemon-exit` (`engine/assist_restart.py`): a PARKED run declines it,
because a run waiting on a person was not working and saying its work was cut would be a claim
about something that did not happen. `assists.MOMENTS` gains `restart`, carrying `note` only —
no model left to remind, no action in hand to hold. `assist._rendered` becomes public
`assist.rendered_line`, so the daemon-side site renders a fired assist exactly as every in-loop
rung does rather than growing a second format. `assist_predicates.py` stayed AT its 350-line
ceiling: four registry entries compacted to pay for the new one.

**And the half without which it would have shipped dead.** 0.477.0 found that the points steps
6-8 wired were firing against ZERO candidates, because `bootstrap.sync_seed_library_docs` is
add-only at the FILE level and cannot carry an `assists:` block into a rule the live library
already has. Its one-shot `migrate_rule_assists` fixed that once and by construction could never
fix it again — it refused a rule that already declared assists, which is every rule it had just
repaired, so a declaration added by any later release could not reach the live library and the
next point would ship dead exactly as those three did. The carry is therefore now a RECURRING
sync at the granularity the problem actually has, the ENTRY: `rsched/seed_assists.py`, add-only
per declaration id, refusing to overwrite an entry (the operator's wording outranks the seed's),
to resurrect one this rule's own history removed, or to create a rule the live library lacks.
`migrate_rule_assists.py` and its test are DELETED under the delete-after-convergence policy.

23 tests (14 + 9), red first: five sibling test files pinned `gaps == {"restart"}` and now pin
the empty set. Docs swept — `docs/rule-assists.md` (eight moments), `docs/architecture.md`,
`CLAUDE.md`. **Honest limit: proven by test only.** No graceful daemon exit has happened while
this code was live, so the fire has no live specimen yet; after the next restart a run
interrupted mid-work should carry a `hook restart` line in its routine's `state/notes.md`.

### A run is told what is LEFT of its budget, once per turn (D227)

The operator, 2026-10-31, answering the builder's R3094 §1: *"Why not just remaining turns,
minutes, tokens (if limited)?"* — remainders and nothing else. No cost figure, no usage
breakdown, no history.

`[BUDGET LEFT: 11 of 12 turns · 44 of 45 min]`, with a token part only when a token ceiling
exists, and `179 of 200 turns (this resume window, re-based at turn 327)` on a resumed leg.
A run was told its ceilings exactly once, at turn 1, and never its consumption — so it paced by
the wall clock: on the run that raised this, 265 min since the run id against a 240-min ceiling
while the engine had metered 144 (121 of those minutes dead in a container recreate), i.e. "stop,
you are 25 over" with 96 min left. The workaround was hand-reading `status.json`: a turn spent,
and a run reaching into engine internals for a fact about itself.

**The seam is a TAIL, not the contract.** `engine/loop._tails`, where the budget WARNING already
rides — because `engine/composer.py` composes the harness contract and the state digest ONCE at
run start, so a number that changes every turn would be frozen at turn 1 there and a lie on every
later turn. `test_every_observation_tells_the_run_what_is_left` is what tells the two apart: it
asserts the remainder MOVES, 11 → 10 → 9 → 8.

**One reading, not two.** `turnwindow.budget_block` is now the single computation:
`write_status`'s own ledger/meter/turns_left copy is deleted and it calls the same function, so
`status.json` and the prompt cannot drift apart and tell a run different things. That also shrank
`run_context.py` 593 → 587 (it was AT its ceiling with zero slack, so the fix was to REMOVE the
second reading rather than add a third), and `loop.py` stayed net-zero at 437 by moving the
warning's prose into `turnwindow.converge`.

**What it costs, measured and pinned** in `tests/test_budget_left_line.py`: 46 B/turn for the
common run, 75 B with a token ceiling, 91 B resumed, **0 B when nothing is limited** — ~14 kB over
a 300-turn run, against the 63,670 chars of contract those same turns already re-send.
`tests/prompt_budget.json` is UNCHANGED and its note says why: that file measures
`composer.system_sections`, and this tail is not in it. `(if limited)` is literal — an unlimited
axis prints nothing rather than the word "unlimited".

Both of the decision's constraints are honoured and tested: the line states a FACT and never asks
the run to pace (the contract insists a budget is "a CEILING and a runaway BACKSTOP — never a
pace, and never a ration"), and every remainder is given beside its own ceiling so
`window_turns + turns_left == max_turns` stays legible (F564/D155) and a resumed leg's fresh
window cannot read as a spent one. 9 tests, red first; `docs/prompt-anatomy.md` §3b gains item 7
and §5.3's claim that an ordinary observation carries "nothing else" is corrected.

Deliberately NOT in the line, faithful to his wording: `max_cost` and the run-spanning
`max_total_turns`. A run with a cost cap is still told nothing about it.

### A stale model role is visible where a person looks (R3106, F643)

Two routines each carried a bare `Sonnet` stored nine weeks earlier; after the catalog rename
both died at boot — rc=1, no finish, no summary, no `result.md`, an orphan row and nothing to
read. `trials.roles_problem` already detected it and was reachable only from `rsched validate`,
a command a person types, while its WEAKER sibling `ignored_problem` was on the routine page: the
lesser failure had a surface and the total one did not. New
`trials.config_problems(server, cfg, trial)` composes both where their predicates already live,
and `web/api_routines.py` stays at its 381-line ceiling (five lines in, five out).

It judges the CHAT roles only. `decision`/`decision_media` sit in the same `models:` map but
resolve against the decision catalog, so the literal change the report asked for would have
printed "the run dies resolving it" for a working decision model — a false alarm on the one panel
that has to be believed. Three API tests plus a browser test that LOADS the routine page and
asserts the `.panel.err`, all red first.

### The skipped-stage count names the release its ruler moved in (R3107)

`COUNTED_SINCE["stages_skipped"] = "0.366.10"` shipped in 0.454.0; what was missing is the
regression pin and the reason, so a future reader cannot delete the entry as unexplained.
0.366.10 (F563) is the release that last moved the COUNT — it began crediting a stage the run
WROTE into its phase cursor — while 0.402.0 changed only what the finish's claim check DOES with
an unentered stage, which this number never read. The comment now names the one-way bias as well:
a run that orients faster re-reads fewer modules, so a release improving orientation would read
as making the fleet LESS complete. Proven by temporarily removing the shipped entry
(`assert 3.0 is None`).

## [0.484.0] — 2026-10-31

### The gateway's reported cost is what bills a call; `-metered` is the paid route of the same model

Since 0.483.0 every model call goes through the Otari gateway, which prices each request itself
and returns the figure in the usage block as `cost_usd` (a six-decimal USD string). The
`anthropic` adapter read no reported cost at all, so pay-per-use Claude models through the
gateway (`claude-opus-5-metered`, `claude-opus-5-5`) were metered only by a reference list price
— and `claude-opus-5-metered` found none, leaving its calls unpriced and a cost budget on it
unbounded.

- **A reported bill is read on both wires.** `endpoints/usage.reported_cost` reads a usage
  block's billed dollars — OpenRouter's numeric `cost` first, else the gateway's `cost_usd` —
  parsed through `Decimal`, and a missing, malformed, negative or non-finite value is no figure
  (never a zero, never a guess). The anthropic adapter now files it as usage `cost`, the field
  the openai adapter already filled from OpenRouter, and the openai adapter takes `cost_usd`
  when no `cost` is there (a malformed `cost` used to raise in `float()` and end the call). The
  two decision adapters read it the same way. The semantics are unchanged: reported `cost` is
  the bill and the pricing seam takes a non-zero one at its word as `notional`; a subscription
  call's reported `"0.000000"` is its true bill, kept as `cost`, and its `notional` is still
  the list price.
- **A re-asked turn bills both requests in dollars.** The anthropic adapter summed a forced
  re-ask's two usages key by key as integers, which would have truncated a sub-dollar `cost` to
  nothing; it now folds them with `fold_usage` (`endpoints/usage.summed`).
- **`-metered` is the gateway's suffix, not part of the model's name.**
  `catalogs.reference_name` drops a trailing `-metered` (the suffix only) before the thinking
  level, so `claude-opus-5-metered` gets `claude-opus-5`'s list price (`prices.price_name`) and
  window (the limits reference lookup) — the fallback for a call whose bill was not reported.
- Docs: `docs/model-gateway.md` gains "What a call costs"; the cost-source passages in
  `docs/endpoints.md`, `docs/run-analytics.md`, `docs/decision-models.md`,
  `docs/architecture.md`, CLAUDE.md and the touched module docstrings now name the gateway's
  figure, and the ones saying no `anthropic` adapter writes a cost speak in the past tense.
- Tests: `tests/test_reported_cost.py`.

## [0.483.0] — 2026-10-31

### Model traffic leaves through the separate gateway; the compose proxy and the decision server move out

Every model call on this instance already goes through **Otari**, the gateway in the separate
[llm-interfaces](https://github.com/wehnsdaefflae/llm-interfaces) stack, whose ONE standalone
CLIProxyAPI holds the subscription logins. This release removes what only existed for the proxy
this repository used to run itself:

- the `cliproxy` compose service and its `claude-proxy` profile, `deploy/cliproxy-init.sh`,
  `deploy/cliproxy-migrate-uid.sh`, `deploy/cliproxy.config.example.yaml`, and the tests that
  existed for them (`test_cliproxy_init.py`, `test_cliproxy_migrate_uid.py`,
  `test_deploy_proxy_ports.py`, the proxy section of `test_deploy_state.py`). Compose now pulls no
  image at all, so `test_every_image_compose_pulls_is_pinned` holds the NEXT pulled image to the
  pin and asserts the built/reused split it depends on instead of asserting that one exists.
  `test_deploy_selection.py` drives nat64.sh's profile check with the `desktop` profile, the one
  profiled service left — the check itself was always generic and is unchanged.
- **Kept**: the CLIProxyAPI management client (`endpoints/cliproxy_mgmt.py`, `cliproxy_login.py`,
  `cliproxy_quota.py`), the endpoint watch and the console's account rows and sign-in. They now
  run against the standalone proxy through a model-less `cliproxy-mgmt` endpoint
  (`quota_source: cliproxy`), whose card shows every account, every sign-in and the quota line.
- `deploy/decision-server/` and its protocol test moved to their own repository,
  [local-model-server](https://github.com/wehnsdaefflae/local-model-server) (deployed on predator
  as `local-model-server.service`, same port); every reference points there.
- `docs/claude-proxy-cutover.md` is replaced by **`docs/model-gateway.md`** — the four gateway
  endpoints, normalised model ids, signing in through `cliproxy-mgmt`, decisions through the
  gateway — and the docs sweep (CLAUDE.md, DOCKER.md, SETUP.md, endpoints, architecture,
  getting-started, README, the deploy scripts' comments) describes no service that no longer
  exists.

**Deploying it**: a host whose `.env` still says `COMPOSE_PROFILES=claude-proxy` drops that
profile (keep `desktop` if it runs the desktops), and a `routine-scheduler-cliproxy-1` container
left from the old service is removed with `docker compose up -d --remove-orphans` — otherwise
`deploy/nat64.sh` refuses, correctly, because a container sits outside the selection.

### A gateway's reworded `response_format` refusal degrades the turn instead of failing it

The `openai` adapter retries once without `response_format` when a 400 names it. Otari rewords a
provider's refusal as *"Model X does not support the requested response format."* — a space, no
underscore — so the retry never fired. `"response format"` joins the hints
(`endpoints/openai_compat.py`); `tests/test_openai_rf_refusal.py` pins the reworded refusal and
that an unrelated 400 still keeps its schema.

### System One decisions carry images

`endpoints/decisions_systemone.py` refused every image. The gateway's `/api/v1/systemone` and
llama-server's take an optional `images` list of `data:` URLs (at most 16), so the adapter now
sends a request's images there and refuses only more than 16. Both protocols carry images now;
`DECISION_MEDIA_PROTOCOLS` became `DECISION_MEDIA_DEFAULT` (on for `openai`, OFF for `systemone`,
because Jev itself reads none), so a vision model on a System One server is switched on by its own
`multimodal: true` — which the loader and the settings route no longer refuse. The console's
protocol line says so, the add-model form's *protocol default* names what it resolves to, and a
preset for a System One gateway or local server joins the provider list.

## [0.482.0] — 2026-10-31

### The console's responses are compressed (the operator: "super slow")

*"The webui at rsched.markwernsdorfer.com is super slow. Fix it"*. Measured before changing
anything: **no `content-encoding` on any response** — not from the app, not from Caddy in front
of it — while `GET /api/questions` returns ~1.1 MB of JSON (the whole fleet's question HISTORY,
rows settled since September: no limit, no window, no projection) and `static/app.js` refetches
it **every 30 s for every open tab**. The server itself was not slow: 2.6 ms locally for `/`,
0.60 s worst case net.

So the first fix is the one that changes no behaviour at all — `GZipMiddleware` beside the CSP
in one seam, `appwiring._add_middlewares`. `static/app.js` goes **19,753 → 7,314 bytes on the
wire (37 %)**; this instance's captured API payloads compress to ~31 %.

**Where it is wired is load-bearing, and the test suite found it.** `app.py`'s two
`@app.middleware("http")` layers are `BaseHTTPMiddleware`, which re-emits every response as a
stream, and starlette's gzip skips a small body only when it arrives in one chunk with
`more_body=False`. Wired OUTSIDE them, a 9-byte artifact download was gzipped and lost its
`Content-Length` — breaking `test_served_files_keep_their_bytes_type_and_freshness`, a contract
about artifact downloads with nothing to do with compression. It is therefore wired INSIDE
them, and `tests/test_response_compression.py` pins all of it: a large payload compresses, a
small body is byte-identical with its length intact, `identity` gets plain bytes, and the CSP
survives compression.

Capping `/api/questions` is a separate, behavioural change and is not in this release.

### A run is asked for its own recipe revisions only if it HAS a recipe

*"'bg-c-20261029-080013-cd837685 proposes 1 revision to its own recipe' this is a bug never
suggest recipe improvements for anything else but routine runs"*. A detached background task is
a one-shot brief he typed: no recipe, no `## Done when`, nothing a proposal could edit. The ask
was gated on `is_conversation` alone — false for a dir under `background_home` — and the card
only on `ctx.depth`, so tasks were asked and filed cards.

One predicate now governs both seams, `recipeproposal.asks_for_revisions(ctx)`: a child run, a
conversation and a detached task are not asked and cannot file. Suppressing one seam alone
would leave either a card nobody can apply or a question about a document that does not exist.

### A null recipe revision files nothing (R3086)

*"why are you surfacing this at all? don't propose null revisions. report this as a bug!"* The
ask REQUIRES a run to say when its recipe needed no change — that is how "I looked and nothing
was contradicted" is distinguished from not having looked — so the compliant wording
(`RECIPE REVISION: none.`) produced one empty card per clean run, and `vodafone-outage-claims`
is daily: he had to edit and approve a change that said there was no change. `extract` now
drops a line whose whole content says there is no revision, matched against a closed set after
punctuation is stripped, so "none of the stages name the cursor" is still filed.

### A permission's `requires:` names the live util (R3081)

`predatory-publisher-tarpit` opened every run with two FAIL rows it could not fix from inside
itself: `outbound-mail`'s `requires:` still named `fau-mail` and `gmail` after those utils were
consolidated into `mail`. The consolidation reached every caller's code and nothing reached the
stores that hold util names as STRINGS — five symptoms of that one cause have now been found
weeks apart by different routines (four reminder regexes, four task notebooks, this permission).

The seed is corrected, and because `sync_seed_library_docs` is add-only (it must never overwrite
an operator-edited library doc), `migrate_permission_requires` carries it to the live library
once at boot. It rewrites only the `utils:` list, leaves the body prose byte for byte, and
leaves a file he already corrected alone. Measured: `outbound-mail` is the only permission of
the 18 seeded / 20 live whose `requires:` names a util the 57-util catalog no longer holds.

**And the cause reached a second store, which the suite caught and the first attempt at this
release missed.** Correcting the permission alone reds
`test_patterns.py::test_a_shipped_patterns_capabilities_survive_its_own_permissions`: four
settings patterns (`radar`, `project-steward`, `mailbox-service`, `personal-steward`) reserve
`fau-mail:send` + `gmail:send` and nothing else, and `floor_capabilities` keeps a reserved util
only while a HELD doc requires it — so every routine created from those patterns would have had
its send reservation floored away at its first save. A *partial* correction of these stores is
worse than none. Both seed and live patterns are carried by the same migration; the six live
routine configs that name the dead verbs **beside** `mail:send` are untouched (nothing is
blocked — the rows they produce now offer "drop the dead name" instead of "install a util that
will never exist", which is the whole visible effect of the correction).

## [0.481.0] — 2026-10-30

### The browser screen has a clipboard-sync switch, under the frame (D221)

The operator: *"can you make the novnc browser share the clipboard with my host machine?"* — he
chose the full two-way clipboard over the recommended one-way paste, and then, shown that the
channel was already open: *"add a checkox if we want the clipboard to sync or not. under the
novnc iframe"*.

This is a switch on an existing channel, not a new one. What gates the clipboard is the iframe's
**permission grant**: the embedded noVNC page can reach the host clipboard exactly while the
embedder permits it. `x11vnc` already runs with no `-nosel`/`-noclipboard`, so the RFB side
relays the X CLIPBOARD selection both ways, and the full-screen page already drops `view_only`.
So the control is the `allow` attribute — and OFF means the attribute is **absent**, not merely
different.

Default on. The choice is remembered in this browser's own storage, because it is a property of
the machine the operator is sitting at rather than of the instance. Flipping it re-mounts the
frame (Permissions Policy is evaluated when a document loads, so changing `allow` on a live frame
would do nothing), and the control's title says so rather than silently reconnecting the session.

The right rail's read-only mirror is untouched: it keeps `view_only=1` and `pointer-events: none`.

## [0.480.0] — 2026-10-30

### A turn's side fields and a fired rule NAME what they point at, and link it (F591)

The operator, 2026-09-30: *"also if reminders or rules fire it needs to be nicely highlighted,
formatted, and link to the specific rule or reminder"*.

Until now a `remind` / `remind_feedback` side field left no trace in the transcript outside the
action's collapsed JSON fold, and a rule fire was prose inside a collapsed observation with the
rule slug as dead text. Both now render as their own rows:

- **side-field rows** — `note`, a reminder written or revised this turn (with its pattern and
  description), and the label a run put on a hold — each linking the reminder's row in that
  routine's Recipe health;
- **a rule-fire strip** OUTSIDE the fold, speaking the row-state vocabulary, so a `pre-action`
  assist that HELD an action is the loudest row on the page and cannot be scrolled past; each
  fired rule links its prose, and a declaration id rides along faintly so a rule with several
  declarations says which one fired.

New `static/components/concept-rows.js` rather than growth in `static/components/transcript.js`,
which is held at its ratchet ceiling. Links come **only** from structured fields the payload
already carries (`a.remind.id`, `a.remind_feedback.id`, `o.assists`) and never from scanning
prose — a slug like `work-order` would match ordinary English in every say line on the instance.
`slug` is a new optional transcript option: without it an id renders as the plain code it names,
because a link with no routine to point at is worse than dead text. 147 lines of browser tests
cover the positive and the negative case.

## [0.479.0] — 2026-10-30

### A blocking question carrying a config patch is answerable where it was asked (F807)
- The operator reported he could only answer such a question on the Decisions page, not in the
  conversation he was in. **Established: by OMISSION, and worse than reported.** `configProposal`
  is imported by exactly one module, and `interact.py` published the live `status.json` question
  WITHOUT the patch — while everything shown during a block is built from that copy. Meanwhile
  `web/decisions_read.py` builds a live blocking row from `active.question` and skips the durable
  pending record by qid, so while the run waited **no surface at all** could offer *approve &
  apply*.
- **The full apply control, not a link**: new `config_bridge.live_fields` publishes
  patch/target/home into the live question (one line in `interact.py`, which was at 346/350), and
  `answerform.js`'s question panel mounts the factory above the form with *"answering never
  applies it — take the change here first"*. The same line restores the Decisions page's live-row
  button with no edit to `decisions_read.py` (350 lines, zero slack).
- **Left open deliberately and stated:** a blocking question with a patch can still TIME OUT on
  its stated default, and the observation the model then reads names neither the patch nor that it
  stayed unapplied. That is a separate one-line `obs_admin` fix, not scope creep here.

### A finish rejection says ABSENT when the field never arrived (F805), and the outcome check agrees with the schema (F806)
- `birthday-admin:20261022-080004` burned ~10 turns: nine consecutive finishes carrying
  `accounting: null` were each told *"your accounting is incomplete: no entry for d1 … g1"*, a
  verdict about CONTENT for a field that arrived absent. New `accounting.arrived()` and an
  exclusive `absent` category through `finishgate._unanswered` (read from the RAW action) and both
  console renderers: such a finish now reads *"no `accounting` field arrived with this finish — it
  was ABSENT, so this is not a verdict on what your entries say"*.
- **F805's second defect does not exist, and no fix was invented for it:** the schema-retry path
  CANNOT drop a list field — every attempt is a fresh `endpoint.complete()`, `normalize_action`
  only drops already-empty values, and the real dropper (a provider's pseudo-XML swallow) is
  already repaired in main via `engine/paramsalvage.py`.
- F806: the finish schema prescribes two labelled parts while the outcome-first check demanded the
  numbers above them, so two routines paid a turn a week apart. Fixed on the CHECK side only —
  `outcome_words.done_when_line` now says *"opens its `**Work:**` part with the outcome"* and the
  judge prompt says where the outcome belongs. The schema and the `outcome-first` rule are
  untouched. **12 live routines still carry the old clause verbatim**; the judge-prompt change is
  what protects them, and migrating those recipes is routine-improver's call.

### A conversation can clear its own access denials (F783)
- A Decisions-page deny_forever writes a `{entity: false}` tombstone into the conversation's own
  `routine.yaml`, and the only editor that removed one was the ROUTINE page's — which no
  conversation has. **Built the editor, not the documented exclusion:** no code, comment, doc or
  test anywhere states a reason conversations are excluded, while `grants` is already classified
  LIVE and a conversation's false rows are already enforced. An omission, not a decision.
- Parallel router `web/api_conversation_grants.py` plus a shared `declined-access.js` card, with
  the routine page refactored onto the same renderer. One deliberate divergence: the conversation
  panel clears ONE named row rather than replacing the `grants` map, because that panel writes on
  click while the routine page is a draft the operator accepts — a replace would drop an
  allow-forever row arriving in between.

### A run whose prompt floor already exceeds its window is recorded at boot (D223 = F539's second half)
- `engine/boot.py` had no window/floor comparison, so a run that could complete no turn from turn
  0 still started and died mid-run — `llmsectest-weekday:20260922-060001`, dead at turn 106 having
  spent $1.03. New `engine/promptfloor.py` files `prompt_floor_over_window` with `floor_tokens`,
  `window_tokens` and the model as FIELDS.
- It RECORDS and never refuses, which is the operator's decision and not timidity: the window read
  is the CATALOG figure that an F278 run-local correction can make smaller than the real clamp, so
  a veto would rest on an approximation while turning a degraded run into no run.

### A holder that DECLINED what a change needs is named in the impact (D222)
- F609 rightly stopped a recorded decline reading as a GAP, but `library_impact._unmet` collects
  exactly the gap severities — so an authoring approval's break list stopped naming a routine that
  genuinely will not get the credential a util newly declares. `_unmet` now keeps the `denied`
  state too, and `impact_lines` words it *"declined, so this util will not work there"*, never
  `BREAKS`. The Library save's 409 says "affects" and renders those lines, so one wording serves
  both surfaces. `library_watch` is unaffected: it collects `blocks` rows only.

## [0.478.0] — 2026-10-30

### The browser suite no longer OOM-kills the daemon: its own container, and runs rank above the daemon
- **What was happening.** Every "orphaned: process gone at daemon boot" burst from 2026-10-22
  to 2026-10-30 (eight of eight) followed, by one to two minutes, a kernel record on the host:
  `Memory cgroup out of memory: Killed process … (rsched)` in the ENGINE container's cgroup,
  during a release gate's browser stage. Since 0.440.1 that stage launches a Chromium per xdist
  worker, so the 5 GiB cgroup held the daemon, four test workers, four Playwright drivers and
  ~25 Chromium processes; the kernel chose the daemon (largest resident size plus swap, every
  adjustment 0), the container's main process died, and `restart: unless-stopped` started a
  fresh one that reaped every live run. Three runs had concluded "not an OOM" from
  `memory.events` reading `oom_kill 0` — read after the restart, from a cgroup that did not
  exist when the kill happened.
- **The browser stage runs in a container of its own** — new compose service `uitest`: the
  engine image (`uv run --frozen`, production's interpreter and lock), mounting the checkout and
  a read-only browser cache and nothing live, with a memory ceiling of its own in the host's
  override. Its only process, `deploy/uitest/runner.py`, runs the jobs a caller drops into
  `<checkout>/.uitest-jobs`; `deploy/uitest/submit.py` writes one and streams it back as if it
  ran in place — output into the caller's log, the job's exit code, a SIGTERM ending the job —
  so the gate's `flock`, log parsing and deadline kill are unchanged. The job's environment is
  allow-listed (no injected credential is written to disk), a module outside the checkout rides
  along with `--carry`, and no answering runner is exit 77, which the gate already reads as
  `unavailable`. Whoever can write the spool can already write the code the daemon imports, so
  the spool grants nothing (`deploy/uitest/protocol.py`). By hand: `docker compose exec uitest
  /opt/rsched-venv/bin/python -m pytest -q -m ui`.
- **Under memory pressure the kernel now takes a run, never the daemon** (`rsched/oomrank.py`):
  `engine-run` raises its own `oom_score_adj` to 1000 before anything else, and every util,
  script, `shell` command and test suite it spawns inherits it; the daemon keeps 0. A killed run
  costs one run; a killed daemon cost all of them. Set by the engine because a jailed process
  cannot set its own — `/proc` is read-only in the sandbox.
- **The orphan row stops sending people outside the box.** `daemon/containerbirth.py`'s
  sentence named "a compose restart policy, a healthcheck, a deploy or an auto-updater" (a
  healthcheck restarts nothing here) and its docstring argued against an OOM from the
  post-restart counter; it now names the OOM killer first with the host command that confirms or
  rules it out, and the docstring records why the counter could not see it.
- The test harness's two "how to run the browser suite" messages, CLAUDE.md, README and
  deploy/DOCKER.md ("The browser test container", and the OOM rank) say `uitest`; the
  instructions to run the suite in the engine container over the sidecar were stale since
  0.440.1 and now describe only the opt-in WATCH mode. `tests/test_deploy_image.py`'s pinned-pull
  rule no longer counts an image another service in the same file builds.

## [0.477.0] — 2026-10-30

### Hooks: the `run-end` point fires, and a rule's assists finally reach the live library (D152 step 8)
- **`run-end` is live** — the first hook point whose carrier reaches the NEXT run rather than
  this one. It fires in `engine/loopend.finish_run`, after `result.md` is written (the point's
  own words) and before the autocommit, so a line written for the next run is committed with the
  run that learned it; a rewind rewrites `result.md` without going through `finish_run`, so it
  cannot re-fire. The dead-point list is now **one**: `restart`.
- **The carrier is not a new store.** `state/notes.md` is already the channel every state digest
  renders, so a hook writes one stamped line there through the same writer the `note` field uses
  (`engine/notes.append_line`, factored out of `capture` so the two cannot drift apart).
- New payload **`note`** — `remind`'s counterpart at a moment with no model left to remind — and
  a new predicate `children-killed-at-end` (`engine/assist_runend.py`): the run ended while
  children were still working, so they were terminated; their output is on disk in `sub/<n>/` and
  nothing in the next prompt said so.
- **The declarations were never reaching this instance.** `evidence-discipline` has declared
  assists in `library-seed/` since 0.471.0 and the live library's copy declared none, because
  `sync_seed_library_docs` is add-only — so the points steps 6 and 7 wired (`child-exit`,
  `spawn`) were firing against zero candidates, a wired point indistinguishable from a dead one.
  New one-shot boot migration `migrate_rule_assists.py` inserts the seed's block into the live
  frontmatter and leaves every other byte alone (the live prose has been revised since seeding),
  refusing a rule whose live copy already declares assists.

### The run page has a routine rail (F619, the operator's ask of 2026-10-04)
- *"i want a list of all routines next to a routine run page just like there's a list of all
  conversations next to a conversation. the only difference is that the routines can be expanded
  to the previous runs"* — built whole: the run's own routine is marked and arrives expanded to
  its previous runs; any other routine expands on click and only then reads its runs.
- A NEW component (`static/components/routine-rail.js`), because `run.js` and `conversations.js`
  are both over the one-responsibility budget and shrink-only. Rendering it found a defect four
  green tests could not: previous-run dates wrapped at the 218 px default width — fixed in CSS
  and pinned by a fifth browser test. The rail does not poll: the list is as it was on arrival.

### A cost ceiling binds on an anthropic route (F758)
- `budgets.max_cost` metered `usage["cost"]`, which only `endpoints/openai_compat.py` ever wrote
  — so the ceiling documented as "a whole-dollar ceiling on real provider spend" bounded nothing
  on `https://api.anthropic.com`, while 17 of 30 enabled routines inherit one. It now meters
  `endpoints/usage.dollars_spent` (notional first, a provider's reported cost as fallback), which
  is stamped on every completion whatever transport carried it. `child_budgets()` is repaired by
  the same line: its slice was derived from spend a cost-blind parent never saw.
- **Disclosed:** on a subscription route the cap now binds LIST-PRICE dollars, not an invoice —
  a run can trip a $5 cap having been billed nothing. Nothing changes behaviour today (no routine
  sets a finite `max_cost`), and a cap on an unpriced model still bounds nothing, which
  `prices.py` refuses to estimate. Said in all four doc surfaces rather than hidden.

## [0.476.0] — 2026-10-30

### Fixed
- **An engine note told a live run to stop trying something that worked** (F802, from R3057).
  When the operator granted `machine:omen` mid-run, the grant-applied note said *"Saved, but it
  takes effect at your NEXT RUN … machines: the machine bindings and share mounts are resolved at
  boot"* — and the very next action in the same process ran `gu remote list --json`, got omen
  back with `"ready": true`, and `gu remote test omen` succeeded. The claim came from one table
  entry, `configflow.CLASSIFICATION["machines"]`, whose `why` is what the operator reads.
  It is wrong for the EXEC half: `engine/exec_env._machine_env` composes the machine env per util
  call from `ctx.routine.machines` **plus `ctx.granted_now`**, the run's own grant overlay that
  `engine/requests.apply_decision` writes the moment an access request is answered. The half that
  really waits is the sshfs SHARE MOUNT, which the daemon makes at binding time —
  `_machine_env`'s own docstring already draws that line ("A one-time grant covers EXEC only").
  The entry stays `NEXT_RUN` (a config edit does not reach a live run) and its reason now names
  both halves, the way the `fs_read_roots` entry has always done.
  New `tests/test_configflow_machines.py` pins the prose to the behaviour from both sides: a
  grant-only `granted_now` really does produce the machine env, and the reason may not deny it.
  Red-first proven by restoring the pre-fix string: the prose test fails on
  `assert 'mid-run' in 'the machine bindings and share mounts are resolved at boot'`.
  `tests/test_configflow.py` could not have caught this — it asserts that every field declares a
  half and a non-empty reason, never that the reason is true.

## [0.475.0] — 2026-10-30

### Fixed
- **A supervisor rung was never told what the run it judges is FOR** (F803). `ladder._goal`
  probed `ctx.routine` for `instruction`, `task` then `name`, and `ladder._stopping` probed it
  for `done_when` and `finish_line` — but `RoutineConfig` declares **none of those four** and
  `config/base._Config` sets `extra="ignore"`, so an undeclared key cannot survive as an
  attribute either. Measured on 0.474.1 against a real config: `_goal` returned the two-word
  DISPLAY NAME (`"Self audit"`) via its `name` fallback and `_stopping` returned `[]`, on every
  rung ever dispatched — while `_goal`'s own docstring promised "the instruction it was started
  with, never a paraphrase: a supervisor judging drift against a summary of the goal cannot see
  drift INTO that summary". A display name is the most extreme summary available.
  The two readers move to a new `engine/laddergoal.py` and read the sources that actually hold
  the answer, the same three `engine/finishgate.py` composes: `loop.instruction` for a child run
  or conversation (where the prompt really lives — `instruction.md` is a compile seed a real
  routine never persists, so it is `""` for a scheduled run), then `donewhen.read()` for the
  recipe's `## Done when`, `finishline.open_outcomes()` for the routine's finish line and
  `goals.owed()` for the open goals a person asked. Each stopping line keeps its id, so a
  directive can name `d2` or `b1` instead of paraphrasing it. The `getattr` defaults that
  absorbed the mismatch are gone.
  This survived a green suite because `tests/test_ladder_trigger._Routine` defined exactly the
  three attributes the real config forbids, so the readers were proved against a shape
  production never produces — the same class as R2348/R2352 in this same module, where the fake
  said `subs`, so did `ladder.py`, and every rung died of an `AttributeError` for months behind
  green tests. The fake is stripped of all three, and the new `tests/test_ladder_goal.py` pins
  the invariant against `RoutineConfig` itself (`test_the_reader_cannot_be_fooled_by_a_fake_again`)
  so no fake can hide it a third time. Found by this run's deep cruft sweep.

## [0.474.1] — 2026-10-29

### Fixed
- **The failed-backup push could never be delivered.** `deploy/rsched-backup-failed.service`
  wrote bash's `${NTFY_URL%/}` straight into ExecStart, which systemd expands itself before bash
  runs; it could not parse the name, substituted an empty string, and curl exited 3 on a URL with
  no host. Its first real firing (2026-10-29 20:51, once the unit was finally installed) is how it
  was found. Every `$` and `%` bash must see is now doubled, which also keeps the topic out of the
  process's argv; a test holds the command to it.
## [0.474.0] — 2026-10-29

### Added
- **Both ends of a child's life are hook points now** (D152 step 7, b4). `spawn` FIRES — the twin
  of step 6's `child-exit` — so a rule reaches the one run-tree moment where something can still
  be changed: a running child cannot be re-briefed, only killed and started again, so a rule
  reads the BRIEF back at the first boundary after it was sent. New
  `engine/control.announce_started_subruns` (called from the turn boundary; it appends no message
  of its own, because the run already has the spawn observation in its prompt),
  `Subrun.announced_start` so the point fires once per child however many boundaries pass, and
  `assist_child.py` becomes the RUN-TREE predicates module with `thin-child-brief` — a brief
  under 400 characters, or long enough while naming nothing the child can open — declared by the
  seed rule `evidence-discipline`. The registry's dead-point list is down from four to **two**
  (`run-end`, `restart`, both of which carry `prompt_section` and so reach the NEXT run).
- **A conversation can be given its own scoped secrets, from its own page** (D216 rank 1, F782).
  The engine always injected `secrets.d/<conversation-slug>.env` into a conversation's util calls
  — the scoped store is keyed by the RUN's slug — but there was no way to PUT one there. The
  write surface shipped as `web/api_conversation_secrets.py`; this adds the CARD, by making the
  routine page's own-secrets component serve either subject (`ownSecretsCard(slug, {base,
  subject})`) and mounting it in the conversation's capabilities panel. One card over one store,
  because two cards would drift apart from the shadowing label they share.
- **A run served by a SMALLER model is told what window it now has** (F539). `capabilities.py`
  states the context window once, at boot, under the model the routine was BOUND to, and a
  fallback chain may step DOWN in window — so after a downward switch the only standing figure a
  run holds about itself is false, by as much as a factor of six, and the prompt can only be
  appended to. New `engine/substitution.py` adds a durable `ENGINE NOTE: CONTEXT WINDOW REDUCED`
  naming both models and both windows as numbers, measured against the chain HEAD (so
  200k→200k→32k is caught), latched at the smallest window announced and rebuilt on resume from
  the note's own record. Two runs paid for this: `llmsectest-weekday:20260922-060001`, dead at
  turn 106 having spent $1.03 on a run unrunnable from turn 0, and
  `weightloss:20260923-220004`, which read its own field-shifted output and wrongly concluded
  "the model cannot reliably hold the action schema".

### Changed
- **`static/components/transcript.js` is split** (F776, the operator's own answer: *"split
  transcript.js so the line fits where it belongs"*). 740 → 462, with `obs-text.js` (243),
  `obs-records.js` (129) and `obs-attachments.js` (73) carved out, all three under the bar and so
  with no ratchet entry. The documentation that could not fit is now ~25 lines in `obs-text.js`
  on the batched `ask_user` row — what the count means, where `o.batch`/`o.count`/`o.blocking`
  come from, and that "blocking" there is a filed question's CLASS and not `mode`. The size
  ratchet stands unchanged: no doc-only exemption, no redirect to a file with slack. `chat.js` is
  at its own ceiling with zero slack, so `transcript.js` re-exports `attachmentRow` and
  `revokeBlobs` and its importer is untouched.

### Fixed
- **The operator's own recorded decline is no longer reported as blocked work** (F609). One
  branch of `readmodels/surface_needs.py` emitted a `denied` secret as `BLOCKS` carrying a
  `clear_grant` fix — a proposal to undo him — and `daemon/library_watch._resolve` harvests
  exactly the `blocks` rows, re-filing a drift row on whichever commit had moved the library
  HEAD. A denied node is now a `NOTE` with no fix: the third row that reports a STATE, beside
  `action:write_recipe` "on" and `schedule:goal` "retired". The regeneration stops at its source,
  and `clear_grant` retires from all four registrations the repo holds equal. The run loses
  nothing — F756 tells it at the call it makes, which is the channel a run can act on. New
  `library_drift.reask_reason` names what changed since an answer, read off the fingerprint the
  answer was keyed on, and must be read from a snapshot taken BEFORE `_withdraw_closed`, since a
  widened reason retires its own settlement. Consequence, deliberately not special-cased: a util
  that newly declares a secret a routine has DECLINED no longer appears in the authoring
  approval's break list, because `library_impact` reads the same function ("one function, read
  forwards, twice") — widening it is one line and the operator's call.

## [0.473.1] — 2026-10-29

### Fixed
- **The nightly backup no longer fails every night after one timeout.** From 2026-10-24 no
  snapshot completed: `rsched-backup.service` was killed at its 2-hour `TimeoutStartSec` six
  nights running. On the sshfs share a run costs one round trip per file (~270k files here), so it
  had grown from 49 min (10-07) to 1h47 (10-22); the first kill left `snapshots/.in-progress`
  behind, and `backup.sh` deleted that FIRST, inside the next run's two hours — 13 to 73 minutes of
  `rm -rf` over SFTP — so every later night was killed too. `deploy/backup.sh` now carries on in
  an interrupted run's folder (`--delete --delete-excluded` make the result exactly the home) and
  keeps a failed home's folder for the next run; every delete (retention, a same-day rerun, old
  discards, now gathered in `snapshots/.discard/`) runs after the snapshot took its date and the
  stamp was written. The unit's bound is 4h — a wedge guard, not a target.
- **A killed night is reported the morning after, not on day three.** `backup.sh` stamps its
  attempt first (`.rsched-backup-started`); `daemon/backup_watch.staleness` reads an attempt that
  began after the last completion and outlived the unit (`ATTEMPT_BOUND_HOURS`, held equal to
  `TimeoutStartSec` by a test) as `stale`. So `backup_stale` and `rsched postdeploy`'s
  `backup-freshness` now see a failed night at once — 10-25's postdeploy read a 29-hour-old stamp
  as PASS while two nights had already been killed, and the first `backup_stale` came on 10-27.
- **deploy/DOCKER.md installs the backup units by `systemctl --user link`, never a copy.** The
  reference host's copied unit predated 0.374.3's `OnFailure=`, so the ntfy push for a failed
  backup never existed there and the six failed nights sent nothing.
## [0.473.0] — 2026-10-29

### Added
- **Reconnect on every connected account** (Settings → Connections). Re-consenting an OAuth
  connection — after widening `GOOGLE_OAUTH_SCOPES`, or on a `needs_reauth` flag — used to mean
  *disconnect* then *connect*, so a connect that failed left the account with no token at all: on
  2026-10-29 that happened to google:personal while its redirect was broken (an http private-IP
  `public_url` Google rejects), and the nine routines bound to it lost their token. *reconnect*
  starts the same authorize flow for the row's own `provider:account` and sends no DELETE; the
  callback overwrites that one key only once the new consent has succeeded, so the working token
  stays bound until then. A `needs_reauth` row now reads "needs re-auth — reconnect" with
  reconnect as its primary control. docs/oauth-connections.md says to reconnect, not disconnect.

## [0.472.0] — 2026-10-28

### Removed
- **The `steward-publishing` permission.** After the Steward hub's retirement (0.470.0) it had two
  holders, both publishing their OWN page or app (deficit-coach, birthday-admin): a library
  document standing in for two recipe paragraphs. Each of those routines now carries the conduct
  (where the kit's contract lives, the kit check before a publish, the three proofs) in its own
  recipe and the kit's read root in its own `fs_read_roots`; the seed and the live library copy
  are deleted. No library document declares `expects:` any more — the mechanism stays, documented
  with this permission as its past example (docs/rules-permissions.md).

## [0.471.0] — 2026-10-28

### Added
- **A hook can fire when a CHILD RUN hands back** — `child-exit` is the first of the point
  registry's four dead points to acquire a mechanism (D152 step 6). A curated rule may declare
  `moment: child-exit`; it fires in `engine/control.announce_finished_subruns` AFTER the
  hand-back message is appended, so the line reads as commentary on something the model can see,
  and it costs no turn. It carries the free payloads only — `remind` and `action`, never `hold`:
  it changes no control flow, the same line `hooks/points.ACTION_POINTS` derives and the
  operator's *"No veto ever"* ruling draws. `engine/assist_child.py` brings the first predicate,
  `child-fell-short` — a child that exited with anything but `ok`, which is the one case where
  folding its summary onward would carry a claim the child itself declined to make — declared by
  the `evidence-discipline` rule. The registry's gap list is now three (`spawn`, `run-end`,
  `restart`) and `assists.MOMENTS` is five. Pinned by `tests/test_assist_child_exit.py` (14
  tests, incl. that four children spend the once-per-run assist once). Wiring a dead point also
  reddened a pin in a file the change never opened — `test_hooks_perform.py` asserted all four
  points were still dead — which is the registry doing its job: the gap list is pinned, so
  closing a gap is an act with a test to change rather than a silent drift.
- **Every item carries a first-seen stamp, so item age is readable** (F753). `/api/items` rows
  gain `first_seen` and `first_seen_or_earlier`; the store is one small JSON map under
  `.control/` (`rsched/firstseen.py`), on the ⚑ precedent and for its documented reason — the
  self-audit routine rewrites `audit/report.json` wholesale every run, so a stamp kept there
  would be clobbered. The serve path is the sole writer and only ever `setdefault`s under a file
  lock, so a recorded date is never moved. Items that predate the store read as a FLOOR (*"14d
  old or more · since … or earlier"*), never as a fabricated date: the creating pass records an
  epoch plus the backfilled id set by NAME, because `now_iso` is second-resolution and an epoch
  comparison would read an item stamped in that same second as a sighting it never was. A report
  and a run summary read their own true filing time instead. Rendered by the new
  `static/components/item-age.js`; `static/views/messages.js` is byte-identical.
- **A proposed recipe revision can be EDITED before it is approved** (F790) — the operator's
  third control: *"we need an edit option for the proposed revisions to a routine's own recipe at
  the end of its run"*. The card offers `apply it`, `edit` (a prefilled textarea whose
  `apply edited` posts the operator's lines) and a `discard` that now asks WHICH true reason —
  *not wanted* or *I made this change myself* — and quotes that to the routine, instead of always
  telling it the revision "was not wanted". `POST /pending-creations/{pid}/revise` takes an
  optional `Edited` body, caps the lines at `recipeproposal.MAX_LINES` server-side (a cap the card
  enforces is not a cap) and refuses an emptied edit with a 400. The resumed leg is told whose
  words these are — *"THE LINES BELOW ARE HIS"* — because a leg told the operator's sentence is
  its own would implement and defend wording it never chose.

### Security
- **The console sends a Content-Security-Policy** (D219, decided in `docs/designs.md`):
  `object-src 'none'; frame-ancestors 'self'` on every http response including the 401/403
  refusals (`web/security_headers.py`, a pure-ASGI middleware added outermost). Two directives and
  no more, deliberately: a `blob:` document inherits the policy of the page that made it and the
  console renders every artifact from a blob, so a `script-src` would stop the inline scripts of
  every HTML artifact a run writes — those are safe because the frame is `sandbox="allow-scripts"`
  with an opaque origin, not because a policy stops them running. The decision's precondition was
  re-verified before shipping: nothing in `static/` or `src/` builds an `<embed>` or `<object>`,
  and a PDF artifact renders in an `<iframe>`, which `object-src` does not govern. The wider
  policy waits on artifacts being served from their own route.

### Changed
- **`util.js`'s `act()` is the shape for NEW console controls, and the ~36 hand-rolled
  disable/re-enable copies stay** (F786) — the operator's ruling, recorded in CLAUDE.md with the
  measurement behind it so nobody re-proposes the sweep: of the 36 sites, only **4** can be
  converted mechanically (the criterion being "ignores `act()`'s return value and has no
  post-success remount"); 10 both read the response and remount, 8 read it, 14 remount, and the
  rest are judgements with reasons in their own code.

### Known gaps in this release
- F790's guard that stops `apply it` staying live while the edit box is open is green-only, not
  red-first: three attempts to see that assertion fail died on the shared gate lock.
- F753's store is written only when the items index is SERVED, so a stamp can lag until someone
  opens the page; there is no `sort=age` parameter yet, and the self-audit routine's own
  `items_compact.py` still computes its age column from the rewrite date (filed as R3028).
- D219 ships no browser test asserting the header VALUE inside a loaded page, and
  `frame-ancestors 'self'` is a claim about the outside world: nothing here embeds the console
  elsewhere, but an operator framing it from another host would see it blank.

## [0.470.0] — 2026-10-28

### Changed
- **The Steward hub is retired; a run's summary and its asks replace the page** (operator,
  2026-10-28). Routines no longer publish a status page or read feedback from one. The finish
  summary now comes in TWO labelled parts — `**Process:**` (how the run went: what it read and
  checked, what failed, what was out of reach) then `**Work:**` (the task: its outcome with
  numbers, names and links, its conditions and consequences, what is still open) — asked for by
  the schema, the finish gloss and the example. The finish gloss also says where a decision goes
  instead of a page control: a BLOCKING ask (`blocks_work`) the moment the run cannot work
  without the answer, every other one in a CLOSING batch before the finish (`engine/askclass.py`).
  A conversation's reply has no parts. The dashboard, the activity feed and the routine page's
  runs table show the Work part in their one-line slot (`static/md.js` `outcomeLine`). The
  always-on contract shrank by 9 chars (`tests/prompt_budget.json`).
- The library's `steward-a-project` workflow and the `project-steward`, `personal-steward`,
  `radar` and `watcher` patterns no longer grant `steward-publishing`; the permission now covers
  only a routine's OWN page or app on the Steward host (deficit-coach, birthday-admin).
  `docs/status-pages.md` says what replaced the hub and keeps the kit reference for those two.

### Removed
- **`hub_tab`** — the routine identity field, its `HUB TAB:` harness line, its PATCH field, its
  settings-pattern row and the routine page's Hub tab control. The live routines carrying it
  were migrated in the same change.
- **The `hub_feedback` run-gate check** (`gatekit`), whose only source was the hub; the live
  checks of that kind were removed from their routines' gates in the same change.

## [0.469.0] — 2026-10-28

### Fixed
- **A conversation's deliberately chosen tool-call model survives a chat-model switch** (F784).
  The composer exists to let a conversation run a different tool-call model from its chat model
  (`static/views/conversations-new.js` seeds `tool_call` from the main picker, then overrides it
  when its own picker is set) — and the conversation header's model `apply` sent
  `models.tool_call = <main>` on EVERY save, so the first time the operator switched the chat
  model that choice was silently undone by the surface beside it. Nothing records which role was
  seeded and which was chosen, and nothing needs to: the seed makes a seeded `tool_call` equal to
  `main`, so one that DIFFERS can only have been chosen. It is now carried through untouched,
  exactly as the same handler already carries `efforts.tool_call`; a seeded role still follows
  main, and clearing main to the system default no longer drops a chosen tool role either.
  Pinned by `tests/ui/test_conversation_tool_role_kept.py` (3 browser tests; 2 of them fail
  against the previous line, the third pins the seeding that must not change).
- **Two stale comments on the machines card corrected** (same report): it claimed the
  conversation COMPOSER mounts it (the mounts are the routine page's limits section and the
  conversation header's capabilities panel), and its one explanation said "Takes effect at the
  next run" while sitting in a conversation whose own surrounding text says "applies from the
  next reply".

## [0.468.0] — 2026-10-28

### Fixed
- **The batched-ask schema offered a sub-field its own grammar refuses** (F787). `questions` is
  one item schema shared by `decide` and `ask_user` (D212) because `ACTION_SCHEMA` is flat and
  `additionalProperties: False` would strip an undeclared sub-field silently. Two of its
  properties are `decide`'s alone and `askfields._DECIDE_ONLY` refuses them for `ask_user` — but
  `name` was described as plain prose (*"snake_case id the answer is filed under"*) with no
  `decide:` lead, and `answer_type` carried no description at all. So the prompt advertised both
  unconditionally while the engine rejected them: `folder-reorg:20261027-170451` turn 48 lost a
  turn to exactly that, on D212's first live use by a routine other than its builder
  (`wrong_turn_detected` on the health stream). Both now lead with the kind that accepts them
  and say in the same breath how an `ask_user` question is identified and answered instead.
  `docs/prompt-anatomy.md` §5.1's schema dump re-rendered from `schema_for_kinds`, which is what
  `tests/test_prompt_anatomy_example.py` pins byte-for-byte (red first against this edit).

- **`tests/test_prompt_budget.py --update` destroyed the budget's own `note` while reporting
  success** (F791, found by running it). It wrote `{"total": total, **now}`, so every key it does
  not compute was silently dropped — and `note` is the only record of what the always-on contract
  was permitted to grow FOR: one `--update` erased D212's +1382 and D118's +524 justifications
  (2,818 characters of reasoning) and printed the new ceiling as if nothing had happened. The
  updater now carries every key it does not compute, the note is restored from `HEAD`, and a
  round trip is proven to preserve it. Same class as this routine's own measurement defects:
  a tool that silently narrows its own input while returning success.

### Changed
- **`CLAUDE.md` said the own-dir seal was action-layer only** (R2996, reported by
  `scheduler-builder`). It described the pre-D179 world: *"The jail cannot carve `routine.yaml`
  out of the routine's own directory — Landlock unmasks access UP the path."* `ownseal.py:46`
  has `SEALED_FILES = (CONFIG_FILE,)` and `mount_own_dir` grants read-write only to
  `writable_children`, which subtracts `sealed_names` — so the jail does carve it out, by never
  granting write to the parent. The working tier a maintainer reads first now states both layers
  and keeps the one true residual: where the jail does not engage (`sandbox: off`, no Landlock),
  `ungated_write` plus the runner's report is again the whole mechanism.

## [0.467.0] — 2026-10-27

### Fixed
- **An answer you just sent is never repainted as an open question** (F780, the second half of
  F772). Answering published a bus event, and the `/api/questions` read the repaint joined could
  have STARTED before the answer POST landed — so its list still said the question was open and
  the Decisions page painted it over the card it had just settled, building a FRESH ENABLED
  answer button under the cursor. Measured on the client-stamped UI traces: **60 doubled click
  pairs, none of them under 200 ms** — not slips of the hand, but a person seeing an un-pressed
  button and pressing it again. `static/questions-store.js` now records what THIS console
  answered and heals any snapshot whose read began before that answer, so the fix reaches every
  subscriber at once (the Decisions page, the header badge, the tier-1 notifier, the run view's
  inline forms) and `static/views/questions.js` — at its size ceiling — needed no line.
  The healing is not a lock: a read that began AFTER the answer is authoritative and drops the
  mark, so a revision, an expiry or a deletion still gets through. An INTERMEDIATE (ask-back)
  reply is deliberately not recorded — that question stays open by design. Focus could not have
  fixed this: the form disables the control it was clicked with, and a disabled button loses
  focus, so by repaint time nothing in the list holds focus. 0.461.0 had already made the
  duplicate POST harmless; what is gone now is the page offering an act that is already done.
  Pinned by `tests/ui/test_answer_not_resurrected.py` (3 browser tests: the race, the
  authority of a fresh read, the intermediate reply).

## [0.466.0] — 2026-10-27

- **A supervision rung is never dispatched over a ZERO-WIDTH window** (R2958/R2960;
  `engine/oversight.worth_a_rung`, consulted by `ladder.rung_is_due`). Two routines reported the
  same thing from different angles: an oversight rung started with a dispatch carrying no
  evidence — `since_turn: 0`, `turn: 0` — so the rung's run had nothing to judge and spent a
  whole run saying so. `rung_is_due` returned a pull-forward reason before the interval was
  consulted, and a pull-forward fires regardless of the interval, which at turn 0 means a window
  with no turns in it.
  The module's own design sentence — *"an EMPTY interval is still a dispatch"* — is RIGHT about
  the case it defends and silent about this one, so it is amended rather than overturned. A
  STALLED worker (turns elapsed, nothing to show for them) is a signal precisely BECAUSE the
  turns elapsed, and a builder returning nothing there would make the stalled run the one case
  the ladder cannot supervise. A zero-width window is not a stall: the rung is handed empty
  `says`, an empty histogram, no repetition signals and no worker answers, while its own brief
  tells it the engine's counts are the evidence.
  The fix is as narrow as the defect and bounds the PULL-FORWARD path alone. A pull-forward still
  fires whatever the interval says — that is what makes it one, and an escalation a person or a
  repeat failure asked for must never be swallowed — and it is DEFERRED rather than lost:
  `last_rung_turn` is untouched, so the first boundary with a turn in it fires on the same
  reason. The reporters' second claim, a rung-number mismatch, is NOT a defect (`rung = depth +
  1`, so a `rung: 3` event inside a rung-2 context is correct arithmetic) and no release was
  spent on it.
  **A finding the build turned up, worth more than the fix:** no module in `src/` assigns
  `loop.repeat_failure_signal` at all. `ladder.pull_forward_reason` reads it through `getattr`,
  so the `repeat_failure` pull-forward — one of the three in `PULL_FORWARD` — cannot fire in
  production today; its predicate is reachable only from a test. Recorded where it was found and
  reported to its owner, not fixed here: giving it a writer is a behaviour change of its own.

## [0.465.0] — 2026-10-27

- **One `ask_user` action can ask SEVERAL questions, and every decision record now carries its
  CLASS** (D212 increment (a); `engine/askbatch.py`, `engine/askclass.py`). The operator's
  answer, verbatim: *"Both axes: subject decides the default, the run may override it when the
  work is genuinely blocked"*. `ask_user` takes a `questions:` list — N records sharing a
  `qb-<ts>-<turn>` batch id, each filed THROUGH the existing single-question seam
  (`interact.handle_ask`), so a batch's cards are byte-identical to the ones a run has always
  filed plus the id that says they were asked together. A lone `question` takes exactly the path
  it always took, pinned byte-for-byte.
  The CLASS is `blocking` or `closing`: the SUBJECT sets the default (a typed access `request` →
  blocking, a plain question → closing) and the run may override per question with
  `blocks_work: true`, whose test is his — *without this answer the run cannot do the work it was
  fired to do*. The override goes one way only: a run cannot declare its own access request
  closing, because it asked for the entity precisely because something it meant to do needs it.
  **CLASS is orthogonal to `mode`** — the class says whether the run could do its work without
  the answer, `mode` whether its process sat and waited — and `mode: blocking` is REFUSED on a
  batch: parking a run on the first of five answers is not a thing a run could want.
  Measured over the 282 question records this instance had filed: `mode: blocking` was used 0
  times, 0% of answers arrived inside an hour (median 2.97 h), **37% of runs (60 of 163) already
  asked more than one question** — up to 6, one action and one turn each at a median 430 tokens
  of prose — and the operator already answers in batches (51% of his sittings dispose of several,
  a third across routines, median 11 minutes). The asking side was the half that did not match.
  Room was made by EXTRACTION, with every ceiling re-tightened in the same change: the shared
  `questions` schema → `engine/schema_questions.py` (actionschema 670→661), the report disposal
  grammar → `engine/reportfields.py` (actions 545→496), the D85-A newline repair →
  `engine/askprose.py` (interact was exactly on the 350 bar, now 346).
  **The cost, stated plainly: the always-on engine contract grew 1382 chars** (62,345 → 63,727 in
  `tests/prompt_budget.json`, raised by hand with its full reason in the file's own `note`, as
  that shrink-only gate prescribes and D118 precedent set). A first draft cost +2667 and was cut
  to this. A run that batches three asks saves two turns of its own context, which is far more
  than 1382 characters. Increments (b) the summary rendering and (c) the Decisions-page split are
  NOT built: the class is a recorded fact nothing reads yet, and a record filed before this
  carries no `class` key, which a reader must read as `closing`.
- **An orphaned run now says WHETHER its process tree was replaced, instead of "cause
  unrecorded"** (R2928; `daemon/containerbirth.py`). Three kills in two days closed out as
  *"orphaned: process gone at daemon boot — cause unrecorded (no deliberate-shutdown mark)"*, and
  three runs across two routines concluded the killer was unreachable from inside the container.
  It is not: pid 1's start time (`/proc/stat`'s `btime` plus `/proc/1/stat`'s `starttime`) against
  the orphaned run's last status write answers it, and on 2026-10-26 pid 1 was measured **54
  seconds YOUNGER** than the orphan rows — the whole container had been recreated under the run,
  which is also why no mark existed: nothing in that process tree performed the stop.
  So the boot reap's `unknown` splits into `container_recreated` (pid 1 younger), `daemon_died`
  (pid 1 older: the container lived and the daemon alone went, a different investigation that
  should not wear the container's verdict) and a residual `unknown` that now means /proc itself
  could not answer. The shutdown MARK still outranks the inference — a mark is a statement of
  intent by the process that performed the stop — the question is asked per orphan rather than
  once per boot, and the restart-resume stays keyed on `daemon_restart` alone, because a
  recreated container is exactly where the daemon knows too little to start anything again. No
  OOM is ever inferred here: a cgroup at 99% of `memory.max` is not a kill (on 2026-10-26 `anon`
  was 322 MB of 5.32 GB, the rest reclaimable, `oom_kill` 0), and the reap's own `classify_cause`
  owns that question from the dead engine's peak RSS. **What recreated the container — a compose
  restart policy, a healthcheck, a deploy, an auto-updater — is outside the namespace and remains
  the operator's to find.**

## [0.464.0] — 2026-10-27

Three fleet defects, each measured on the live instance before it was built.

- **`read_file` can convert a document again** (R2981/R2977, `engine/docread.py`). A PDF, docx,
  pptx or xlsx **never converted before** failed in EVERY routine directory with `[Errno 13]
  Permission denied`. The cause was neither ownership nor the util: the engine handed `doc-read`
  an `--out` path inside `.doc_cache/`, which `ownseal.SEALED_DIRS` withholds from every jailed
  subprocess (D179) — so the engine's own cache directory was invisible to the process it asked
  to fill it. DAC permitted the write; Landlock subtracted it, and Landlock has no deny rule to
  carve an exception with. Pre-creating the file engine-side does not help either: the util
  atomic-writes a temp file BESIDE its target, so it needs CREATE in the target's PARENT.
  Now the util is handed a private per-conversion **drop directory** under the system temp dir —
  the one tree `sandbox._toolchain` already mounts read-write for every util — and the **engine**
  moves the result into the cache (`_store`), where its own write was legal all along. Not
  `replace()`: `/tmp` is a tmpfs, so the rename is `EXDEV`; the bytes are copied under a dot-name
  INSIDE the cache and renamed there, which keeps what the content-hash key promises. `_prune`
  also sweeps a drop dir a killed conversion left behind. **`.doc_cache` stays sealed and no
  jail-shape line was touched.**
  It was silent for as long as it was because a cache HIT returns before the util is reached —
  only a never-converted document bit — and the suite's own end-to-end conversion test stayed
  green throughout because its `tmp_path` lives under `/tmp`, which the jail mounts wholesale.
  The new test refuses that hole explicitly (`assert tempfile.gettempdir() not in spec["rw"]`)
  and pins the destination against `ownseal.SEALED_DIRS` itself, so re-sealing or adding a sealed
  dir cannot reintroduce it. Two consequences named rather than buried: a converted document's
  text now exists briefly in a 0700 dir under `/tmp` where it never left the routine dir before,
  and the destination changed in every sandbox mode, not only the jailed one.
- **A capability refusal survives its own turn, and a repeat is answered from it** (F762,
  `engine/denialmemo.py`, hooked by one line in `completion.judge`). MEASURED:
  `vodafone-outage-claims:20261023-024513` re-emitted a write to its own `main.md` at turns 17,
  18, 19 and 34; `moltbook-heartbeat:20261023-172052` at turns 34 and 35. The refusal's wording
  was never the defect — `grantpolicy`'s text already names the right route. **Nothing of the
  refusal survived the turn**: a refused action is corrected inside the schema-retry cycle, and
  that whole micro-dialogue is dropped as debris when the turn recovers (`engine/turndebris.py`),
  so the next turn's prompt held the run's own reasoning and no refusal at all. The specimen says
  it in its own voice at turn 18 — *"I have to correct myself … I never attempted one"* — and
  then attempted it twice more. Now a first capability refusal posts ONE engine note (the
  canonical action, the turn, the permanence, the gate's text verbatim) and a later identical
  attempt comes back as `REPEAT — … ALREADY had refused at turn <n>`. Only what
  `GrantPolicy.deny` refuses qualifies: that gate answers config and ownership, which a run
  changes about neither itself nor anyone, so it is the one class where "this cannot succeed
  later" is TRUE — a schema rejection, a missing field, a file not yet written or a lock held
  elsewhere can all legitimately succeed next attempt. **The verdict is never replayed**: the
  live policy is re-asked on every call, so an `allow once` folded in mid-run makes the very next
  attempt legal.
- **The held-accept refusal is a STATE, not an error** (F761, `static/components/accept-bar.js`).
  Four of the console's eight UI `error` events on 2026-10-22/23 were not errors but D178's
  held-accept refusal, and the operator pressed `accept changes` at 03:06:27, 03:06:34 and
  03:06:43 — three times in 18 seconds. The banner already existed and said the right sentence;
  the defect was the CHANNEL, because a 409 arriving as an error toast reads as *"your click did
  not work"*. A 409 now goes to the held banner alone, which is `reveal()`d — with no toast it is
  the whole answer, and the button he presses is fixed at the foot of the viewport. 422 and
  anything else keep the error channel, because those ARE failures; a caller that renders no held
  state keeps the toast, since silence is worse than the wrong channel. Two prose surfaces whose
  own words this falsified were corrected with it.

## [0.463.0] — 2026-10-27

- **A curated rule can now DO the thing its moment calls for, not only say it** — the `action`
  payload on a rule assist (`rsched/assists.py`, `engine/assist_action.py`), which is
  `hooks/perform.py`'s first PRODUCTION caller. Until now the executable payload shipped in
  0.460.0 had only its own tests as callers: a hook could perform any action at a point, and
  nothing did, so the capability was honest in the registry and unexercised in a live run.
  A rule declares `payload: action` with an `action:` dict beside its line; when its predicate
  fires at the turn boundary, the engine performs that action through the model's own path
  (schema → workflow tools ∩ capabilities → the routing table) at NO model turn, and appends
  what came of it as an ENGINE NOTE beside the rule's own line.
  The design note spelled the payload axis `remind → scaffold → do → hold` and recorded both
  middle rungs as unbuilt "because each needs a helper channel that is not built". The channel
  exists now, so the rung does — spelled **`action`**, the name `hooks/points.PAYLOADS` already
  uses, because `tests/test_hooks_points.py` pins `points.PAYLOADS ⊇ assists.PAYLOADS` and two
  names for one concept is how two tables come to disagree. `scaffold` stays out of the
  vocabulary: the engine still cannot honour it.
  **Four bounds, three of them the operator's own rulings.** *"Any action. This includes utils
  and therefore scripts"* is what may run. *"No veto ever — a hook performs and reports; only a
  hold (prose to the model) can stop an action"* is why `MOMENT_PAYLOADS` bars `action` from
  both moments that change control flow — the same line `points.ACTION_POINTS` derives — and
  why there is no branch on success anywhere in the rung: a failed action is reported and
  changes nothing about what the run does next. `assists.STEERING_KINDS` refuses the eleven
  kinds that steer a run (finish, spawn, wait, ask_user, goal, create_routine …), the line
  `engine/commands.py` already draws for the user's own slash commands and a sharper one here,
  because an assist is authored ONCE in a library rule and fires in every routine that holds
  it. And the action can never exceed the FIRING routine: the capability gate is at fire time,
  deliberately unchecked at authoring time, because one rule is held by routines with different
  capabilities so "may this run do this" has no answer until it fires.
  Of the two free moments it carries `boundary` only, and the reason is ORDERING rather than
  policy: the observation-moment assists are asked BEFORE their observation is recorded so the
  record can name what fired on it (which a resumed leg rebuilds its guards from), so
  performing there would file the hook's own observation ahead of the one that triggered it.
- **A gate's refusal no longer reads as a success to every consumer of an observation**
  (`engine/observations.is_failure`). It checked `lint_ok` and `selftest_ok` but not
  `header_ok`, so a `write_util` the doc-standard gate REFUSED — nothing written, five
  problems listed — was classed as a successful action by the chain stopper
  (`engine/chain.py`), the exit gate, the `turn:error` reminder trigger and the new rung's own
  note, which told the run "it completed". Found by the rung's first live fire. The `*_ok`
  flags are now read together, with the specimen in the docstring, and a new one joins the
  tuple the day it is written.

## [0.462.0] — 2026-10-27

### Fixed
- **A cross-routine `config_patch` receipt named the ASKER, so a run was told the wrong
  routine was unchanged** (R2943, reported by `config-optimizer`). `patchreceipt.receipt()`
  resolved the target from `patch["routine"]` — but `engine/config_bridge.patch_target()`
  **pops** that key at ask time (so the remaining keys are a clean PATCH body the endpoint's
  `extra="forbid"` accepts), so the lookup always fell through to `match["routine"]`. Measured
  live: two patches applied on 2026-10-09 to `fau` (27 `run_gate` checks) and
  `steward-hub-maintainer` (`budgets.max_turns: 150`) were re-delivered to `config-optimizer`
  as *"config_patch NOT APPLIED — … (config-optimizer is unchanged); the operator's 'approve &
  apply' button is still available"* — wrong routine, and "unchanged" about a routine that was
  changed, with an invitation to press a button. The receipt now reads `config_target`, the
  same field the apply button already resolves against
  (`static/components/configproposal.js`), so the two surfaces can no longer disagree; a
  self-patch (no `config_target`) still names the asker, and a body that still carries
  `routine` still wins. Four tests in `tests/test_patch_receipt.py`, two red against the old
  resolution order.

## [0.461.0] — 2026-10-27

### Fixed

- **A background action that ENDS without a result told the run a raw dict instead of what it
  lost** (`engine/observations.py`, new `tests/test_background_no_result_obs.py`). D118 phase 1
  renders a background observation above the per-kind chain only when it carries `started`. The
  two background ENDS carry no result either: `background.settle`'s abandoned payload and
  `background.kill`'s cancelled one hold `kind`/`handle`/`started_turn`/`note` and no `name`, no
  `exit`. A backgrounded `script` or `util` is in `obs_floor.OWN_ERRORS`, so floor 1 declined
  them too, and `obs['name']` raised. Floor 2 then did its job — the run survived and read the
  JSON — which is why this lived for a year behind a message nobody could act on. Measured live
  (**R2970**): conversation `c-20261026-095411` lost a 25-minute background `script` to the
  container restart, and its RESUMED leg (a resume re-renders the stored observation) read
  `KeyError: 'name' at engine/observations.py:305` instead of the sentence naming the loss.
  Both ends now render their own sentence — which of the two it was, the handle, the starting
  turn and the note. `render_guard` could not have caught this class: it renders what the two
  dispatch seams RETURN, and these payloads are written straight to the transcript, through no
  seam — so the pin is four cases of its own, red-first proven against HEAD (4 assertions).
- **Answering one audit decision filed the answer twice** (`web/api_audit.py`, F772). Nothing
  in the audit-feedback path was idempotent, so one answer became two byte-identical inbox
  messages a second apart and the next self-audit run read it twice. The console race behind it
  is now named rather than guessed at: `static/views/questions.js:436` defers a repaint only
  while an `INPUT`/`TEXTAREA`/`SELECT` holds focus, never for a BUTTON, so answering publishes
  its bus event, the list repaints mid-POST, the locked form (`answerform.js:82-85`) is replaced
  by a fresh enabled one, the card still reads unanswered, and the person clicks the same option
  again. Measured over `.ui-traces/` for 2026-10-25/26: **60 adjacent same-target click pairs,
  not one under 400 ms** (so not one was a double-fire), and on the Decisions page every option
  click and every `answer` click is followed by an identical click 1.3–2.8 s later. The floor is
  the mechanism this writer already offers for the purpose — `inbox.file_message`'s
  deterministic `name` stem, as a report delivery uses so a re-delivery REPLACES its queued
  message. Keyed on the feedback's CONTENT, so a duplicate collapses while a genuinely changed
  answer still files: he re-answered all six decisions on 2026-10-25 and two answers differed,
  which a per-decision key would have hidden. The repaint race itself is **F780** — the file is
  at its size-ratchet ceiling, so it is a separate change and not a line smuggled in here.

## [0.460.0] — 2026-10-26

### Added

- **A hook can PERFORM an action, not only put prose in front of the model** (new
  `rsched/hooks/perform.py`) — the Claude-Code half of the operator's hooks ask, and hooks
  step 2 of D152. He ruled on 2026-10-26: *"Any action. This includes utils and therefore
  scripts."* He named the ACTION VOCABULARY rather than a sandbox or a command form, so this
  adds no execution path: `perform(loop, action, point=…, source=…)` runs an authored action at
  a hook point with NO model turn through the path chat slash commands have used in production
  since they shipped — `validate(ACTION_SCHEMA)` → `validate_action(allowed_kinds=…, grants=…)`
  → `actionroute.dispatch_action` (`control.run_user_command`'s core, with a hook's authorship
  instead of the user's).
  Two safety properties come free from reusing it, and both are asserted rather than documented:
  a hook can never do what the FIRING ROUTINE may not, because `validate_action` is the model's
  own capability gate; and it never reaches `executor.dispatch`, the path that skips D39's
  secret-exposure gate (a `/util` once went straight to the executor, which injects every
  REQUIRED secret a util declares, so a secret the operator had DECLINED reached a util the chat
  ran — `tests/test_hooks_perform.py` checks the module's import and call graph, not its prose).
  A performed action's observation is flagged `hook_action` with its point and its author, and
  increments `executed_actions`, so it grounds a later model finish exactly as a command does.
  Every failure — unknown point, schema, capability, a raising dispatch — becomes the returned
  observation: a hook layer live in every run must not be able to break one.
- **`hooks/points.py` gains the `action` payload, and `ACTION_POINTS` derives where it is
  allowed**: a point carries `action` iff it is NOT in `HOLDING_POINTS`. That is the operator's
  SECOND ruling of the same day, answering whether a performed action's failure may veto the
  action the run was about to take: *"No veto ever — a hook performs and reports; only a hold
  (prose to the model) can stop an action."* So a performed action never changes control flow,
  `perform` returns an observation and never a verdict, and the two points that do change what
  runs (`pre-action` prevents an action, `pre-finalize` defers a finish) keep `hold` alone —
  `HOLDING_POINTS`' by-count pin now records a decision instead of an open question. The four
  points nothing fires at yet (`spawn`, `child-exit`, `run-end`, `restart`) declare `action`
  already, so wiring one later is a site change and not a payload-column change.
  NOTE: `perform` has no production caller yet — its tests are the only ones. The payload is
  honest in the registry and still unexercised in a live run; giving it a real caller is the
  campaign's next step.

## [0.459.0] — 2026-10-26

### Fixed

- **A resumed leg is no longer told its dead children's work is lost (F778).** A run killed
  mid-flight resumed with the engine note *"these child tasks … did NOT survive the restart
  (results lost): … Re-issue any you still need"* — the opposite of the truth. Every dead child's
  work is on disk under `runs/<ts>/sub/<n>/`: its `artifacts/`, its `transcript.jsonl` and the
  `instruction.md` it was briefed with. The note sent the resumed parent to discard it; measured
  cost, 2026-10-25: two children holding ~1,400 s of build work each, recovered by hand, and the
  same kill on 2026-10-26 took a 69-turn child, also recovered by hand.
  New `engine/subsalvage.resume_note` (with `_orphan_line`, `_turns_reached`), appended by
  `engine/boot.py` where the old note was. Per dead child it now names the turn it reached (read
  from the child's own transcript; a 0 is reported as 0, never invented), the directory its work
  is in, what landed in its `artifacts/`, and — only when the file is really there — the path to
  its original brief. Then the two options that exist: READ what it left, or RE-BRIEF a fresh
  child with the original brief PLUS what the dead one produced, with the warning that re-issuing
  the bare brief is what throws the work away.
  Re-adoption is ruled out IN the note, with its reason, instead of reading as an omission: a
  child is an `EngineLoop` on a daemon THREAD inside the parent's process (`subruns._start`), so
  it dies with the leg that owned it and no process remains to pick up — the decision
  `docs/child-runs.md` § Process model records, naming "reattach-on-resume" among the costs
  deliberately not paid.
  `tests/test_subrun_resume_note.py` (9 cases) pins each claim, including that a brief absent
  from disk is never promised and that a missing child directory does not crash the boot.
  `tests/test_loop.py::test_resume_marks_orphaned_children_dead` was updated, not dropped: it
  asserted the removed sentence verbatim and now pins what the new note promises.

## [0.458.0] — 2026-10-26

### Hooks: every moment one can fire at is named in ONE table (D152 step 5)

The operator asked whether hooks are fully implemented, naming "a pre-action hook, post action
hook, pre finalize hook, and many more (get inspired by Claude code's hooks)" and that the hook
system should be the seam extending the harness² features already started. Three of those points
already fired — each at a hard-coded call site, with nothing naming the set, so the question
could only be answered by reading the turn loop and a new point cost an edit in five files.

- NEW `hooks/points.py`: twelve points, each with what it reaches, which payloads it may carry
  (`remind` / `note` / `hold`), its CARRIER (observation tail · engine note · held turn · prompt
  section), which mechanisms fire there today and the engine seam that does it. The rule layer's
  four moments and the reminder layer's eight trigger kinds both MAP onto it (`for_moment`,
  `for_trigger`), pinned against `assists.MOMENT_PAYLOADS` and `reminders.KINDS` so the registry
  is one vocabulary rather than a second opinion beside them.
- NEW `hooks/dispatch.py`: the one evaluator. It drops a candidate whose payload the point cannot
  carry (previously a silent no-op) and treats a predicate that raises as "did not fire", never
  as a failed turn. `engine/assist.py::_matching` routes through it, so the table has a real
  reader instead of being a description beside the code.
- The points NOTHING fires at yet are in the table with an empty `fires` — `spawn`,
  `child-exit`, `run-end`, `restart` — because a registry listing only what is built cannot say
  what is missing.
- **A correction the table forced out:** the hook design's rule 2 said `hold_before_action` is
  the only delivery that can change what runs. TWO points can — `pre-action` prevents an action
  and `pre-finalize` DEFERS A FINISH, so the run takes a turn it would not have taken (that rung
  was built after the rule was written). Pinned by count, so a third is a decision, not a drift.
- `run` — EXECUTE something at a point — is deliberately absent from the payload vocabulary,
  with a test pinning the absence. The operator has since ruled ("Any action. This includes utils
  and therefore scripts"), and that is the next step, built on the path `engine/commands.py`
  already proves: an action at a turn boundary, through `validate_action` → `actionroute`,
  costing no model turn.

### A run-tree hook's sender is told what happened (R2924)

`runtree.send` answers with a HOOK's shape — `runtree`/`delivered`/`hook`/`addressee`, never the
`filed` + `R<n>` of a ledger row — and the report renderer had no branch for it, so every
run-tree send fell through to the branch's last line: *"could NOT write the reports log (I/O
error) — the report was not filed"*. Measured: a child of `scheduler-builder` believed that line,
re-sent, and BOTH copies arrived in its parent's prompt; every refusal `runtree.resolve` writes
(unknown label, exited child, sibling, top-level run addressing `parent`) was swallowed the same
way. `obs_admin._runtree_line` renders it first, before any branch assuming a ledger row — and a
GENUINE write failure still says the hook was not delivered, pinned by its own test, because
"never report a failure" would be the same defect with its sign flipped.

### The run controls stop hand-rolling `act()` (F779)

`static/components/runsupervise.js` wrote `disabled = true; try { await api() } catch {};
disabled = false` by hand at both buttons, and `static/util.js:337` documents the shared helper
plus the two failures the hand copies cause: 👁 supervise re-enabled on both paths only because
nothing between the await and the re-enable threw, and ⟲ rewind re-enabled only in its `catch`.
Both run through `act()` now — the re-enable rides a `finally` and focus returns to the button a
disable could not let it hold. The two new browser tests are RED against the hand-rolled version.
Measured and NOT swept: 38 more hand copies of the shape remain across `static/`.

### Background mutation ordering, and the feature made reachable (D202 = D118 phase 3)

His decision: *"Widen BACKGROUNDABLE_KINDS to mutations behind a dependency / explicit-barrier
model"*. Four defects fixed, each with a red-first test:

- **the feature was unreachable by any model** — `BACKGROUNDABLE_KINDS` had been widened while
  the model-facing `background` description still read "NOT available on a WRITING KIND", so the
  engine accepted a deferred write every run's own schema told it to refuse;
- **the barrier crashed every dispatch without a loop** — `_claims()` wrote an attribute from the
  READ path, and the barrier runs for every action, so an ordinary `util` call became an
  `engine_error`;
- **one file reached by two spellings was two targets** — the STALE READ the model is promised is
  impossible; targets now resolve against `run_context.work_dir`, a memory topic keeping its own
  namespace;
- **a `then_script` riding a deferred write was silently dropped** with D39's call-time secret
  gate never asked (F633's shape) — refused in the schema-retry cycle now.

NEW `engine/schema_background.py` holds the field spec so its description could grow;
`engine/actionschema.py` 685 → 670 lines with its ceiling TIGHTENED, no ceiling raised.
`tests/prompt_budget.json` was raised by hand 61821 → 62345 (+524, all in `schema`) — the only
place a run learns which kinds may be deferred and what the ordering promise is — and that raise
is **not funded by a cut elsewhere**; the next cut should lock itself in with `--update`.
`docs/designs.md`'s phase-3 entry is deleted, which closes the item.

### The between-runs prompt-cache gap, measured (D163 increment 1 = F507)

His answer B: *"per-run cost: extend what exists … starting with F507's between-runs prompt-cache
gap, which is the one part nothing measures today"*. **462 of 686 retained fires across 31
routines started COLD, re-writing 25,217,105 prompt tokens for $124.58 above a cache read**
($0.2697 per cold fire; top: `voice-model-trainer` $14.14, `self-audit` $10.71,
`scheduler-builder` $10.58). The salvaged save point's number was 22% too low because two silent
DROPS took fires out of the view entirely rather than into an unknown bucket:

- `HEAD_LINES = 8` missed turn 1's usage row in 77 of 676 metered runs (11.4%), as far as line
  34, pushed there by pre-turn-1 records — biased exactly the wrong way, since the runs pushed
  furthest down are the inbox-heavy, expensive ones. Now 200, with the measurement in the comment.
- requiring a `model` dropped 6 more real fires whose turn-1 row carries `cache_write` but no
  model; metered now means "carries a token key", and such a fire counts as `unpriced`.
- a fourth state, **`undetermined`**, is counted INSIDE `fires`, so a cold share can never be
  computed over only the fires the reader managed to read.

Warm fires re-wrote 8.05 M tokens whose premium is deliberately NOT priced (a usage row cannot
separate lost prefix from new content); the quantity is carried and named on the page instead of
estimated. `warm_read_share` is 0.3216 over all 211 warm fires.

## [0.457.0] — 2026-10-26

### An answered decision stays answered across a DST change, and eight stale-prose defects from the deep sweep

**F771 — the operator answered six decisions twice in one night, and a string compare of two
timestamps is why.** `web/decisions_read.py` and `web/api_audit.py` both decided "has he already
answered this?" with `marker >= generated` on two ISO stamps **as text**. Europe/Berlin fell back
03:00 CEST → 02:00 CET on 2026-10-25: the report's `generated` was `02:40+02:00` (00:40 UTC) and
his answers were stamped `02:07..02:17+01:00` (01:07..01:17 UTC) — **27 to 38 minutes later in
real time, every one sorting earlier as text**. Measured 6 of 6. After the next daemon boot all
six cards re-presented as open and he answered them again within five minutes; **two came back
with different wording**, so work had already been closed against a superseded answer.

- **The fix was already in the tree and neither site used it.** `readmodels/stamps.instant()`
  exists for exactly this — its own docstring names *"stamps either side of a DST change ordered
  backwards"* as the reason. New `stamps.at_or_after()` joins it and both gates route through it.
- **A stamp that names no moment now makes NO claim.** The first draft of the helper fell back to
  the text compare, and its own test caught what that means: `'zzz' >= '2026-…'` is True, so a
  corrupt marker would read as *answered* and hide a decision from the operator for good. The two
  failure modes are not symmetric — re-asking costs a click he can see.
- Red-first on his real stamps: `test_an_answer_across_a_dst_change_is_not_re_asked` failed
  `D210 was re-asked after he answered it` before the fix.

**The deep sweep's stale prose, eight items** (read-only child, `artifacts/deep-sweep-20261025.md`):

- **`daemon/pause.py` — the document the file itself calls "the CONTRACT" was wrong about a safety
  property.** It said the reap's *three* automatic legs are held; `holds_automatic_leg` has **two**
  callers and `tests/test_global_pause.py` says the sigkill retry is *deliberately* NOT held. A
  reader auditing "does a paused instance start engines?" was told no by the one document
  designated as the answer. The retry now sits in the exemption list with the test's own reason.
- **`daemon/runner_state.py`** attributed the process group's SIGKILL backstop to `KILL_GRACE_S`
  (this module's own 10 s daemon→engine wait) instead of `procgroup.TERM_GRACE_S + 1` (31 s) —
  contradicting its own module six lines above.
- **`migrate_total_turns.py`** hand-rolled a third `subprocess.run(["git", …])` inside the package,
  getting none of `libgit.git`'s process group, `GIT_OPTIONAL_LOCKS=0` or `procgroup.terminate`
  treatment — the plumbing F285 centralised. It already imported `libgit`.
- **`reminder-census` is deleted, not maintained.** The operator removed it from the library on
  2026-10-05 and `bootstrap.sync_seed_utils` never resurrects a deleted util — yet the repo still
  shipped its 332-line seed, a test and a boot-migration row that would have reported
  `not read: [Errno 2]` forever. Seed, test and row removed; `docs/designs.md` corrected from
  "all four utils" to three. (The lockstep worth keeping — `transcript.js` ↔ Python `BRIEF_FIELD` —
  is held independently by `tests/test_actions.py`.)
- **The `own_fields` authority label sold 4 knobs where `configdial.OWN_FIELDS` declares 6** — the
  one surface where the omission costs the operator a decision.
- `engine/actionschema.py` documented a `util:codemap` that has never existed (the map comes from
  `code-search map`), and claimed six prior derivations while enumerating five.
- `hooks/reminderproj.py` now says in one line that it is called from the tests alone — which is
  its design, not neglect, and what the codemap's orphan flag keeps finding.

**Not shipped, named instead:** `static/components/transcript.js`'s sub-run fetch swallows its
error with the comment *"the next poll retries"*, while the poller is armed only for a LIVE run —
so on a finished run the fold goes silently blank. The fix needs added lines in a 740-line file
and the size ratchet reds on any growth over the bar, so it is a finding rather than a quiet
widening of the ceiling.

## [0.456.0] — 2026-10-25

### The language a run writes in is stated, once (F544, decided as D214)

The engine said what `say`, the finish summary, a question and a note are FOR, and never what
language they are IN. F544 asked for that sentence; D214 is the operator's answer — *"English
only, just like the rest of the conversation / routine run"*.

- **The harness contract now states it** (`engine/harness.py`), in the paragraph that already
  owns the prose channel, beside the say contract — not restated per field. `actionschema.py`'s
  own comment says why: a cross-cutting property worded twice ships a contradiction, which is
  how "ONE terse clause" and "2-3 sentences" once reached one prompt together. One sentence, one
  site, every kind covered.
- **It draws the distinction that keeps it true.** The run's OWN prose is English — the narration,
  the summary, questions, notes, reports — because every run is read beside every other run's and
  one language keeps them comparable. What a run PRODUCES takes the language its reader needs: an
  email to a German counterparty is German, a translated document is in its target language. A
  flat "English only" would have forbidden deliverables this instance exists to send.
- **The author-side half shipped with it**, which was the operator's condition on this item —
  *"a rule for authors and a prompt-surface statement that disagree are worse than neither"*. The
  shared library's new `prose-language` rule carries the same two-part statement for recipe
  authors, so the two surfaces cannot drift apart into a contradiction.
- **It was paid for by a cut, not by raising a ceiling.** `tests/test_prompt_budget.py` holds the
  always-on engine contract to a shrink-only ratchet with zero slack — 0.454.0's Harness²
  discipline, *"if you add always-on text, cut something else to pay for it"*, because every word
  is re-sent on every turn of every run. The harness RESTATED the whole `role_swap` field
  description; it now points at it the way the same paragraph already points at `note`'s. No
  guidance is lost — it is in the same prompt, one screen down. The engine's always-on contract is
  **61821 chars, 57 smaller than before this item**, and `prompt_budget.json` is re-locked by the
  repo's own `--update` so the cut cannot silently re-grow.
- `docs/prompt-anatomy.md` is deliberately NOT edited: §5.1 is a rendered artefact (the schema dump
  inside it is pinned byte-for-byte against the composer's own projection), re-rendered from the
  composer and never hand-patched. A hand edit there broke the fence's JSON in this very item.

Supersedes the misleading record on 0.401.0: that release named F544 in its commit subject and in
its CHANGELOG header while shipping only R2249's vision-refusal bug, which is why this row stayed
`in_progress` for 15 days. Its `shipped.jsonl` row has an empty `done` list and is correct; the
commit subject was not. Fold the changelog from `shipped.jsonl`, never from commit subjects.

## [0.455.0] — 2026-10-25

### Six decided items: a decline withholds, a rejected turn is taught, settings are judged at the writer, a run proposes its own recipe, the run-history depth is a real boundary, and an answered decision keeps its card

Six operator-decided items, each built in its own worktree and gated before merge.

**F756 + D213 — a per-secret DECLINE withholds that credential; it does not refuse the util.**
Measured live by `llmsectest-weekday:20261023-040002` and called a bug by the operator: he granted
the `GMAIL_*` secrets to `gu mail` and declined the `FAU_*` ones, after which the engine refused
EVERY call of that util — including `--account default`, which needs no FAU credential. A decline
protects the credential, not the util: the name is now scrubbed from the child env exactly as a
`?`-optional one is and the call proceeds (`engine/secretgate.Withheld`,
`withheld_for_call`/`withheld_for_util`), the observation renders `withheld_declined` as a COUNT
with the repair route (R17 — a denial enumerates nothing), and the exposure ask's `default` no
longer promises the util is not run. Forbidding a util is a `util:` grant or a reservation; a
secret row is not that lever. D213 is the operator's ruling on the asymmetry this exposed:
*the engine refuses for calls IT chose to make and withholds for calls the RUN chose* —
`mediaops.vision_describe` (the `view_image` fallback) still refuses, because the run did not
choose that call and a credential-less attempt would fail inside the util about a call the run
never wrote. Written into both code sites plus `docs/sandboxing.md`,
`docs/rules-permissions.md` and `docs/prompt-anatomy.md`, and pinned by a test over one declined
row so neither site can be "made consistent" alone. The undecided paths do not move.

**F736 — a schema-rejected turn is told WHICH field, and an identical repeat is refused once.**
`wrong_turn_detected` rows showed runs re-sending one rejection on consecutive turns;
`llmsectest-weekday:20261023-040002` earned `'say' is a required property` eleven times.
New `engine/schemamemo.py`: `field_help` quotes the ACTION SCHEMA's own sentence about the field
a problem line names, and `recall` says the rejection was already earned and at which turn, with
the provider grammar dropped for that retry. It refuses the identical ASK, never the run — that
specimen recovered from all eleven, so ending the run at the second would have discarded 396
turns of finished work. Known limit: the memo is per-process, so a resumed leg starts empty.

**F610 — proposed settings are judged by the WRITER, not at the operator's accept click.**
Four writers stored unvalidated vocabulary (`patterns/drafts.write`, two `web/api_settings.py`
routes, and `patterns/store.problems`, the creation-time pattern writer), proven red-first. The
judge moved into a new `patterns/validate.py` and is re-exported from `recommend.py`, so no
second copy of the vocabulary exists; a bad value is refused at write time with the key and its
allowed values named, and the web routes answer 422. Known limit: the 13 `GAP —` fields
(`capabilities`, `deliberation`, `schedule`, `run_gate`, the fs roots) are still judged only on
the accept route.

**D200 — every finishing run is asked for its own recipe revisions.** The operator's requirement:
*"In the future I want recipes to suggest a change in their recipes themselves when they finish"*,
housed in the engine finish seam, modelled on D195's known-flaw prompt. The ask lives in the
digest's FINISH block (`engine/recipeproposal.py`) rather than the harness, whose budget is
exact-equality shrink-only; the lift is in `engine/loopend.py`, the close-out every ending shares,
so even a budget-spent reserved finish turn proposes. `RECIPE REVISION:` lines land as one
`recipe-revision` card; approving arms the existing revise unlock and resumes the proposing run
with its recipe writable, because a scheduled run holds no recipe-writing capability. Two flaws
named: no repo command re-renders §5.1's prose fence (hand-edited from the composer's own emitted
string, with a drift test but no byte comparison), and the ask adds ~620 characters to every run's
digest, which the shrink-only ratchet does not measure.

**D207 — the run-history depth cap is a real boundary, enforced where the jail is composed.**
`read_file` refused an older `runs/<ts>/gate.json` while `gu json get <the same path>` returned it.
The sweep ran first, as the operator asked: 9 path-shaped prior-run reach sites across 7 routines,
and the boundary would newly refuse **0** of them. The bypass was `ownseal.mount_own_dir`'s single
`ro.append` granting read beneath the whole routine dir; Landlock has no deny, so the routine dir
is now LIST-ONLY (`landlock.ACCESS_LS`) with its other children mounted `ro` individually and,
under `runs/`, only the permitted depth plus the current run. New `rundepth.py` is the one
predicate both enforcers compile from. Both doors are covered — the carve lives inside
`mount_own_dir`, and the daemon's admission jails fall to a closed `last` floor (their own test
was red until fixed, which a one-door fix would have missed). Kernel-proven: a jailed `cat` of an
older run's `status.json` gets EACCES while `ls runs/` still lists every id. Named shortfall: a
granted root ABOVE the routine dir (`~/routines`, held by 7 routines) is one Landlock hierarchy
and is not carved, so for those routines the boundary is advisory — and the run is told so via
`run_depth.not_enforced_under`.

**F529 — freight that arrived mid-run is named at the turn boundary, and an answered decision
keeps its card.** The operator answered the same decisions twice (*"i answered your questions. for
the second time btw"*) because a run reads audit feedback once, at boot, so an answer arriving at
turn 150 could not reach the list built at turn 5. New `engine/freightnote.py` names such a file
at every turn boundary — "An answer sitting in inbox/ is ANSWERED … do not ask him again" —
consuming nothing, so the next fresh run still gets everything. And `web/decisions_read.py` no
longer drops an answered audit decision: it keeps a settled card with his own words, where before
his card simply vanished with no receipt — the only decision shape that silently emptied.

## [0.454.0] — 2026-10-25

### Harness² adopted where it fits: a change is judged by its moment, the order is the one stated, the always-on contract has a budget

The operator asked for an analysis of Harness² (arXiv 2610.14224, google-research/harness2)
against this system's "the workflow is the harness" approach, then: *"or you implement the
improvements yourself. now"*. Measured on the live fleet's last 442 depth-0 runs first:

- runs follow a stable stage path (87% monotone, pairwise concordance 0.99 against the order runs
  actually take), but the ENGINE's declared order was wrong for 10 of 29 routines;
- stage coverage tracks nothing (coverage vs Done-when unmet share r = −0.008);
- the engine's own always-on contract is 60-85% of a run's first prompt, the routine's recipe a
  median 5%;
- 96% of recipe changes end `too few runs` — at about one recipe change per two runs, the rate
  verdict can almost never be drawn;
- every routine's claim verifier resolved to the model it judges.

**The moment test** (`engine/frictions.py`, `readmodels/change_frictions.py`). Every run now
records `quality.frictions`: its failures by a signature every routine names the same way
(`schema`, `script:`/`util:<name>`, `edit_miss`, `read_miss`, `memory_cap`, `refused:<kind>`,
`deferral:<rung>`, `budget:<resource>`, `unmet:d<n>`), counted from its own transcript at its
end; the reserved-turn injection carries a `budget` key so the resource is on the record. Each
change then carries Harness²'s compose criterion beside its rate verdict: a signature in ≥2 runs
before it is the harness's, and after it it is `fixed` or `persists`; a new one is `introduced`.
Two runs a side suffice; a window that has to reach across a neighbouring change names it
`confounded`; a recipe commit's `Addresses: <signature>` line is judged as its aim (`fixed`,
`persists`, or `unfounded` when the runs before never repeated it). A run with no recorded
frictions is unknown, never clean. A one-shot boot backfill (`migrate_frictions.py`) counts the
runs retention kept. The Changes view gets a failures column and a moment table, and a change the
moment test judged is no longer folded away as "came faster than it ran".

**The stage order main.md STATES** (`statemap.declared_order`): its numbered Run flow list or its
arrow chain, not the first place prose names a stage. On the same 436 runs the declared first
stage is the one runs start with in 390 (was 273); no routine's pair order got worse.
`change_signals.COUNTED_SINCE` gains `stages_skipped: 0.366.10` (F563 changed that ruler), and
two docs stopped describing behaviour F620 removed.

**The engine contract's budget** (`tests/test_prompt_budget.py`). `composer.system_sections`
names the prompt's sections (the prompt is byte-identical), every run records their sizes as
`quality.prompt_chars`, and the harness + schema + example — 61 878 chars, the same for every
routine, re-sent every turn — are held to a shrink-only ceiling: growth fails until something
else is cut or the ceiling is raised by hand with a reason.

**`NOTE verifier`** (`rsched/verifier_model.py`): `rsched validate` names a routine whose
`tool_call` role resolves to its `main` model's provider model — all 32 today. Which model judges
which stays the operator's choice; the verifier docs no longer promise a second model.

Outside this repo, routine-improver's recipe now runs separate MECHANICS (a chronic signature,
fenced from domain content) and DOMAIN passes, names what each commit `Addresses:`, reads the
moment test of its own last edit first, and keeps `state/experience.md` (cue · lesson ·
evidence) for proven fixes; its `scripts/outcome_review.py` reports `mechanics`.

## [0.453.0] — 2026-10-25

### A decision answered mid-run records WHAT was decided, not just that it was

The operator answered six decisions (D210-D215) on the Decisions page between 02:07 and 02:17.
self-audit's report, generated at **02:40 — after all six**, still listed every one
`status: "open"` with an empty `resolution`, because only its own next run writes that field. So
every surface reading the report alone showed six answered decisions as awaiting him, and a
routine reported them back to him as outstanding. His words: *"there is no unanswered question on
the decisions page. file dis discrepancy as a bug and fix it"*, then *"also make the report record
a resolution for an answer that arrived mid-run"*.

Measured before anything was built: `/api/questions` serves 286 rows with **zero** unanswered, so
the Decisions page was right — the whole discrepancy was on the reading side. Answering writes two
durable records (`web/api_audit.write_feedback`, under a lock): the marker in
`audit/decisions-answered.json`, and the queued `[AUDIT decision · D21x] selected: …` message
carrying his own words.

The report file belongs to self-audit, so the engine fixes the read model every surface shares.
`readmodels/items._report_item` now fills an empty `resolution` from his queued answer's words —
falling back to `answered <ts>` once a mid-run delivery has consumed the message, which says the
answer exists without inventing words nobody wrote. A resolution the report *did* write always
wins: that is self-audit's considered wording. `source_paths` now fingerprints the audit inbox,
so his answer invalidates the memo rather than waiting for some other source to change. New
`readmodels/decision_answers.py` holds the reader, because putting it in `items.py` crossed the
350-line house bar.

The item's **`status` is deliberately untouched.** A first build also let the answered marker
flip `open` → `settled`; the release gate reddened on
`test_api.py::test_items_page_reflects_answered_decision_after_consumption`, which pins that an
item's status is the REPORT's (self-audit's judgement, where `settled` means the item is dealt
with) while the answer is an overlay the pages apply from `web/api_audit.answered_decisions`. The
live case agrees: D213 was answered and the work it authorizes (F756) is still unshipped, so
`settled` would misreport it. What was missing was never the status — only the words behind it.

## [0.452.0] — 2026-10-25

### A routine's own `scripts/` can run — the venv goes where the jail can create it

Three reports had one cause (R2887/R2891/R2892, folded as R2907): `uv` put the venv at
`<routine>/.venv`, and a sandboxed subprocess can never create a new top-level entry in a
routine dir — D179's own-dir seal mounts the dir read-only and only its EXISTING unsealed
children read-write, and a Landlock rule needs an inode, so there is nothing to grant. It was
never a missing grant, and widening one would not have fixed it.

A script whose PEP 723 block declares **no dependencies** now runs on the daemon's interpreter
and builds nothing, the precedent `daemon/gate_prepare.py` already set for a dep-free
`admit.py`; one that declares dependencies gets its venv at `<routine>/scripts/.venv`, inside an
existing unsealed child the jail does mount writable. That alone fixes
`scheduler-improvement-research`, whose `scripts/outcome.py` declares `dependencies = []` and
had **never run once** though its recipe requires it every run; run through the fixed runner it
returns `exit 0, measures={proposals_made: 66, proposals_accepted: 20, handed_to_builder: 12}`.
The placement was measured rather than assumed — in a strict jail with own_dir only and `/tmp`
forced read-only, `mkdir <routine>/.venv` reproduces R2892's Errno 13 and
`mkdir <routine>/scripts/.venv` succeeds, after which a real `uv venv` + `uv pip install` builds
and runs there (`tests/test_script_venv.py`). New `src/rsched/scriptenv.py`, because `scripts.py`
sat at 349 of its 350-line ceiling. *Not* proven: the conversation surface's half rests on the
code path plus the kernel test, not on a reproduction inside a live conversation.

### A person can ask a LIVE run for a supervisory rung

The operator pressed run-now with the brief "rung" twice on 2026-10-16 while `fau` was live and
got "routine fau is busy" both times — the moment you most want to look at a run is the moment
that door is shut (F750). The busy rule is **not** exempted: `Runner.active` is keyed by slug,
so a second concurrent run of one routine has nowhere to live and would race its state. Instead
the ask is a request **on the run**, which is how the escalation ladder already supervises a live
run safely: in-process at the worker's own turn boundary, as an oversight child whose turns are
its own. `POST /api/runs/{run_id}/supervise` (the run view's `👁 supervise`, live runs only)
records it in `control.json`; `engine.switches.person_asked` reads it as the sixth control.json
signal, reusing the applied-ts edge trigger so one ask is one rung; `ladder.at_boundary` reads it
**before** the knob, because `ladder.enabled` is false by default and gating the ask on it would
shut the door on exactly the run being watched. The rung reports `person_requested`, so a reader
can tell it from one the mechanism produced. The hard skip is untouched. The rung's turn-cap
arithmetic moved to `oversight.py` (the ladder's declared pure half) and the rewind control to
`static/components/runsupervise.js`, since `ladder.py` and `run.js` both sat at their ceilings.

### A whole-model-chain failure reaches the blocked-work fold

`model_chain_exhausted` — every endpoint in the chain refusing, the host effectively mute — was
emitted and then read by nobody: the only signal was one routine's failed run page (F716). It is
in `BLOCKED_EVENTS` now, so `/api/health/blocked` folds it like the seventeen events beside it.
Two prose counts that had already drifted follow the set again: `docs/architecture.md` said "the
eight events" while naming ten of eighteen, and `docs/run-analytics.md` said "Ten".

## [0.451.0] — 2026-10-25

### `main` was red on a date, not on a defect — a shared stream is not an exact-equality payload

Every release on this host was blocked this morning by a test no builder had touched.
`tests/test_lanes.py::test_a_boot_rescan_that_raises_does_not_stop_the_scheduler` asserted the
WHOLE health stream equals `["scheduler_tick_error"]`, but it drives a real `Scheduler` loop — and
a real loop runs the standing-condition watches, which file into the same stream.
`daemon/marker_watch.py` files `migration_due_soon` the moment any live `MIGRATION(expires=)`
enters its 21-day window, and `migrate_seed_utils.py` / `migrate_enabled.py` declare
`expires=2026-11-15`: 21 days before that is **2026-10-25**.

Deleting the two converged migrations would not have fixed it — the live markers expire 11-15,
11-30, 12-15, 12-31 and 2027-01-15, so their warn windows overlap almost continuously into next
year and the red would merely have moved to 2026-11-09. The assertion now filters to the event it
is about (`helpers.health_events(home, event=…)`, as the sibling assertions in
`tests/test_scheduler.py` already do) and keeps both halves of its intent: filed exactly once, and
the detail still names the boot rescan.

### The double-conformance gate detects a dataclass stand-in by SHAPE, not by name (F766)

R2883's detector matched impersonated classes by NAME and `Completion` — the most impersonated
class in the suite — was not on the list; the hole reddened a release the same day the detector
shipped. A fifth literal entry would have been **vacuous**: every `Completion` stand-in is
`def complete(...): return SimpleNamespace(...)`, never a variable named for the class, so a
name-keyed entry would have added zero detections. The shipped rule is general — a
`SimpleNamespace` whose keywords are all fields of exactly ONE dataclass under `endpoints/` or
`engine/`, supplying every field it requires, is the violation, because `D(**kwargs)` would have
worked. Measured: **441 namespace sites → 4 hits, all `Completion`, 0 false**; widened to all 48
dataclasses it gives 12 hits of which 8 are false, every one from a dataclass with no required
field, which is what the scope exclusions hold out. The predicate found a **fourth** latent site
the report's own grep missed. New `tests/double_survey.py` is the reader both rules share.

### One predicate answers "can anything drain this row?" on both surfaces (F768)

`readmodels/orphans.find_undelivered` decided a report's target existed by probing
`<home>/<slug>/` on disk, while `registry.scan` — the authority every other surface uses — keys
the fleet by the slug declared **inside** `routine.yaml`. The two disagreed for an archived
routine directory and for any routine whose directory name differs from its slug, which is the
exact class of defect F738 was built to end. Both surfaces now take the same installed set, the
per-row field says `target_installed`, and the messages page's banner reads it.

### An explicit null clears an endpoint's inline key, and stripping the last credential is refused (D208)

`PUT /api/settings/endpoints/{name}` already existed, so the work was the semantics: `api_key`
now distinguishes three states, read through `model_fields_set` rather than a dump — absent and
blank both leave the stored key alone (the console's own submit stays non-destructive), while an
explicit `null` clears it. `model_dump()` cannot tell absent from blank and `exclude_none=True`
eats the null outright, which is the same trap that made `{"ladder": {"max_depth": null}}`
unfireable in F759. A save is refused when the endpoint would be left with no credential at all
AND this save changed the credential triple: reading the RESULT keeps `anthropic metered` and
every keyless Ollama/vLLM endpoint savable, so only an actual clearing is stopped, and the
refusal raises inside the config lock so nothing is written. The key-source logic moved to a new
`web/settings/endpoint_keys.py`, taking `endpoints.py` from 346 to 339 lines rather than raising
its ceiling. The console has no "remove key" control yet — the clear is API-only.

### A report whose words claim a disposal its fields do not carry is told so on filing (F770)

A report can say "this closes R2841" in its prose and close nothing, because disposal travels
through `answers` / `settles` / `supersedes` — and no surface noticed the difference. The filing
observation now names the ids the text claims but the fields do not carry, beside the existing
truncation note, so the author reads it on the turn they filed it.

## [0.450.2] — 2026-10-25

### The config dial pointed every reader at a module that has never existed

`configdial.py`'s own docstring told whoever came looking for the write-side boundary that it
lives in `web/patch_authority.guard_autonomous`. **There is no `patch_authority` module anywhere
in the tree** — `find . -name "patch_authority*"` returns nothing, and `guard_autonomous` appears
in exactly one place: that sentence. The real boundary is one import away (`autoapply` holds the
single reading of `configdial.refusals` before the applier writes, as `autoapply.py:27` says),
and the single writer both doors go through is `web/api_routine_patch.apply_updates`. The line
now names those, and says what it used to claim so the correction is legible.

Found by re-verifying a five-run-old deep-sweep premise against today's tree rather than filing
it from the sweep's own notes: of its seven checkable claims, four were already fixed or had
moved file (`actionschema`'s "a run can never edit its own config" is gone, `background.py`'s
docstring now matches its own `STOPPABLE_KINDS`, `detached.py` moved to `daemon/` and its
`_STRIP_ACTIONS` does list the memory kinds) and this one was still true verbatim.

## [0.450.1] — 2026-10-25

### An answer filed after a run consumed the first one reaches the next run (F763)

**Eight operator answers were sitting unread across the fleet, the oldest 41.6 days and 15 runs
old.** A pending question record is unlinked the moment a run consumes its answer
(`engine/inbox_questions.py:117`). An answer filed AFTER that — the operator answering the same
card again, or the Decisions page serving a record a boot consumed seconds earlier — had no
pending record left to match, so `collect_deferred_answers` skipped it with *"the answer belongs
to someone else … Leave it alone"* and **no run would ever read it**. The route that files the
answer (`web/api_questions._file_answer`) writes the file unconditionally and returns `ok`, so
nothing on any surface said the answer had gone nowhere.

The specimen that shows it is not theoretical, in `config-optimizer`:
`questions/answered/q-20261009-020522-41.json` carries `consumed: 2026-10-09T04:41:29+02:00`
with the answer *"approved & applied the proposed config change"*, while
`inbox/answer-q-20261009-020522-41.json` is stamped **04:41:37 — eight seconds later** — and
says *"Apply — add the 7-day heartbeat"*. He answered generically, a boot took it, he then
clicked the actual option, and the specific answer has been unreadable for 15.8 days. Same for
`-42` (*"Apply — raise max_turns to 150"*). The oldest, `q-20260911-032048-121`, has no archive
either, so it is invisible on the Decisions page as well.

`collect_deferred_answers` now reconstructs the ASK from the durable archive
(`_reanswered`) when the pending record is gone, which is the same source and the same field
filter `revise_answer` has used since F525 — so a later answer is delivered, archived over the
original row (keeping its `first_consumed`) and marked `revised`. Fixed in the CONSUMER rather
than in the web route on purpose: the loss happens however the file came to be written.

The control is pinned too — an answer with neither a pending record NOR an archived ask is
still left alone, because that file belongs to a blocking ask whose record lives in the run's
`status.json`, and taking it here would steal the answer a parked run is waiting for.

### `mock_proxy` is re-entrant, so a second call re-aims the mock instead of wrapping it (F755)

`tests/helpers.py::mock_proxy` read `httpx.Client` off the module it had already patched, so a
second call in one test wrapped the first mock and passed `transport=` twice. The resulting
`TypeError` was caught by `cliproxy_login`'s own `except (httpx.HTTPError, ValueError,
TypeError)` and surfaced as **"Could not reach the proxy's management API"** — a sentence about
the network standing in for a bug in the test. Two test files carry comments warning authors off
the second call (`test_cliproxy_login.py:230`, `test_endpoint_watch.py:247`) instead of it simply
working. The real class is now captured once at import.

## [0.450.0] — 2026-10-24

### A resumed run no longer loses the children it started (F732)

Filed at the operator's own request on 2026-10-19 ("maybe we can do sth to at least be able to
salvage some of the data in the future"), after a run of this builder spawned four children, was
interrupted and resumed, and the next `subruns` call answered **"no sub-workflows spawned this
run"** over seven child directories — two of them holding a finished hand-back.

`SubrunManager.subruns` is in-memory (threads and completion events cannot be rebuilt), so a
resumed leg starts with an empty registry while every `sub/<n>/` directory is still on disk. New
`engine/subsalvage.py` owns the row shape for **both** sources, because a shape living in two
modules drifts: a salvaged row would quietly stop carrying a field the live ones gained. A
directory with no live child becomes a row in state `orphaned-by-resume` naming its path and what
landed in its `artifacts/`; `wait` and `kill` on that number refuse **with that reason** instead of
"no sub-workflow N", which read as "it never existed" and sent a run off to redo finished work.
The empty sentence now scopes itself to THIS leg.

Salvage, not recovery, deliberately: the thread died with its leg, so nothing will be announced or
collected and the directory is the deliverable. Nothing new is written at spawn — the directories
already outlive the leg (`childrun._highest_child_number` relies on exactly that for numbering), so
a second bookkeeping file could only drift from the truth. The extraction takes `subruns.py` from
359 to 349 lines, under the size bar for good.

The first release gate reddened on one node, and it is worth recording: `tests/test_loop.py` built
the manager's parent as `SimpleNamespace(ctx=SimpleNamespace(depth=1))`, so the salvage read of
`parent.ctx.run_dir` raised — the exact defect class 0.448.0's own double-conformance gate exists
for, which covers `loop`/`ctx`/`transcript`/`budgets` doubles but not this one.

### A body the model generated incomplete no longer reads as a clean write (F588)

The item asked for "the bytes WRITTEN, not the payload's length" — and that is **empty for a local
write**: `fileops.py` has reported `path.stat().st_size` since the F460/R2452 work, and `write()` is
all-or-nothing, so the two numbers agree whenever the write succeeded. The usable difference can
only come from the BODY, so `fileformat.unparsed()` names a body that does not parse as the format
its path declares and the observation carries one WARNING line. Before this, half a JSON document
and a cut-off `.jsonl` record both read back as successful writes of complete files.

A note rather than a refusal: a deliberately-invalid fixture written to a `.json` path costs one
noisy line, never a blocked write. `edit_file` has the identical blindness and is **not** covered —
the item names `write_file`, and the predicate is reusable as-is.

### A renamed ladder or tuning key is repairable through the API again (F759)

Reported by config-optimizer. `_apply_ladder` tested `unknown` against the PATCH's keys and then
merged, so a pre-rename key in the STORED block survived every write and `load_routine` reported it
forever — `fau` still carried `ladder.max_depth` and `ladder_rung_height` from a web edit made
before D189's rename, and the operator's own repair attempt
(`{"ladder": {"max_depth": null}}`) **could not work in principle**: `model_dump(exclude_none=True)`
strips the null before any writer sees it. That is why his apply "did not fire".

So the writers now hold their RESULT to the known set and say what they dropped (`repaired` in the
response), while an unknown key in the patch stays a 400 — a typo must stay loud, or a save answers
`updated` and does nothing. `write_tuning` returns what it dropped and both its callers report it.
New `web/patch_merge.py` holds the mergers (`api_routine_patch.py` 468 → 430 lines): one
vocabulary, read at both ends, the same set `config/routine_scrub.py` and `load_tuning` scrub
against. The live effect on behaviour was nil — the loader already dropped both keys and fau
resolved to the operator's intent — so this is about repairability, and about two console error
toasts that had no cure.

## [0.449.0] — 2026-10-24

### The util-stats snapshot is computed once, in the daemon (R1.3, `docs/designs.md` part (a))

Every engine used to recompute one fleet-wide read model at run end — a library git walk, the
whole durable usage stream and every un-counted transcript — to write a file **no engine reads**.
The new `daemon/util_stats_watch.py` subscribes to the existing `EventBus`, wakes only on `run_*`,
debounces (5 s quiet, 60 s cap), computes off the event loop and is guarded so a raise cannot end
the listener; it is mounted in `web/appwiring.py`'s lifespan beside the Web Push listener, because
the bus lives in `web/` and `daemon/scheduler.py` sits at its 466-line ceiling. No reader contract
moved: `snapshot_path()`, the file shape, `/api/stats` computing live and the `util-stats` util as
its only reader are untouched. **The speed win is negligible and is stated as such**: 0.225 s cold
against the live homes × 23.6 root runs/day ≈ **5 s of CPU per day**. What justifies it is
structural — one writer instead of N, and the per-run cost was the one that only grows. Two
behaviour changes: `rsched run-once` no longer refreshes the snapshot by itself, and the file may
lag a finish by up to 60 s.

### How long an abort went UNSEEN is recorded (F597)

An abort that goes unseen for 30-60 seconds was unreproducible, because nothing subtracted two
moments that were both already known: the door stamps `runs/<ts>/abort.json` with its own `ts`
before it signals (F751), and the engine resolves that mark at close-out. `Abort.unseen_s` is
computed in `abortmark._unseen_s` and rides the finish event and the health row through the new
`loopend.abort_fields`, so the actor fields and the interval cannot drift apart. It is **absent,
not zero**, when no door stamped a moment (a signal handler writes no file, a parent's kill never
leaves the process): nothing measured is a different claim from an instant abort. Out of
`Abort.__eq__` on purpose — the class is one resolved attribution, and two aborts naming the same
actor for the same reason are the same attribution however long each took to land.

### A numbered answer to a numbered question grounds a goal (F731)

The engine renders an options question as numbered picks — and told a run to lay its choices out
that way — then the goal validator refused the run for the ambiguity the engine had built: *"the
words '1.' are not in anything the person wrote to this run. Quote them verbatim"*. A routine that
had correctly recorded an approval as a goal could not record it at all. **The finding's
prescription would have fixed neither reported case, which measuring showed**: both live records
(`vodafone-outage-claims` run `20261019-031230`, and `tv-show-tracker-seedbox-manager`'s from
September) carry `"options": []` — their choices were numbered in the question's own *prose*,
because a question that really carries `options` submits the option TEXT and needs no resolving.
New `engine/goalpicks.py` therefore reads choices from `options` when present and from the
question's numbered prose otherwise, in both answer-event shapes, skipping fenced code blocks (the
measured question contains a ```js snippet whose first line begins `1 < r.length`). The grounding
rule does not weaken: words nobody wrote, a pick of a number never offered, and a choice offered
but not picked are all still refused. The latest pick of a repeated number wins, and a question
numbering two different lists resolves to nothing rather than guessing.

### A report addressed to a routine that no longer exists is counted apart (F738)

`item_counts.counts()` takes `installed` and classifies an addressed report whose target is no
routine this instance holds as `unroutable` — a fifth disjoint part of `counts.active` with its own
chip beside the live count. Such a row can be drained by **nobody**, yet read as live work waiting
on an owner. It is reported beside the count rather than returned to triage: re-routing would mean
writing in a read model, would have to override the `to` the owner is derived from, and would push
another routine's queued work into someone else's triage. Two corrections to the finding came out
of building it — `grants-radar` was **archived, not deleted**, so the predicate had to be "no
INSTALLED routine" (`registry.scan`, which skips `.archive/`) rather than "no routine in any
home"; and this moves **zero rows today** (R2629 was folded into R2888), so the guard is
prospective. A switched-off or retired target is deliberately not unroutable: it exists and can be
switched back on.

### `workflows.library`'s git pass-through is gone (C1.10)

Its 7 production call sites call `libgit.commit` directly, every subject byte-identical, and
`tests/test_libgit.py::test_no_pass_through_forwarder_stands_in_front_of_libgit_commit` holds the
line. **The entry's premise was stale and the build corrected it**: all three wrappers were already
one-line pass-throughs, so nothing bypassed libgit and the lock, the SIGTERM-first timeout and the
`commit_failed` filing were in force everywhere. What this buys is indirection removal — a
per-module `git_commit` hides which primitive a writer reached, and is where a divergent default
would spread unseen. The red-first is therefore structural, not behavioural: the obvious
behavioural test is green before the change and proves nothing. `library_docs` and `utils_lib`
remain, named by the test as held back.

### Removed

- **`schedule_once`'s dead `expires_at`** (T3.5, the entry's own prescription). It was set by a
  test and by nothing else — no API body, no `schedule_run` action and no console control could
  produce one — so all it could do was drop a make-up fire nobody could see it drop. Gone from
  `arm()`'s parameter and the record it writes, `describe()`'s passthrough, and the daemon
  manager's expiry branch. The docs now say a one-shot **eventually** runs once however stale, and
  that cancelling is how an armed one is dropped. The test that set the field becomes the one
  pinning the new contract: a `fire_at` 30 days past still fires.

## [0.448.0] — 2026-10-24

### Fixed

- **A reply that chains as several CALLS now reaches the engine whole** (F754). The Anthropic
  adapter kept `calls[0]` and dropped every later action call with a `log.warning` no run reads.
  Since 0.446.0 a reply's actions all RUN (`engine/chain.py`), so what was lost was a chain
  arriving as calls: the extra actions vanished at the wire with no `unexecuted_note`, and the
  model's narration claimed effects for actions nobody ran — the R2443-R2445 false-claim shape,
  surviving on one channel. `Completion` now carries them: `parsed` the first object,
  `parsed_rest` the rest in wire order, folded by `replyactions.reply_actions` after the reply's
  text actions so the chain runs them in reply order. The forced re-ask carries them through too.
  A stringly first call is still UNREADABLE and still re-asks — nothing is promoted into first
  place, because the order is what the model meant. `endpoints/base.py` was at its ratchet ceiling
  with zero slack, so the pure usage-dict arithmetic (`fold_usage`, `cache_read_share`) moved next
  door into the new `endpoints/usage.py`, exactly as `media_types.py` once did: 383 → 350 lines,
  back under the house bar, ceiling entry dropped for good. One test had to change rather than the
  code: `tests/test_util_guard.py` handed `action_candidate` a `SimpleNamespace` stand-in built
  from the two `Completion` fields it knew about, and reading a third raised there — found by the
  release gate, and precisely the defect class R2883 (below) ships a check for, except that its
  conformance test covers engine objects and not `Completion`.
- **A refusal that grounds a path now says so** (R2879). A `read_file` refused for being binary
  or oversized adds the path to `seen_paths` on the refusal itself (0.339.0), so the move, delete
  or overwrite that follows is ALLOWED — but the refusal never said it, so a run that did the
  right sequence read it as a dead end, spent a turn confused and reported the gate as a defect.
  No behaviour changed; the words did. Four messages across `fileops.py` and `fsops.py` depend on
  one fact — WHICH looks count — so the fact and its wording moved into the new
  `engine/grounding.py` and all four now state it from one place, with a test pinning that they
  cannot drift apart again.
- **The gate cell says it is the gate on a phone.** R2764's new gate `<td>` was marked `inline`
  but declared no `data-label`, which `base.css` renders as the cell's header once the runs table
  stacks below 860px — so at phone width the gate chip ("skipped") sat on the same line as the
  state chip ("finished") with nothing saying which was which. Red first in the browser at
  390×844: `getComputedStyle(td, '::before').content` read `none` before and `"gate "` after.
- **Two console classes that carried meaning and no style** (found by the new class contract
  below): `unservable` (`fileactivity.js`) had no rule at all, so a path the console cannot serve
  rendered in exactly the ink of one it can, and `strong` (`jobs-panel.js`) was dead, so a job
  title rendered at body weight where its rail twin uses `<b>`.

### Added

- **One conformance test asserts that every attribute a test double ASSIGNS exists on the real
  class** (R2883, decided 2026-10-10). F644 found `engine/ladder.py` dispatching through
  `loop.subs` where the real attribute is `loop.subruns` — "a rung has never judged anything" —
  and the ladder's own tests passed because their fake loop defined `subs` to match the caller: a
  hand-built double's attribute surface is authored by the test from the same misreading that
  produced the calling code. `tests/test_double_conformance.py` reads the suite's own source with
  `ast`, never importing it, and checks 126 doubles of four impersonated classes (34 `loop`, 72
  `ctx`, 17 `transcript`, 3 `budgets`) against surfaces read from class annotations, class
  assignments, `self.x` in their own methods and collaborator `loop.x =` across `src/` — `dir()`
  alone has no `subruns`. Coverage floors make a broken detector red rather than vacuously green.
  No test was rewritten, no double replaced, `SimpleNamespace` is not banned. Known limits:
  detection is by NAME, so ~126 of ~208 namespace doubles are unchecked, only four classes are
  covered, and `Completion` is not among them — which is how F754's stand-in got through.
- **Every governable field the settings recommender may propose is now checked or decided**
  (R2884, decided 2026-10-10). `_semantic_problem` validated 4 of `GOVERNABLE`'s 23 keys while
  both values the operator actually hit (`deliberation`, `capabilities.confirm`) have validators
  on the accept route it never calls — and the accept is whole-draft, so one refused field 400s
  every other change the person kept. `tests/test_recommend_coverage.py` requires every key to be
  semantically checked or on a commented `NO_SEMANTIC_CHECK` allowlist with its reason, forbids
  overlap, refuses an allowlist entry that is not governable, and lets the allowlist only SHRINK.
  The checked set is read from the CODE — `_semantic_problem`'s `if` chain became the
  `semantic_checks()` dispatch table it dispatches on — so the test is not a second hand-kept
  list agreeing with itself. 18 fields were uncovered (not 19: `efforts` was added after the
  2026-10-10 measurement); 13 of them name a validator the recommender does not consult.
  Behaviour is unchanged. Known and deliberately not papered over: `CHANGES_SCHEMA` types
  `value_json` as free text, so no closed vocabulary is an `enum` in the decoded schema today.
- **Every class a JS module mounts is styled, and a STATE class must be REACHABLE from the module
  that mounts it** (R2885, decided 2026-10-10). Nothing checked that two states meaning opposite
  things LOOK different: `tests/test_stylesheet_tokens.py` machine-checks the palette's INPUTS and
  nothing checked its output, which is how 0.399.2 shipped `ladderstrip.js` emitting a bare `bad`
  — so a rung that BROKE rendered in exactly the ink of a rung that declined to run. The accepted
  increment was to widen the existing class contract's `conv-*`/`pane-*` scope; measuring it
  showed that would NOT have caught 0.399.2, because `bad` IS named in CSS (by `.test-result.bad`
  and two others) — none of which that module can match. So `tests/test_static_class_contract.py`
  ships the widened scope (rule A: every mounted class token is named by some rule in one of the
  five stylesheets, waivable by an explicit allowlist — 44 of 746 tokens, four named reasons) AND
  the rule the prefix scope could not express (rule B: a state class must be reachable from its
  mounting module — never waivable). Proven by reproducing 0.399.2's defect with no throwaway:
  rule A passed, rule B red naming all three tokens and the rules that cannot match them. A
  stale-waiver test reds on any allowlist entry no longer mounted or since styled and forbids
  waiving a state word. Shortfall, named: rule B proves a state class can MATCH a rule, not that
  two states' computed colours differ — the later browser increment still stands — and the
  41-word severity vocabulary is hand-maintained, so a state word added later goes unchecked
  until someone adds it.

## [0.447.3] — 2026-10-24

### Fixed

- **Two tests no longer burn 60 seconds of real time each in every release gate** (F739).
  `test_remind_global_resolution.py::test_resolution_from_the_shared_store_still_asks_for_approval`
  (`[listed]` and `[universal]`) files a blocking approval that nobody answers, so
  `interact.handle_ask` polled all the way to `ask_timeout_min` — and the test routine
  inherited `conftest.py`'s 1 minute, the smallest whole minute there is. Both now pass
  `budgets={"ask_timeout_min": 0}`, the value `test_decisions.py` already uses for exactly
  this shape: the question is filed, the wait expires at once, the run continues on its
  default and the shared store is untouched, so every assertion is unchanged and the path
  under test is the same one. **Measured: the whole file went from ~130 s to 0.38 s** (6
  passed), taking ~120 s off every release gate the fleet runs. No production code changed —
  the deadline was never the defect, the test's own budget was.

## [0.447.2] — 2026-10-24

### Fixed

- **The refusal shortcut's opening sentence now ends at a fullwidth or CJK stop** (R2844,
  restated in R2863). `refusal._OPENING_STOP` split on ASCII `.!?;:` followed by whitespace,
  so a reply whose first sentence ended in `。` `！` `？` `．` `；` `：` was never split and the
  whole 200-character head counted as "the opening sentence" — letting the zero-cost marker
  shortcut decide a refusal from a marker in a LATER sentence, which is exactly the
  over-broad match the single-sentence restriction exists to prevent. The new stops split
  with NO whitespace lookahead, because the writing systems that use them put no space after
  them. `tests/test_refusal_fullwidth_stop.py` has a case per codepoint; red-first verified
  against the old pattern (all 12 cases carried the second sentence and its marker, none do
  now), and the ASCII rule is pinned unchanged so `3.5 million` is still one sentence.

### Documentation

- **`childrun.inheritable_resources` states the post-0.432.0 contract** (F589): a spawned
  child has REACH (it can run the routine's own `scripts/`, read its roots, call the utils
  its grants name) but holds NO reserved capability — no `shell`, no reserved util or verb,
  no authoring permission, whatever the parent holds. So a standing order briefed to a child
  in terms of a capability does not run and cannot be made to run from inside the child.
  Brief children in terms of reach; keep reserved acts in the parent's turns.
- **`anthropic_api._parse`'s multi-call warning no longer cites the pre-chaining contract**
  (F754). It said "the first is read — the engine runs a reply's first action", which has
  been false since 0.446.0: a reply's actions all run, in order. Actions written as TEXT do
  chain; extra tool-use CALLS are still DISCARDED at this parse, because `Completion.parsed`
  is one object. The comment and the log line now say that, and name F754. **The carrying
  fix is not in this release** — `parsed` holding a list touches every adapter, so it is
  filed as F754 and handed to `scheduler-builder`.

## [0.447.1] — 2026-10-23

### Fixed

- **A still-open `config_patch` names the one control that would apply it** (the operator's
  ruling of 2026-10-23, `q-20261023-130002-345`, option 3 verbatim: *"a prose answer never
  applies, but what the run reads names the one-click action that would"*). R2189 keeps prose out
  of the config-writing path and R2778 tells the asking run what happened AFTER an answer;
  between them sat the question still OPEN, which reached the run as bare prose. A run whose own
  budget proposal was pending could not tell from its prompt that exactly one control applies it
  — so waiting for an answer to apply it was waiting for something that cannot happen. The
  digest's open-questions line now carries `carries a config_patch, NOT yet applied (<routine>
  still holds the old value) — answering never applies it; only the Decisions page's
  "approve & apply" button does.` New `patchreceipt.pending_suffix`, beside the two verdicts it
  must not be mistaken for: it is deliberately NOT one, because `APPLIED` / `NOT APPLIED` judge
  an answer that exists and a pending patch is neither (pinned by a test). A question carrying no
  patch gains nothing — the line is absent, not empty — so every other routine's open questions
  read exactly as before.

## [0.447.0] — 2026-10-23

### The run gate's history, on the routine page (R2764, the operator's words)

> "like the list of previous runs under 'What a finished run delivers' we also need a history of
> the previous gates. which ones fired, which ones didn't. report that"

The gate has recorded every decision since F457 (`daemon/run_gate.py`:284) and no read surface
served a byte of it: a routine's payload carried `run_gate` — the CONFIG, what the gate WOULD
ask — and its run rows, what ran, with the question between them answerable only by reading JSON
on the box. New `readmodels/gate_history.py` projects `runs/<ts>/gate.json` onto each run row as
`gate`: the `decision`, the `fire_reason`, the gate's own sentence, and per check its `id`,
`kind`, whether it found `work` and why. **"Which ones didn't" is reachable because a skipped
fire leaves a run dir** — `daemon/runner.py` claims it (:230) before it asks `run_gate.admit`
(:330), so a skip is a run row with no turns, not a missing row. The gate's `fingerprints` (a
hash per check, the baseline the NEXT fire compares against) are projected away rather than
filtered in the console. Memoized on gate.json's own stat, since a routine page reads 50 rows per
request. The routine page gains a `gate` column with a per-check hover and a
`n of the last m fires judged · x fired · y skipped` tally — **only when some fire on show was
actually judged**, so the ungated majority of the fleet reads exactly as before. A fire no gate
judged renders as absent, never as a gate that found nothing.

### Fixed

- **The health-event stream is readable from inside a run** (R2764). `.control/` is not listable
  for uid 1000 on this instance although the file inside it is 0644, so `gu rsched logs` raises
  `PermissionError [Errno 13]` and the documented way to inspect gate and fire events was
  unusable from EVERY routine run. `GET /api/health/events` serves the records themselves —
  newest first, `routine` and `event` narrowing by exact name, a bounded `limit` (100, max 2000)
  and a `total` saying what the answer left out. The web process already parsed that exact file
  for `/health/blocked`, so this costs no new reader and no new store. `/health/blocked` stays
  the right read for "what did the fleet owe and not deliver": it folds and it windows, keeping
  only `BLOCKED_EVENTS`; this one neither folds nor windows, because a reader chasing a quiet
  failure wants the last event of its kind however old it is. The util still reads the file
  directly and knows nothing of the route — that half belongs to the util library.

## [0.446.0] — 2026-10-23

### Action chaining (D203, the operator's decision)

A reply's actions now RUN, in the order the model wrote them, inside ONE turn — "like in the
terminal", with every result coming back together at the start of the next turn. `reply_actions`
already returned the ordered list and the loop ran element 0; the new `engine/chain.py` runs them
all and owns the whole of what one landed turn does. The chain stops at the first action that
FAILS (`&&`, the operator's correction), and also on a hold, a cancel, an abort, a spent budget,
a `MAX_CHAIN` of 8, or an element that fails the four checks every action meets
(`completion.judge`, split out of `action_candidate`, so a chain is not a hole in the permission
model). A `finish` or `do_nothing` runs only as a reply's FIRST action: written after another
action, its account was composed before that action's result existed (R2443-R2445). A read
earlier in the same chain does not ground a later overwrite — the model has not seen it yet.
Each element lands as its own `assistant_action` event (same `turn`, plus `chained: n`) and its
own observation, so the Anthropic adapter still renders each as the call it was and a resumed leg
replays them unchanged; the rewind/branch boundary, the phase rail, the refusal flag's
`served_by` and the console's turn labels were all corrected for a turn with several actions.
ON for every routine, with an off switch in the routine page's settings (`chain_actions`, default
true, a missing key reads as on — R2782's lesson, and no pattern governs it). Every prose surface
that told runs "exactly ONE action per reply" was rewritten in the same release.

### A run finishes `partial` only when it is blocked (D204, the operator's decision)

"Routines should finish partially instead of fully okay, *only* if they're blocked by sth they
cannot unblock themselves." Every surface a run reads now says that one thing: the harness
honesty paragraph (the honesty half untouched), the finish bullet, the `status` schema
description, the four pinned copies in `docs/prompt-anatomy.md`, and the `general-task` +
`converse` seed patterns — the ledger sentence keeping its force and losing the status word.
`library-seed`'s own `evidence-discipline` carried the retired definition and would have seeded
the contradiction into a fresh install. Of 232 ok and 4 partial fleet finishes in the 14 days to
2026-10-23, all four partials were the run's own choice and none was forced by the engine.

### Fixed

- **A placeholder accounting entry no longer satisfies the finish guard** (F748). A run finished
  `ok` on `d2 met: test`: the check saw that an entry existed and parsed its verdict, never that
  the clause after it answered for nothing. The floor is WORDS, not characters — over 2,093
  entries from 14 days of transcripts the shortest legitimate ones are `no filing` (9 chars) and
  `no letter due`, so a character floor would refuse real `not due` accounts. The refusal quotes
  the offending entry back. `accounting_counts` still counts it, deliberately: it is the
  `met_rate` ruler and discounting would re-score history.
- **An aborted run names the actor that stopped it** (F751). "Run aborted by the user/daemon."
  attributed an abort to nobody. Each door now leaves a pid-stamped `runs/<ts>/abort.json` before
  it signals (F480's pattern one layer down), the engine's own signal handlers record what they
  know, and a parent's kill carries its reason on the child's `AbortEvent`; the finish text and
  payload name the actor, `unknown` only when nothing established it.
- **A `routine.yaml` edit no longer bypasses the run gate** (R2813, the operator's ruling). The
  bypass fired on any change to the routine's config, and one routine's gate had been overridden
  on five of its six admitted fires — every one by his own settings edits, three of them to the
  gate itself. `CHANGE_SURFACE` drops `routine.yaml`; a revised recipe (`tuning.yaml`, `main.md`,
  `stages/`) and the finish line still admit a fire, and *Run now* is the deliberate one-off.

## [0.445.2] — 2026-10-23

### Fixed
- **The job-store seal follows the routine's `jobs` dial, not the path** (F752, taken over from
  R2787). `state/jobs.json` was sealed for EVERY routine purely by name, and the refusal pointed
  the refused run at the `job` action — which, with the job layer off, is projected out of its
  schema and does not exist for it. `voice-model-trainer` met exactly that: no `jobs:` key in
  its `routine.yaml`, its own 174 KB hand-maintained record of remote training jobs at that
  path, and a recipe stage built around writing it. Renaming either side would have fixed one
  routine and left the collision for the next, so the seal now asks the dial.
  New `engine/storeseal.py` holds the engine-owned stores and their refusals: `engine/fileops.py`
  was at its size ceiling (628) with no room to make the seal dial-aware, and the seal is
  consulted from two places (the write gate and the removal gate, which asks the same question
  of everything a `delete` would take with it), so it was a boundary the code already had.
  **Only positive knowledge of "off" unseals.** `_jobs_gate` is one of the seals `_write_gate`
  applies ABOVE its `if grants is None` early return (F698 found the strongest refusals were the
  ones a policy-less context skipped), so a context with no policy keeps the seal, as does a
  policy held across a release that cannot answer `jobs_on` (F677) — in that case the setting is
  read from the routine's own capabilities through `engine/buildskew.py`. A child run is
  unaffected: it is not a routine whose dial is off.
  `tests/test_store_seal_dial.py` pins all six: the reported write lands, a routine that keeps
  jobs is still refused, no-policy still seals, a held pre-rename policy still seals rather than
  failing open, the obligations half is unconditional, and the removal gate follows the same
  dial both ways.

## [0.445.1] — 2026-10-23

Two `docs/designs.md` entries, both isolated defect fixes in code that was already doing nearly
the right thing — hence a patch, not a minor.

### Fixed
- **The refusal shortcut reads a curly apostrophe as the straight one.** `REFUSAL_MARKERS` are
  typed with `'`, and models write `I can’t` (U+2019) constantly, so the zero-cost
  opening-sentence shortcut matched none of them and spent a classification subcall on an
  obvious opener. `engine/refusal._opening_sentence` now folds the HAYSTACK's punctuation
  (U+2018, U+2019, U+02BC, U+FF07 → `'`; U+201C, U+201D → `"`); the markers stay the repo's own
  readable constants. A punctuation fold and nothing more — no NFKC, no accent stripping, no
  dash or ellipsis mapping. Red first: `looks_like_refusal("I’m sorry, but I can’t help with
  that.")` asserted False against the old code, and a new negative case pins that folding
  invents no refusals.
  *Known limit:* no marker contains a double quote, so the two U+201C/U+201D mappings change no
  outcome today and are untested; they are kept so that a marker carrying a quoted phrase cannot
  reintroduce the bug. *Not fixed, reported as R2844:* `_OPENING_STOP` splits on ASCII sentence
  stops only, so a fullwidth or CJK stop leaves the whole 200-character head as "the opening
  sentence".
- **A pipeline call asks for what the model can return, not a flat 16,000.**
  `workflows/pipeline.py` asked `max(ref.max_tokens, <the artifact's constant>)`, so a model
  whose resolved output cap is below the constant was asked for more than it can deliver — a 400
  or a silent truncation, since providers validate `input + requested_output <= window`. The bug
  was the DIRECTION of an operator already present: the new `_budget(ref, want)` takes the
  smaller of the two, and leaves the constant standing when the cap is unresolved (clamping to a
  falsy cap would ask for zero tokens and ship nothing). It reaches the cap through the existing
  public seam, `ModelRef.max_tokens`, which `EndpointRegistry.resolve` already fills from the
  provider-discovered `max_output_tokens` with operator config winning — no private import.
  **Slightly wider than the design entry's (a), deliberately:** the same `max()` was also
  RAISING the outline call from its 8,000 constant up to the model's cap, so every request is
  now ≤ both bounds. Red first: `assert [8192, 16000, 16000] == [8000, 8192, 8192]`.
  *Scope:* `tests/test_pipeline_budget.py` pins what the pipeline ASKS FOR (unit-level over
  `_pipeline` with a scripted endpoint); the cached-row → `ref.max_tokens` chain is pinned by
  `tests/test_model_limits.py`, not by the new module. Part (b) of that entry (deleting
  `schedule_once.arm(expires_at=…)`) is NOT built, so the entry stays in `docs/designs.md`
  carrying only (b).

## [0.445.0] — 2026-10-23

Four decided items: **R2428** and **R2425** (accepted 2026-10-10), and the two that 0.444.0
could not ship because the live checkout was mid-edit — **D185**'s completed campaign and
**R2195**.

### Added
- **`rsched postdeploy` — the deploy session as a fixed checklist with a record** (R2428). A
  deploy session is this project's highest-yield defect detector and was entirely unstructured:
  of 2026-10-01's releases, only 0.374.0 came from a scheduled detector; everything else came
  from a human mid-deploy. `rsched validate` asks whether the config is flyable and `rsched
  drain` quiesces the box — both run BEFORE; nothing ran after. Four named checks now do, each
  derived from a defect that really went unnoticed: `served-identity` prints WHICH box answered
  and where the address came from (0.374.0 — a deploy script drove the wrong host's Docker
  daemon, ~5.2 GB built locally), `build-skew` compares the version the daemon SERVES with the
  checkout it came from (`docker compose up -d` never reloads bind-mounted code, so a green
  `compose config` means nothing about what is live), `backup-freshness` reports how long since
  `rsched-backup.service` last COMPLETED (F602 — 19 nights unnoticed), and
  `state-path-ownership` walks the homes the daemon must write for a uid it cannot write as
  (0.373.1 — one root-owned 0600 file failed its whole home). One line per check, one JSONL row
  per session in `<routines_home>/.control/postdeploy.jsonl`, so the inspection leaves a trace
  instead of living in the operator's head. The item explicitly rejected "more deploy tests" —
  10+ deploy modules exist and more would have caught none of the four, because each was a
  property of the live instance and not of a fixture. Exit 1 means exactly one thing: the
  operator must act before he walks away from this deploy; everything else is NOTE and stays
  green, because a command that exits non-zero on something benign is run once and never again.
  `daemon/backup_watch.staleness` and `/api/status`'s own `build_skew` are consumed as given,
  not re-derived — with one deliberate divergence recorded in `backup_watch`'s docstring: a
  backup record that cannot be READ AT ALL is a FAIL here where the daemon's watch calls it
  `unknown`, because F602's lesson is that for a human at the box the ABSENCE of the indicator
  IS the finding. **Known limit: the real HTTP call to `/api/status` is exercised nowhere** —
  `_status` is stubbed in all 42 tests, since a run's sandbox cannot read the live config
  (Errno 13) or `/api/settings` (403). The checks' logic is tested; their one real I/O path is
  not, and the command has never run against a live instance.
- **A fifth Decisions group: the proposals nothing is waiting on** (R2425). The page is an inbox
  sorted by urgency, and all four captions promised a CONSUMER waiting on the operator — for a
  standing proposal that is literally false: accepted items landed under *"Deferred — the next
  run picks these up"* and no run picked them up. Precedent: 0.345.1 split Messages' `active 75`
  into `active 59 · unread 16`. The new group's caption is `Proposals — considered ideas, decide
  when you like`, rendered below Blocking/Deferred/Meta and above Settled, with the urgency
  affordances OFF: `waits: false` drops the coral waiting-on-a-person colour, the card renders
  `filed <date>` with NO `[data-when]` so the 30-second time ticker can never age it into
  "3w ago", there is no expiring chip and no countdown, and inside the group items sort by
  routine rather than by lateness. A proposal that has sat three weeks is not late. The
  predicate is DERIVED, settling the item's open point without touching the backend: a proposal
  is an open, non-blocking, non-meta item whose asking run has ENDED (`run_state` is already on
  the row, and this is the derivation the card's own "answer & run now" control already makes).
  **Known limit, asserted in a test rather than glossed:** the client cannot see FUTURE
  SCHEDULES, so an ordinary deferred ask from a finished run reads as a proposal even if its
  routine fires in an hour; and the open card's kind chip still reads `deferred`, since it
  renders the record's own `mode`.
- **A run can choose its own model** (D185, the campaign's completion). `role_swap` is a SIDE
  FIELD riding any action — `cheap` to drop `main` to the routine's configured cheap model when
  the run judges the remaining work mechanical, `strong` to come back up — applied at the turn
  boundary that already re-resolves roles every turn. It is deliberately NOT a new action kind:
  a kind costs five prose/count pins and a whole expensive turn spent asking to be cheap, which
  is self-defeating for a token-saving feature, while `note` and `remind` are the repo's own
  precedent for a field that rides the action a run was taking anyway. Since D188 (0.440.0) the
  per-turn model is on the transcript, so a swap is auditable after the fact.
- **A dead model credential is found before a run spends its first turn** (R2195). The daemon
  proactively keeps user connections alive but never checked the credentials every run depends
  on; a new `endpoint_needs_reauth` health event fires when every proxy account that could serve
  a model endpoint is unavailable. **Known limit: it watches only CLIProxy-bound endpoints** —
  OpenRouter and Nano-GPT are not covered.

### Changed
- **The side fields' checks live in one module** (D185 step 1 + 2's extractions).
  `engine/shiftedfields.py` takes the three readers of a malformed action out of
  `engine/actions.py` (the util naming rule, F546's telemetry attribution and D146-C's
  whole-object diagnosis — they must not drift, because the guard and the diagnosis read ONE
  signature), and `engine/sidefields.py` takes the side fields' validation and landing, so each
  new side field no longer costs the action contract's single home two lines it does not have.
  `actions.py` 632 → 545, with both ceilings re-tightened. The Decisions page's inbox TAXONOMY
  (groups, filter chips, priority ordering, the expiring-soon rule) likewise moves out of
  `static/views/questions.js` into `static/views/questions-taxonomy.js` (460 → 449) — that file
  had ZERO ratchet slack, so the extraction came first and the fifth group second.

### Fixed
- `tests/size_ratchet.json`'s `note` lost 34 characters of the R2197 clause when 0.444.0's merge
  resolution joined two appended clauses, leaving one beginning mid-sentence
  (`entry dropped)7 moved the decision card's…`). Provenance prose only — no ceiling and no
  behaviour — but that note is the only record of why each ceiling moved.

## [0.444.1] — 2026-10-23

### Fixed
- **A tool parameter that swallowed the ones after it is repaired before validation**
  (F747). Measured on `birthday-admin:20261022-080004`: the model closed its `summary`
  parameter with a MISMATCHED tag — `</summary>` instead of `</parameter>` — and the
  provider's pseudo-XML tool-call reader then ran on to the next real `</parameter>`. So
  `summary` arrived carrying its own text PLUS `</summary>\n<parameter
  name="accounting">[…]`, and `accounting` arrived ABSENT. The engine answered "your
  `accounting` is incomplete: no entry for d1…g1" — truthfully, about a field the model had
  written and could still see in its own reply. That run spent turns 84-95 bisecting the
  field at full prompt price and ended by shipping a 47-character diagnostic stub as its
  permanent summary, with placeholder accounting entries the check accepted; its work was
  sound and only its record was destroyed. New `src/rsched/engine/paramsalvage.py` puts the
  swallowed parameters back at the seam `normalize_action` already strips grammar debris at
  (`engine/completion.action_candidate`), under three conditions that all must hold: the
  closer names the field CARRYING it, every restored name is a real `ACTION_SCHEMA` field,
  and the field being restored is EMPTY — any one failing abandons the whole repair and
  returns the action untouched, so prose quoting the broken shape (this entry included) is
  never eaten. The repair is not silent: each one files `tool_param_salvaged`, one row per
  run, deliberately NOT blocked work (the turn happened — `git_lock_cleared` is the
  precedent) and deliberately not a `wrong_turn_detected` reason. The swallow hit every
  finish whose summary ran long (1470-5170 chars, 9 of them) and no short one (47-103
  chars, 5 of them); a scan of 149 fleet transcripts over 14 days found it in that one run.

## [0.444.0] — 2026-10-23

Three decisions the operator accepted long ago and nothing had built: R2197 (2026-10-03),
R2196 (2026-10-03) and R2426 (2026-10-10).

### Added
- **A decision can be DECLINED** (R2197 — *"a decision can be answered, snoozed, or deferred,
  but never declined"*). The Decisions page had exactly three verbs: `answer` (which closes the
  item, but only by *saying something*), `snooze` (its own docstring: "UI noise control, **NOT
  an answer**") and `defer` ("the record stays open as deferred"). So an operator who had read a
  proposal and did not want it could type prose, snooze in circles, or leave it open — and every
  accept/reject item this instance files was compensating with hand-maintained status blocks.
  `POST /questions/{qid}/decline` writes the **ordinary** `inbox/answer-<qid>.json` payload with
  `decision: "declined"` plus an optional reason, so `collect_deferred_answers` drains it
  unchanged and there is no new consumption path. The value is carried onto the answer pair, the
  archive row, the `/api/questions` row, a digest line reading REFUSED and a red `declined` chip
  in place of "answered" — so an asking routine learns `declined` as a **value** instead of
  parsing a sentence, which is what lets it stop re-surfacing a refused idea without a human
  keeping a do-not-chase list. The open design point (reuse `engine/requests.DECISIONS` or not)
  was decided on the tree's own evidence: a **parallel, question-scoped** value in
  `rsched/decline.py`, because those values seed a run's grant overlay, because
  `_decide_request` already 400s a decision on a non-request question, and because `deny_now`
  means "declined for THIS run", which a declined proposal is not. An access request's "no"
  therefore stays its typed decision: the route 400s one and the button is absent from that card.
  A defect found by reading rather than by a test: a redundant `declined: true` flag was set
  only on the `_mark_answered` path, while a **consumed** answer's settled card is rebuilt from
  `questions/answered/<qid>.json` (F525) — so a declined decision would have read as an ordinary
  answer at exactly the boot that acted on it. One value now travels both paths.
  *Known limit:* a declined BLOCKING ask is settled by `interact._held_not_settled` matching the
  phrase's head word, not by the value; widening D38's settle rule needs `interact.py`, which is
  at the size bar. A test pins the head word, so a reword goes red first.
- **`rsched validate` asks whether THIS instance's config can FAIL OVER** (R2196 — *"the suite
  proves the CODE correct against fixtures and never asks whether this instance's config is
  flyable"*). Every `load_server_config` call in `tests/` but two passes a tmp path, rightly so
  (`production_guard.py`, after F394) — so the defect class that falls through is a CONFIG
  defect, and the property that killed **eight routines at turn 0 on 2026-09-14** was checkable
  with no network call at all: `Astra high`'s only fallback was `Opus high`, both transported by
  `cliproxy`, and one expired subscription token took the whole chain out. `rsched/conformance.py`
  checks two offline properties per role — a chain with **no reachable fallback** (R1492: a
  classifier refusal advances the chain, so a model with nowhere to go turns a refusal into a
  dead run) and a chain whose members **share one failure domain**. A domain has two independent
  axes and sharing *either* is fatal: the endpoint's `base_url` (one host, one outage) and its
  `key_var` (one credential, one expiry). Demanding both would have passed the 2026-09-14 shape
  itself. `system_model` is checked as the instance's, since every unset role runs on it.
  **Reported, never counted:** the findings print as `NOTE  failover ·` lines beside the setup
  and recipe notes and do not change the exit code — whether a single-domain chain is acceptable
  depends on how many providers this instance has, and making it fatal is the operator's call.
  Not measured against the live fleet: a run cannot read the server config (Errno 13), so an
  in-run count of affected routines is vacuous and was not claimed.
- **The console's stream load is bounded again, and the pool it lands on is named** (R2426).
  0.376.0 removed `stream.js`'s three-tail ration and its REST-polling fallback — correctly,
  since under SSE the browser's ~6-connections-per-origin ceiling was the de-facto cap. But
  WebSockets count against a far larger limit, so both caps went at once and the server never
  had one. **One correction to the accepted item's premise, which changed the fix:** the tails do
  *not* use anyio's 40-token limiter. `streams.run_stream` calls `asyncio.to_thread` — the event
  loop's default executor — and so do `settings/machines.py`'s SSH calls and
  `settings/endpoint_probe.py`'s provider calls; the only `anyio.to_thread` caller in
  `src/rsched` is `api_questions.py`. The documented explicit-`CapacityLimiter` route would have
  bound nothing. New `web/ioload.py` owns `POOL_WORKERS=32`, `SLOW_IO_RESERVE=8` and the
  **derived** `MAX_LIVE_STREAMS=24`; `streams.serve` holds a slot for each socket's life and
  closes `4429 CLOSE_BUSY` past the ceiling. The derivation is the point: `run_stream` awaits its
  two reads sequentially, so one tail holds at most one pool thread at an instant and the
  ceiling provably leaves 8 threads for the SSH and provider callers. `/api/debug/threads` now
  reports `io_pool{workers,threads,queued}` + `streams{live,ceiling,refused}` — a silent default
  is what this item is about. *Still open:* `q-20260912-121159-40` (the true bulkhead) needs
  those callers moved off `asyncio.to_thread`; the two constants are reasoned, not measured
  under load.

### Changed
- `rsched validate` moved out of `cli.py` whole, into `src/rsched/cli_validate.py` — `cli.py` was
  at its 356-line ceiling and is now 266, back under the house bar (its ratchet entry dropped),
  the same split F746 made for `abort`/`drain`. The decision card's lifecycle controls moved out
  of `static/views/questions.js` (493 → 460) into `static/components/decision-lifecycle.js`, so
  the decline button cost that file no line.


## Earlier releases

Releases before 0.444.0 are condensed to one line each; their full notes are
in git history (`git log -p -- CHANGELOG.md`).

## [0.443.1] — 2026-10-22
D175's playbook offer said "work" twice.

## [0.443.0] — 2026-10-22
A routine's desktop can run with no screen at all (F649, the operator's order of 2026-10-16: "give the desktop a headless mode a routine can select,….

## [0.442.0] — 2026-10-22
The finish-summary contract asks for 3-6 conversational lines, not a DETAILED 8-20 (D187, the operator's order of 2026-10-16: "end-of-run summaries….

## [0.441.0] — 2026-10-22
A `do_nothing` action, so a run can say nothing was due and why (D186; operator: "Add a `do_nothing` action carrying a short reasoning").

## [0.440.1] — 2026-10-22
The browser suite launches its OWN browser; the Chrome sidecar is now an explicit opt-in (operator, 2026-10-22: "Maybe we shouldn't use the sidecar….

## [0.440.0] — 2026-10-22
The role that resolved to that model is stamped beside it (`main`, `tool_call`, `uncensored`), so a turn reads `main → claude-proxy/claude-opus-5 ·….

## [0.439.0] — 2026-10-22
A util grant carries the util's own declared secrets (F562), so one operator decision makes a granted util usable.

## [0.438.2] — 2026-10-22
The global pause now holds the reap's post-finish inbox wake — the last self-starting path 0.438.0 did not reach.

## [0.438.1] — 2026-10-21
The two approval dials on the settings card now say WHICH approval each one is (`reminder approval` / `config approval`).

## [0.438.0] — 2026-10-21
A cost-trend finding names the host when the fleet degrades together (F568).

## [0.437.0] — 2026-10-21
A run can never write `routine.yaml`: it proposes the change on an `ask_user` carrying a `config_patch`, and the Decisions page applies it with one….

## [0.436.2] — 2026-10-21
F537 closed the dead apply button for a patch's own keys: a `config_patch` whose top-level key the apply route would refuse is refused at the turn….

## [0.436.1] — 2026-10-19
False positives.

## [0.436.0] — 2026-10-19
The operator's instruction was "use this (https://arxiv.org/pdf/2609.36139) to improve prompts".

## [0.435.0] — 2026-10-19
The operator's order was "implement hooks first as a single mechanism unifying a), b) and c)" — reports/messages, consequence reminders, run….

## [0.434.3] — 2026-10-19
The promise is stated in three places — `background.py`'s docstring, `cancel`'s own observation note, and the `handle` field's schema description….

## [0.434.2] — 2026-10-19
CHILD → PARENT, mid-flight — `target: "parent"`.

## [0.434.1] — 2026-10-19
Three `finish_gate_failed` events on 2026-10-12 between 07:34 and 08:26 — the instance's first ever, in `scheduler-builder`, `llmsectest-weekday` and….

## [0.434.0] — 2026-10-19
`docs/designs.md` listed "the concurrency cap" among D118's built phases.

## [0.433.2] — 2026-10-18
`engine/executor.py`'s list dispatch took its target as `(args[0] or "").lstrip("-")`, so a leading flag became a NAME.

## [0.433.1] — 2026-10-18
New pure `src/rsched/remind_mute.py` holds the one rule.

## [0.433.0] — 2026-10-18
One modality-tagged allowlist, new pure `endpoints/media_types.py`: `MEDIA_MODALITIES` maps mime → `image` / `pdf` / `audio` / `video`, and the….

## [0.432.0] — 2026-10-18
A `script` action looked in the run's own dir (and an open job's workspace) only, and a child run's own dir is its workspace under….

## [0.431.0] — 2026-10-18
`in_transit` — addressed reports still `open`, aimed at a routine that will run.

## [0.430.0] — 2026-10-18
The verb VOCABULARY is the util's own `usage:` block (`utils_verbs.declared_verbs`), so the engine never reimplements a dispatcher.

## [0.429.0] — 2026-10-17
A routine is born with its outcome contract.

## [0.428.1] — 2026-10-17
Runs are priced from the first tick after a deploy. 0.428.0 refreshed prices only with the model-limits cache, whose 24-hour TTL was still running….

## [0.428.0] — 2026-10-17
Outcome contract (`config/outcome.py`, `engine/outcome.py`).

## [0.427.0] — 2026-10-17
A migration's deadline now reaches a person before it reaches the gate (F702).

## [0.426.1] — 2026-10-16
A 402 that says "retry after in-flight requests settle" is a transient, not a dead run.

## [0.426.0] — 2026-10-16
Refusal takeover with exponential back-off — the honeypot premise is retired (operator order 2026-10-16: "it's not a Honeypot anymore.

## [0.425.0] — 2026-10-16
A report row is disposed of only by its recipient (D182).

## [0.424.2] — 2026-10-16
`fileops._write_gate` returned early — `if g is None: return None` — above two refusals that have nothing to do with grants: `_engine_owned` (the….

## [0.424.1] — 2026-10-16
`uv.lock` relocked with `uv lock --upgrade` (watermark was 2026-09-11, 35 days stale): 37 packages moved, 1 downgraded, 1 dropped.

## [0.424.0] — 2026-10-15
`BALLOON_RATIO` is now 2.7 — the measured p95, rounded down to one decimal — and the constant's comment states the measurement rather than a rationale.

## [0.423.1] — 2026-10-15
`pending_edits.py` carried `MIGRATION(expires=2026-10-15)` over the three `trigger_` appliers and the `_read_triggers`/`_write_triggers` pair they….

## [0.423.0] — 2026-10-15
New health event `lane_catchup_declined`, in the machine-checked vocabulary and in `BLOCKED_EVENTS`, so the console's blocked-work fold renders it….

## [0.421.0] — 2026-10-14
Custom cron is a cadence of its own beside Manual … Monthly, in the routine's Schedule section and in a lane's schedule alike: its field takes a….

## [0.420.1] — 2026-10-14
Gemini 4 Argon, bound through the Antigravity sign-in, answered `404 Requested entity was not found` on every call.

## [0.420.0] — 2026-10-14
`routine.yaml` `efforts: {main, tool_call, uncensored}` beside `models:` (conversations too), `system_effort` and `compaction_effort` in config.yaml,….

## [0.419.0] — 2026-10-14
New event `fire_paused` (`daemon/scheduler.py`): routine = the slug, `run_id` empty, carrying `cron` and `next_fire` exactly as `lane_fire_paused`….

## [0.418.1] — 2026-10-12
0.417.0 shipped the run rail's job separator as an inline style, with the reasoning stated in its own entry: "Both stylesheets are frozen at their….

## [0.418.0] — 2026-10-12
The operator's answer, 2026-10-11, in his own words: "the ledger is cheap disk, the prompt is the scarce resource, and R2436's lost question would….

## [0.417.0] — 2026-10-12
The operator's order, 2026-10-08: "routines with tasks (like the 'fau' routine) need to display the task they're currently working on in the right….

## [0.416.0] — 2026-10-12
The operator's order, 2026-10-09: "the 'tasks' i introduced as subordinate jobs a routine manages itself (see the 'fau' routine) introduced….

## [0.415.1] — 2026-10-12
`CLAUDE.md`'s Transcript-events block listed 19 types while `EVENT_TYPES` holds 20.

## [0.415.0] — 2026-10-11
an `error` result is worded for EVERY action kind by default (`OBSERVATION (<kind> <subject> FAILED): <error>`), except the kinds whose own renderer….

## [0.414.0] — 2026-10-11
On 2026-10-09 a run that had already shipped two gated releases lost its ending: a stale lazy import made `finishgate.check_finish` raise….

## [0.413.0] — 2026-10-11
a file at or under the bar may never cross it — 755 files are held there as of today; - a file over the bar may only shrink; - coming back under the….

## [0.412.0] — 2026-10-11
A standing condition is filed once per window, not once per finish.

## [0.411.0] — 2026-10-11
`report` takes `options` (at most `ASK_OPTIONS_MAX`, the same cap and constant as an `ask_user`'s pick-list).

## [0.410.0] — 2026-10-11
The boundary is reported from inside the loop.

## [0.409.1] — 2026-10-11
Measured, 2026-10-10: six already-shipped decisions (D167, D168, D169, D171, D172, D173) were re-delivered as asks, and `.ui-traces/20261010.jsonl`….

## [0.409.0] — 2026-10-10
Four states, not two.

## [0.408.1] — 2026-10-10
Removed: `background.MAX_CONCURRENT`, `background.refuse_at_capacity`, and the cap's read in `actionroute.dispatch_action`.

## [0.408.0] — 2026-10-10
`background.STOPPABLE_KINDS` = `util`, `script`, `shell`: the kinds whose thread is blocked on a subprocess.

## [0.407.0] — 2026-10-10
`endpoints/anthropic_messages.native_turns` — a schema'd call on `auto` sends each past action as a `tool_use` of the action tool and the message….

## [0.406.0] — 2026-10-10
`engine/replyactions.py` (new) — `reply_actions` reads EVERY action a reply carried, in order: the JSON action objects in its text (scanned with the….

## [0.405.1] — 2026-10-10
`daemon/runner.FIRE_BLOCKING_ROLES = ("main",)` — the fire guard asks `roles_problem` only about roles a run cannot avoid resolving.

## [0.405.0] — 2026-10-10
`Runner.fire_blocker(cfg) -> (cause, why) | None` is the new single source of truth for the decision AND its wording — the shape `resume_blocker` has….

## [0.404.0] — 2026-10-10
`engine/background.cancel` removes the Pending from the live list AT ONCE.

## [0.403.0] — 2026-10-09
`GET /api/runs/{id}/file` now serves the routine's EFFECTIVE READ ROOTS as well as the two trees — its configured `fs_read_roots` plus the….

## [0.402.0] — 2026-10-09
One judge, reading the evidence.

## [0.401.0] — 2026-10-09
The engine refuses the call (`engine/visionsteer.py`), in both dispatch paths and before the secret gate — asking the user to decide an exposure for….

## [0.400.0] — 2026-10-09
`GET /api/credential-names` returns names only, never a value: the central Secrets store's names (already in every run's prompt, D46), the….

## [0.399.2] — 2026-10-09
`0.399.1` gave a rung that raised its own `oversight_failed` event, its own read-model state and its own renderers, so that the mechanism's total….

## [0.399.1] — 2026-10-09
`engine/ladder.py` dispatched a rung through `loop.subs`; the attribute is `loop.subruns`, as every working caller has it (`actionroute.py`).

## [0.399.0] — 2026-10-08
Every past run is rebuilt (one-shot boot migration `migrate_runrecords.py`, with `runhistory.py` and `runevidence.py`): the usage stream's 1,331….

## [0.398.0] — 2026-10-08
The run record says what made a run and how it went (`engine/runrecord.py`).

## [0.397.0] — 2026-10-08
A PDF, Word, PowerPoint or Excel file is converted, not refused (operator order 2026-10-08, "implement your suggestions" on document conversion).

## [0.396.0] — 2026-10-08
The action tool is offered on `auto`, so effort and thinking reach the model (operator order 2026-10-08: "effort does NOTHING?! this needs to be….

## [0.395.0] — 2026-10-08
Goals: what a person asked of a run, kept by the run as their scribe — one mechanism for routines and conversations (operator order 2026-10-08).

## [0.394.0] — 2026-10-08
The never-grantable credential guard names the credential FILES, not the config directory (D169, operator's answer A).

## [0.393.3] — 2026-10-08
Answering a decision that carries a `config_patch` no longer takes its `approve & apply` button away.

## [0.393.2] — 2026-10-08
A one-shot call with no tool definition is no longer written to the cache either. 0.393.1 put its claim marker on the system prompt or the first….

## [0.393.1] — 2026-10-08
One-shot calls through the subscription proxy are no longer written to the prompt cache.

## [0.393.0] — 2026-10-08
`then_script`: a write or edit and the script that checks it, in ONE action.

## [0.392.0] — 2026-10-08
Background actions, phase 4 (D118): a run may have at most THREE background calls in flight, and the fourth is refused with its reason rather than….

## [0.391.1] — 2026-10-08
A shared schema field's clauses each carry their own kind lead — F639.

## [0.391.0] — 2026-10-07
One VM per routine, not a shared one.

## [0.390.1] — 2026-10-07
A note refused for a switched-off sharer told the sender to name the carrying task "in the title" — a note has none.

## [0.390.0] — 2026-10-07
The refusal suggests valid targets (`rsched/recipients.py`), best first: the routine that carries the target as a TASK (named, so the hand-off lands….

## [0.389.0] — 2026-10-07
Which model answers a call that names none: the first configured model that can carry it, the routine's roles before the instance defaults — text:….

## [0.388.0] — 2026-10-07
The setting `capabilities.tasks: off | on`, on the routine page under Abilities → Run history, reminders & tasks.

## [0.387.0] — 2026-10-07
Settings → Decision endpoints — a catalog of its own beside the LLM endpoints (`decision_endpoints`, `decision_models`, a default and an image….

## [0.386.2] — 2026-10-05
a read root the kernel cannot stat is dropped from that call's jail, named in a logged warning.

## [0.386.1] — 2026-10-05
A second check WARNS — never fails — for every marker inside `MIGRATION_WARN_DAYS` (21), naming the file, the date and the days left.

## [0.386.0] — 2026-10-05
One shared predicate.

## [0.385.3] — 2026-10-05
`script` crashed every time — `executor.DISPATCH` has no `script` entry on purpose (its gate sits in front of it in `_route`), so a backgrounded one….

## [0.385.2] — 2026-10-05
The operator reported it plainly: "'open the upstream directly' for the sidecar browser does not work".

## [0.385.1] — 2026-10-04
Found by LOOKING at the rendered transcript rather than by a test, which is the whole reason the look is required. 0.384.0 gave an `unread` row — a….

## [0.385.0] — 2026-10-04
One measurement, both surfaces.

## [0.384.1] — 2026-10-04
The media-fallback error event kept `str(exc)[:120]` of the endpoint's exception.

## [0.384.0] — 2026-10-04
The safety matrix is the crux, not the plumbing.

## [0.383.0] — 2026-10-04
Its own outcome, never the deadline's.

## [0.382.2] — 2026-10-04
A path with a parent directory in front of it.

## [0.382.1] — 2026-10-03
0.382.0 added three fields to `RoutinePatch` without declaring them in `configflow.CLASSIFICATION`, and `tests/test_configflow.py` red-flagged it in….

## [0.382.0] — 2026-10-03
Limits & reach → Oversight — `ladder.enabled` + `ladder.max_depth`.

## [0.381.2] — 2026-10-03
Found by looking at the rendered card, not by a test: a run whose rung 1 ruled `off_track · redirect` and whose rung 2 then handed back nothing….

## [0.381.1] — 2026-10-03
`engine/admin_handlers.py` — a third check beside the self-target and unknown-target ones, off `registry.info` (the memoized one-routine read, no….

## [0.381.0] — 2026-10-03
The four event types are in the vocabulary.

## [0.380.1] — 2026-10-03
`reminder_checks.description_problem` rejects the whole action when a caution runs past `MAX_DESCRIPTION_CHARS` (400), and the refusal named only the….

## [0.380.0] — 2026-10-03
Step 5 of the ladder design (`docs/designs.md` § The escalation ladder), built on the operator's answers of 2026-10-02 to the two questions that had….

## [0.379.0] — 2026-10-02
The old path keeps working, served by the same `receive_hook()` through a second route decorator: a URL a third party holds was handed out by this….

## [0.378.0] — 2026-10-02
The authority is split, deliberately.

## [0.377.0] — 2026-10-02
`action` (the default) is unchanged, including every stored reminder: a record with no `kind` field reads as `action`, so nothing written before this….

## [0.376.0] — 2026-10-02
The capacity fix for F606 (the operator, 2026-10-01: "i cannot open more than three tabs of the routine-scheduler webui. if i do, it doesnt load.").

## [0.375.0] — 2026-10-02
Tailscale was the only https path this repo documented.

## [0.374.5] — 2026-10-02
`daemon/backup_watch.py`'s unknown-state message ran two characters past the 100-column budget and failed the release gate's quality stage.

## [0.374.4] — 2026-10-02
The operator's report: "i cannot open more than three tabs of the routine-scheduler webui. if I do, it doesn't load." The arithmetic was already….

## [0.374.3] — 2026-10-02
`rsched-backup.service` failed every night from 2026-09-12 to 2026-10-01 — nineteen nights with no snapshot — and nobody saw it until a deploy….

## [0.374.2] — 2026-10-02
`deploy/backup.sh` no longer walks the share with `du`.

## [0.374.1] — 2026-10-02
A guarded root can now be NARROWED, and only narrowed.

## [0.374.0] — 2026-10-02
A deploy script refuses a Docker daemon that is not on this host.

## [0.373.1] — 2026-10-01
The `cliproxy` sidecar runs as the instance's uid, not its image's root.

## [0.373.0] — 2026-10-01
The export reads every credential the instance holds before it writes anything — the config's own credential values, the central (`secrets.env`) and….

## [0.372.3] — 2026-10-01
`deploy/nat64.sh on` no longer drops the host's memory ceilings.

## [0.372.2] — 2026-10-01
The action tool is forced again — the 0.371.0 adapter: forced first; a model that refuses forcing (the newest Claude models on the direct API) gets….

## [0.372.1] — 2026-10-01
`util-seed`'s `git` and `remote` are the live library's versions.

## [0.372.0] — 2026-10-01
The Anthropic adapter never forces the tool choice.

## [0.371.1] — 2026-10-01
`rsched/firetimes.py` is the one place a fire instant is computed — the fire table, both catch-up paths (`registry.missed_fire`, a lane's watermark….

## [0.371.0] — 2026-10-01
A util's `fs:` declaration could escape its grant through `..` or a symlink: containment was checked lexically while Landlock mounts the resolved….

## [0.370.2] — 2026-10-01
The wait asks the run.

## [0.370.1] — 2026-10-01
One way to end a group.

## [0.370.0] — 2026-09-30
A timeout terminates git.

## [0.369.1] — 2026-09-30
items: operator (2026-09-30), found on 0.369.0's first boot The Help tab served the pre-0.369.0 build. pdoc imports every module of the package.

## [0.369.0] — 2026-09-30
A routine NAMES its pattern (`pattern:`) and holds every value itself; an override is a field that differs.

## [0.368.1] — 2026-09-30
items: operator (2026-09-30), found while correcting stale lane references in routine memory The routine card was lane-blind.

## [0.368.0] — 2026-09-29
`rsched/lane_fires.py` gains `stamp_paused_skip` / `last_paused_skip` / `clear_paused_skip`, riding the same locked file as the watermark — the two….

## [0.367.0] — 2026-09-28
The cause.

## [0.366.14] — 2026-09-27
items: q-20260927 (operator), R1988 > "where's the ui surface for the script gating routine runs? i cannot find it. it should be > independent from….

## [0.366.13] — 2026-09-27
items: F573 A global pause SKIPS a due lane fire and moves the lane's watermark with it.

## [0.366.12] — 2026-09-27
`landlock_path_beneath_attr` is a packed Linux ABI struct: a u64 then an s32, twelve bytes with no tail padding.

## [0.366.11] — 2026-09-26
items: [F566] Seven runs across five routines died between 01:43 and 04:08 and every one of them reported the same cause: `Endpoint failure: nano….

## [0.366.10] — 2026-09-26
items: F563, R1947, F521 `status.json` has carried `stages: {declared, entered, skipped}` since F521/R1681, to answer "which declared stages did this….

## [0.366.9] — 2026-09-25
the budgets line renders what this node may still start — `at most 8 child runs` with the whole budget in front of it, `3 more child runs (8 shared….

## [0.366.8] — 2026-09-25
items: D146, F547, F460 `weightloss:20260923-220004` was configured on Opus high.

## [0.366.7] — 2026-09-25
items: F554, D146 A run the operator continues, or one that resumes after a restart, appends a further usage record under the same `run_id`.

## [0.366.6] — 2026-09-24
items: D143, F393 The module was 530 lines against the repo's ~350 one-responsibility budget, and it had been naming its own seam since the day it….

## [0.366.5] — 2026-09-24
items: F546, weightloss:20260923-220004 A rejected `kind: "util"` action is counted per-util at the validation seam, because a denied or malformed….

## [0.366.4] — 2026-09-23
Every settings section is framed by `panelSection`, which hands its render callback a `reload` as the THIRD argument — that is how a section that….

## [0.366.3] — 2026-09-23
`rsched.utils_run.Jailed` is the outcome of every jailed subprocess this system runs, and its two interesting fields are `CapturedOutput`, the….

## [0.366.2] — 2026-09-23
Three routine-improver utils were reported to the health stream this morning as `util_killed … by signal 1`.

## [0.366.1] — 2026-09-23
The per-routine cost trend shipped yesterday and flagged five routines on its first morning.

## [0.366.0] — 2026-09-23
`net:` in a util's header is a BOOLEAN — Landlock allows all TCP or none — and the engine container shares a docker network with the headful Chrome….

## [0.365.1] — 2026-09-23
`fail_under = 87` had not been checked since 0.57.1, roughly three hundred releases, and the comment beside it claimed 88 while the gate demanded 87….

## [0.365.0] — 2026-09-22
"invisible — everything else … `~/.config/routine-scheduler`" is true because no grant covers those paths, not because the jail knows their names.

## [0.364.0] — 2026-09-22
`api(path, {body})` serializes the body for you, unconditionally.

## [0.363.0] — 2026-09-22
`status.json` gains a `stages` object — `declared`, `entered`, `skipped`, each in the recipe's own flow order (main.md's run-flow ordering, not….

## [0.362.0] — 2026-09-21
an `<iframe src>` is a naked GET: no Authorization header, and the ticket was put in noVNC's `path` parameter (meant for the websocket), so the….

## [0.361.0] — 2026-09-21
In the Schedule section, carried by that section's existing save schedule button rather than a second one: one save for "when this routine runs". -….

## [0.360.0] — 2026-09-21
`/browser-view/<path>` relays the noVNC page and its assets (`httpx`, hop-by-hop headers dropped), and `/browser-view/websockify` relays the VNC….

## [0.359.0] — 2026-09-21
Consumption archives what it removes: `questions/answered/<qid>.json` holds the ask, the answer, its source and when a run took it.

## [0.358.0] — 2026-09-21
`config` merges over the stored block, key by key.

## [0.357.0] — 2026-09-21
`GET /conversations/{slug}/messages` lists what is queued and not yet consumed. - `PUT /conversations/{slug}/messages/{id}` rewrites one in place —….

## [0.356.0] — 2026-09-21
`POST /routines/{slug}/archive` now returns `external_residue`: what the routine published outside the scheduler, where it is, and who can remove it.

## [0.355.0] — 2026-09-21
Grammar.

## [0.354.3] — 2026-09-21
The action documentation tells every run that re-writing an artifact's filename updates it in place — "extend what is there instead of making….

## [0.354.2] — 2026-09-21
Classification cannot rest on prose.

## [0.354.1] — 2026-09-21
`drain_messages` reconstructs each inbox message with only the keys it knows, and `closes` was not among them — so a report sent with `closes: true`….

## [0.354.0] — 2026-09-21
Explicit opt-in, fail closed.

## [0.353.3] — 2026-09-21
Preserve deliberate-shutdown attribution across populated run homes, including colliding slugs. - Render scheduled-fire SVG titles without aborting….

## [0.353.2] — 2026-09-20
`restart.mark_deliberate_shutdown` leaves a breadcrumb on the way out so the next boot can report orphaned runs as `daemon_restart` rather than….

## [0.353.1] — 2026-09-20
The routine drain-and-exit restart worked exactly as designed on 2026-09-20 and the instance still stayed down for two hours.

## [0.353.0] — 2026-09-20
D137: util-authoring approvals now offer “approve this kind for the rest of this run”.

## [0.352.0] — 2026-09-19
The engine offers `action:write_recipe` as a run-scoped grant and tells the asking run it is allowed for this run and usable now.

## [0.351.0] — 2026-09-18
A reply that genuinely finished several rows could settle only one.

## [0.350.0] — 2026-09-17
The accounting contract gains its second half. v1 (`stopping.unaccounted`) proves a finish ADDRESSED each active condition; v1b….

## [0.349.0] — 2026-09-17
`SmartCrusher` no longer touches JSON.

## [0.348.0] — 2026-09-17
The Stats tab gains Output compression by routine: candidates, attempts, applied previews with their hit rate, estimated tokens saved, rejections,….

## [0.347.0] — 2026-09-16
A reaped run now records WHY its process is gone (F480).

## [0.346.0] — 2026-09-16
Switching a routine off is a SCHEDULE state, not a checkbox beside the schedule.

## [0.345.2] — 2026-09-15
A folded row is no longer a peer item on the Messages page. 0.345.0 gave a report the ability to take rows over; they kept appearing as their own….

## [0.345.1] — 2026-09-15
The Messages page's headline count stops summing a worklist with a feed.

## [0.345.0] — 2026-09-15
A report can TAKE OVER other reports (`supersedes`), and a sender may not open more than three parallel threads to one owner.

## [0.344.1] — 2026-09-15
Saving a routine's permissions no longer flattens its domain into the routine's own file.

## [0.344.0] — 2026-09-15
`Astra high`'s fallbacks are exactly `[Opus high]`, and `Opus high` declares none.

## [0.343.1] — 2026-09-15
A run rendered a PDF, `view_image`'d it, and was answered with a line that both said "shown to you below; look at it now" and — at its far end —….

## [0.343.0] — 2026-09-15
unknown permission-doc slugs are dropped, a junk capabilities mapping is a 422; - the mapping is RAISED to cover every held doc's `requires` and….

## [0.342.2] — 2026-09-14
`resume()` refuses on `cfg.slug in self.active`, so the routine could never be resumed or fired again — every attempt returned 409 while the….

## [0.342.1] — 2026-09-14
`recover_orphans` closed every boot-recovered run as `failed`, so a run the daemon itself killed by restarting was recorded as a failure the routine….

## [0.342.0] — 2026-09-14
`config_patch` accepts `domain: "<id>"` beside `routine: "<slug>"`.

## [0.341.2] — 2026-09-14
`PATCH /api/domains/{id}` returned the domain record and nothing about what the patch did, so a caller could not distinguish an applied key from a….

## [0.341.1] — 2026-09-14
The binding is the proxy's.

## [0.341.0] — 2026-09-14
The proxy endpoint's card lists the proxy's accounts (`GET …/proxy-accounts`): provider, label and the proxy's own status word — `token expired`, a….

## [0.340.0] — 2026-09-14
The Decisions read model is memoized per home (`web/decisions_read.py`) behind a stat fingerprint of every source that can change it: the home dir….

## [0.339.0] — 2026-09-14
A file over `fileops.READ_MAX_BYTES` (8 MiB) is refused with its actual size and the cap in the message (`12,345,678 bytes exceeds the read_file cap….

## [0.338.1] — 2026-09-13
Oversized utility capture is explicitly incomplete and parseable (F472).

## [0.338.0] — 2026-09-13
Abilities-panel responsibilities separated (F453).

## [0.337.1] — 2026-09-13
Lane progress stays current during run transitions (F479).

## [0.337.0] — 2026-09-13
Action-message timing in routine transcripts and conversation work folds (F476).

## [0.336.1] — 2026-09-13
Deleting a previously viewed attachment no longer crashes the next model request (F478, R1454).

## [0.336.0] — 2026-09-13
Pause scheduling also holds active routines after their current turn (F475).

## [0.335.0] — 2026-09-12
The run and conversation rails are ONE layout at every width that can hold a rail (D125, F456).

## [0.334.0] — 2026-09-12
The console's slowness, attributed and fixed at the cause.

## [0.333.0] — 2026-09-12
An answer no longer starts a run; "answer & run now" does, when you click it. 0.330.0 fired a run the moment a deferred question was answered.

## [0.332.0] — 2026-09-12
The daemon records its own slowness, and can be asked what it is doing.

## [0.331.0] — 2026-09-12
`browser-sessions` permission doc (`library-seed/permissions/`): the shared signed-in browser (the `chrome` sidecar at `http://172.30.7.10:9222`) was….

## [0.330.0] — 2026-09-12
The fleet-wide report trigger of 0.329.0 is reversed; a person's answer wakes a routine, a routine's report does not.

## [0.329.0] — 2026-09-12
An operator's answer now wakes the routine that asked.

## [0.328.0] — 2026-09-12
A model's fallbacks are now PICKED from the catalog, not typed.

## [0.327.0] — 2026-09-11
`GET /api/items?target=<slug>` — the other end of a report.

## [0.326.0] — 2026-09-11
A `config_patch` can name the routine it is FOR, and the apply goes there.

## [0.325.0] — 2026-09-11
A one-shot LLM call places no cache breakpoints.

## [0.324.0] — 2026-09-11
A routine can work inside a PROJECT WORKSPACE, bind-mounted at its host path.

## [0.323.0] — 2026-09-11
The digest's two directory listings are capped (`composer.DIR_LIST_MAX` = 40, newest first, with a `… and N more file(s) not listed` tail): `state/`….

## [0.321.0] — 2026-09-11
Prompt-cache health is measured, not assumed.

## [0.320.0] — 2026-09-11
Settings can select a dedicated catalog model for background context archival.

## [0.319.0] — 2026-09-10
New and existing routines and conversations without an explicit output-compression setting now use Headroom.

## [0.318.0] — 2026-09-10
Routines and conversations can select Off, Measure only, or Headroom output compression.

## [0.317.0] — 2026-09-10
Limit discovery decided which metadata API an endpoint speaks from its KIND, so every `anthropic` endpoint was assumed to publish nothing and fell….

## [0.316.1] — 2026-09-10
A `script` action refused by one of its declaration gates (a `gu` exec the docstring's `calls:` line does not name, or engine header keys written….

## [0.316.0] — 2026-09-10
Replace character-based context settings with `context_tokens` across configuration, endpoint resolution, Settings, model pickers and compaction.

## [0.315.0] — 2026-09-10
Removed the custom Claude CLI transport, session/wire code, credential configuration, legacy quota reader, and endpoint UI.

## [0.314.0] — 2026-09-10
Optional, pinned CLIProxyAPI Compose service with persistent authentication under the existing backup inventory.

## [0.313.0] — 2026-09-10
delete — removes a file, or a whole directory with `recursive: true`; a directory without it is refused, and deleting a path outside the routine's….

## [0.312.1] — 2026-09-10
`GET /api/items` memoized its five-file merge but then re-parsed the 177 KB `changelog.jsonl` a second time, unmemoized, at the handler tail — on….

## [0.312.0] — 2026-09-06
The panel listed every dependency a routine's setup resolves to, diagnosed each one precisely, and then abandoned the reader.

## [0.311.0] — 2026-09-06
a LANE (`rsched/lanes.py`, `.control/lanes.json`) — when a set of routines fires and in what order, plus its mid-chain-failure policy.

## [0.310.0] — 2026-09-05
the setup surface's "held, but its requires: are not switched on" check knew actions and utils only, so a routine holding the doc with the dial off….

## [0.309.0] — 2026-09-05
Three gaps, all pointing the same way: a feature that exists but cannot be seen or reached is not really shipped.

## [0.308.0] — 2026-09-05
Compaction's good tier hands the elided middle to a model and waits 180–600 seconds for a set of navigable files back.

## [0.307.0] — 2026-09-05
Compaction archives the elided middle losslessly to `runs/<ts>/history/` and leaves the prompt a pointer at `INDEX.md`.

## [0.306.0] — 2026-09-05
git-checkpoint (pre-action, HOLD) — the rung the design note reserves for a crisp pre-action predicate with an irreversible cost to skipping.

## [0.305.0] — 2026-09-05
The 26 curated general rules are advisory prose enforced by nothing, and the realistic failure is not a run that refuses one.

## [0.304.0] — 2026-09-05
Σlabels` is the count of holds nobody labelled — which is itself the signal that the layer is being paid for and not read.

## [0.303.0] — 2026-09-05
The routine page's recipe file-tree (`.recipe-navcol`).

## [0.301.0] — 2026-09-05
Two reported defects, both where a proxy stood in for the fact it was meant to represent.

## [0.300.0] — 2026-09-05
`actionschema.canon(action)` is now that rendering, and the documented match target: `util:fs-ops mv a b` · `shell: rm -rf build/` · `read_file….

## [0.299.0] — 2026-09-05
`compaction.ANTICIPATE_AT` (0.85).

## [0.298.1] — 2026-09-05
Found by pointing 0.298.0 at the real machine rather than by testing it.

## [0.298.0] — 2026-09-05
`MachineConfig.exclusive` makes `remote submit` take a QUEUE TICKET instead of launching.

## [0.297.0] — 2026-09-05
The generation prompts are the cause, so they changed first.

## [0.296.0] — 2026-09-05
New `endpoints/limits.py` asks each provider what its models' limits actually are — OpenRouter and Nano-GPT from their model listings, Ollama from….

## [0.295.0] — 2026-09-05
The console was rendering something else.

## [0.294.0] — 2026-09-05
A summary is now an item.

## [0.293.0] — 2026-09-05
The defect.

## [0.292.0] — 2026-09-05
The dock is styled again.

## [0.291.0] — 2026-09-05
A deleted library doc no longer comes back (and no longer gets pushed).

## [0.290.0] — 2026-09-05
F439 reopened and actually fixed.

## [0.289.1] — 2026-09-05
Chat copy buttons are now reachable on touch devices (F433).

## [0.289.0] — 2026-09-04
The console has four sidebars — the main navigation rail, the page-nav, and the run view's left and right rails — and the operator asked to drag any….

## [0.288.3] — 2026-09-04
The operator reported that picking light in the theme toggle left everything dark.

## [0.288.2] — 2026-09-04
Closures are no longer flagged.

## [0.288.1] — 2026-09-04
The run view's goal/stopping panel renders each condition as a flex row — `[mark, text, [s<n>]+note meta, requires-select, "any stage" input, ✕]` —….

## [0.288.0] — 2026-09-04
A conversation is a stream of messages, and until now the agent's reply was always implicitly an answer to the newest one.

## [0.287.1] — 2026-09-03
0.287.0 deployed, and `rsched validate` against the running instance immediately reported four routines — `ards`, `fau-grant-prep`, `nanogeofeld`,….

## [0.287.0] — 2026-09-03
Gating was fail-open.

## [0.286.1] — 2026-09-03
0.286.0's new setup-surface row detected "this routine tracks a phase" by a `## Phases` heading in main.md.

## [0.286.0] — 2026-09-03
Operator: "the workflow 'general-task' says to 'Do one small step and return its result.' … it also says 'There is NO shell' which is only true for….

## [0.285.0] — 2026-09-03
Operator: "we need a visual indicator if the daemon is draining atm." With 0.283.0 the daemon no longer blocks-drains — a requested restart waits for….

## [0.284.0] — 2026-09-03
Operator: "the default timeout must be overridable as an action parameter." A `util`/`script` action's `timeout_s` was capped at 600s by the action….

## [0.283.0] — 2026-09-03
Forking a conversation collided the branch with its parent and wedged both.

## [0.282.0] — 2026-09-03
A `manage_group verb=run` from a run with no user queued a proposal that could never be built.

## [0.281.0] — 2026-09-03
Operator: "the editable description field for routines must be multi line." The routine config page built the description editor as a single-line….

## [0.280.0] — 2026-09-02
`position: fixed` was broken on every page.

## [0.279.0] — 2026-09-02
`ftp` util — `dir` is a confinement, not a convenience.

## [0.278.0] — 2026-09-02
`status-page` rule.

## [0.277.0] — 2026-09-02
`suggest_rules_permissions` — the creation-time rules/permissions/deliberation preselection.

## [0.276.0] — 2026-09-02
Operator: the header `⑂ branch` button sat "at the top ... where we don't want it." It was the last entry point that forked at a TYPED turn number —….

## [0.275.0] — 2026-09-01
Operator: "rewind is a button at the top instead a small button at the message the user wants to rewind to." Rewinding a conversation (D69: truncate….

## [0.274.1] — 2026-09-01
`manage_group` prompt taught a retired parameter.

## [0.274.0] — 2026-08-31
Operator: "routine descriptions must be generated way more comprehensively … wrt purpose, requirements, side effects, and dependencies w other….

## [0.273.1] — 2026-08-31
A conversation trying to pair WhatsApp hit `sqlite3.OperationalError: unable to open database file` even with `fs-write` granted for the session dir….

## [0.273.0] — 2026-08-31
Operator: "can you withdraw phone notifications on decisions once they've been responded to?" A Web Push notification for a decision (`tag:….

## [0.272.1] — 2026-08-31
A stack row's entity fell into the 9px dot column.

## [0.272.0] — 2026-08-30
Advisory, never automatic.

## [0.271.0] — 2026-08-30
A grid item defaults to `min-width: auto`, so a bare `1fr` text column refuses to shrink below its longest line: the sentence was CLIPPED at the….

## [0.270.0] — 2026-08-30
The side the routine is actually in is emphasised, the other dimmed, so the row reads as "this is what you have, and this is what you would have….

## [0.269.0] — 2026-08-30
Nothing resolves a template at runtime.

## [0.268.0] — 2026-08-30
`create_routine` carries `stopping`.

## [0.267.0] — 2026-08-30
The Library tab previews what a save would break — and asks.

## [0.266.0] — 2026-08-30
`⑂` on every reply bubble.

## [0.265.0] — 2026-08-30
"Settings template" was unreachable.

## [0.264.0] — 2026-08-30
`-n auto` now means cores MINUS ONE (`pytest_xdist_auto_num_workers` in `tests/conftest.py`, so `auto` stays portable rather than pinning a literal).

## [0.263.0] — 2026-08-30
`workflows.scaffold` fits a settings template to what the creation flow already decided (`templates.suggest`) and writes only the DIFFERENCES into….

## [0.262.0] — 2026-08-30
`rsched/templates.py` + `<libraries_home>/templates/.md`.

## [0.261.0] — 2026-08-30
`recipe-authoring` / the `write_recipe` capability.

## [0.260.0] — 2026-08-30
An ability that is OFF no longer renders a requirement stack.

## [0.259.0] — 2026-08-30
`components/abilities.js` renders one card per conduct doc — which already IS an ability, prose plus the capabilities it presumes — with the whole….

## [0.258.0] — 2026-08-30
`library_impact.py` answers "who depends on this, and does this change break them?".

## [0.257.1] — 2026-08-30
`readmodels/surface.py` adds a `note` row per uncovered util and gated kind, naming the group when the group supplied it.

## [0.257.0] — 2026-08-30
`readmodels/surface.py` joins a routine's effective config (group inheritance merged) with the library's `requires:`/`expects:` and with the util….

## [0.256.1] — 2026-08-30
`fs: rw $SIGNAL_SESSION_DIR` with the variable unset daemon-side resolved to the literal string `$SIGNAL_SESSION_DIR` — harmless in the jail spec (no….

## [0.256.0] — 2026-08-30
`fs:` is now a required util-header line, on exactly the terms `net:` already had — undeclared is treated as none, and `header_problems` refuses a….

## [0.255.1] — 2026-08-29
The Decisions page no longer yanks focus out of the answer field while you type.

## [0.255.0] — 2026-08-29
New general rule `status-page` — the modular half.

## [0.254.0] — 2026-08-29
The `clarification` template routine, and the `kind: template` protection built around it.

## [0.253.0] — 2026-08-29
Routine creation asks in DECISIONS, and the workflow list is no longer closed.

## [0.252.0] — 2026-08-29
`routine-seed/` and the routine-seeding boot path.

## [0.251.1] — 2026-08-28
`docs/conversations.md` still said deletion was simply permanent, which stopped being the whole truth the moment a nightly mirror existed (0.251.0).

## [0.251.0] — 2026-08-28
`deploy/rsched-backup.{service,timer}` — the state mirror runs nightly. 0.249.0 gave the instance an incremental backup; a backup nobody runs is not….

## [0.250.0] — 2026-08-28
System word lists in the engine image (`wngerman`, `wswiss`, `wamerican`, ~10 MB).

## [0.249.0] — 2026-08-28
`deploy/backup.sh` — a recurring incremental mirror, because the tarball was never a backup.

## [0.248.1] — 2026-08-28
`deploy/bundle.sh` left five data homes out of the migration tarball — every conversation, every detached background run and all three messenger links.

## [0.248.0] — 2026-08-28
The `chrome` sidecar no longer shares the engine's network namespace, because that coupled their lifetimes. 0.247.0 used `network_mode:….

## [0.247.0] — 2026-08-28
A `chrome` sidecar: the logged-in browser the `--cdp` utils drive (`deploy/Dockerfile.chrome`, `deploy/chrome-entrypoint.sh`,….

## [0.246.1] — 2026-08-27
Endpoint credit-balance card and connection-test were 404 (regression from the F393 split).

## [0.246.0] — 2026-08-27
A script may use the utils it declares.

## [0.245.0] — 2026-08-27
A test whose engine stub stopped taking ran against PRODUCTION (F394).

## [0.244.0] — 2026-08-27
Every source file is under the ~350-line budget (F393). 26 were over; 0 are now, across 222 files.

## [0.243.0] — 2026-08-27
Stopping-condition verification — v2 of F334/D98 (`engine/verifier.py`). v1 proves a run ACCOUNTED for its goal; it cannot prove the account is true.

## [0.242.0] — 2026-08-27
Goal conditions are LOGICALLY CONNECTED.

## [0.241.0] — 2026-08-27
A config change made while a run is live becomes an in-flow message (F337).

## [0.240.0] — 2026-08-27
Approval-free intra-group notes (F335, `rsched/groupnotes.py`).

## [0.239.0] — 2026-08-27
A scheduled run can propose a routine or a group instead of being refused (F328).

## [0.238.0] — 2026-08-27
Conversation branching and hand-back (F325, built in full).

## [0.237.0] — 2026-08-27
Branches, subtasks and subroutines are ONE concept: the CHILD RUN (F338, completing it). 0.233.0 shipped the load-bearing half — the hand-back.

## [0.236.0] — 2026-08-27
The new-routine intake WALKS its clarification instead of being trusted to (F383, closing R476/R492/R494/R503). 0.230.0 shipped the mechanical half —….

## [0.235.0] — 2026-08-27
A machine share that did not mount can no longer be mistaken for an empty one (R514).

## [0.234.0] — 2026-08-27
Four converged one-shot migrations deleted (F358) — the delete-after-convergence policy running its own course, ahead of the….

## [0.233.2] — 2026-08-26
API error toasts no longer render `[object Object]` (F392, failure-visibility).

## [0.233.1] — 2026-08-26
A `config_patch` that binds a rule now applies through the Decisions page (F392).

## [0.232.0] — 2026-08-26
A 402 that prices out `max_tokens` now degrades instead of killing the turn (F362).

## [0.231.1] — 2026-08-26
The container entrypoint repairs a root-owned `~/.cache/uv`.

## [0.231.0] — 2026-08-26
The conversation composer picks general RULES and OAuth connections before the first reply (F339).

## [0.230.2] — 2026-08-26
A C toolchain in the engine image (F341, operator choice 2026-08-26).

## [0.230.1] — 2026-08-26
The widened Artifacts panel surfaced build intermediates — a regression in 0.230.0 found by checking the live instance rather than the tests:….

## [0.230.0] — 2026-08-26
Routine-scoped secrets (D103, operator decision 2026-08-26 — R497).

## [0.229.0] — 2026-08-23
The chat bubble's ⧉ copy button is mellowed (operator order 2026-08-23: "visually too strong").

## [0.228.0] — 2026-08-23
Conversations bind remote machines (D102 = both halves, operator order 2026-08-23; R475/R496: heavy conversation work — a GPU batch render — had no….

## [0.227.0] — 2026-08-23
Create and revise are separate permissions.

## [0.226.0] — 2026-08-23
`util-authoring` no longer grants deletion.

## [0.225.0] — 2026-08-22
Settings → Machines teaches the two-key distinction inline (R474, `c-20260822-174836`: a user pasted a key into "host key (pinned)" and asked whether….

## [0.224.0] — 2026-08-22
Copy-to-clipboard button on every conversation chat bubble (operator order 2026-08-22).

## [0.223.0] — 2026-08-22
A sent message with an attachment no longer renders twice in the conversation chat (R473, `c-20260822-174836`).

## [0.222.0] — 2026-08-22
`create_routine`'s draft/preview observation no longer reads as success.

## [0.221.0] — 2026-08-22
Refusal detection catches the "I'm not going to do this" decline-with-redirect shape.

## [0.220.0] — 2026-08-22
Refusal detection is the harness's own job again — the uncensored (honeypot) model no longer judges whether a reply is a refusal, it only receives….

## [0.219.0] — 2026-08-22
The conversation UI calls the refusal-referral role "uncensored", not "honeypot" (operator: "you cannot call the model 'honeypot' in the frontend.….

## [0.218.0] — 2026-08-22
The honeypot (uncensored) role is switchable mid-conversation.

## [0.217.0] — 2026-08-22
The honeypot (uncensored) model role can now be set BEFORE a conversation starts.

## [0.216.0] — 2026-08-22
A refusal wrapped in `finish(status=ok)` (or `partial`) is now caught too (live specimen `c-20260822-091412`). 0.215.0's `_intercept_refusal_finish`….

## [0.215.0] — 2026-08-22
A refusal that arrives as a `finish(status=failed)` is now caught (live specimen `c-20260822-085029`).

## [0.214.0] — 2026-08-22
Refusal clarification: the essence/remainder split (operator follow-up).

## [0.213.0] — 2026-08-22
Refusal clarification replaces whole-turn uncensored referral (operator order).

## [0.212.0] — 2026-08-22
The per-routine `subroutine` model role is retired (user order, 2026-08-22).

## [0.211.0] — 2026-08-22
Message attachments render inline in transcripts.

## [0.210.0] — 2026-08-21
Recipe length on the Stats tab (F371, user order).

## [0.209.0] — 2026-08-21
Guided create_routine: preview → confirm (D92=A).

## [0.208.0] — 2026-08-21
Semantic stopping conditions v1 (D98=A, F334 — engine + API).

## [0.207.0] — 2026-08-21
Inbox consumption timing (user order 2026-08-20, F368).

## [0.206.1] — 2026-08-19
Child dead-end cascade copy (F351, R404): a sub-workflow's reserved-capability denial no longer hints `ask_user with request:` — a child cannot file….

## [0.206.0] — 2026-08-19
A resumed run leg no longer drains the whole inbox (F359, user order 2026-08-17).

## [0.205.0] — 2026-08-16
The two-phase `split` flag is retired (D90 option A, user-selected 2026-08-16; F354, R359): a group chain now fires ONCE, member by member in order.

## [0.204.0] — 2026-08-16
Message-field keys flipped (F353, user order 2026-08-16): in EVERY message input — conversation composer, run composer, answer forms and the….

## [0.203.0] — 2026-08-15
Weekly schedules repeat on a SET of days (F347, user order 2026-08-15 — "do it like Google Calendar does custom repetitions"): the schedule editor's….

## [0.202.0] — 2026-08-15
Message fields are always multi-line, and stand alone on narrow screens (F346, user order 2026-08-15): the run view's composer and the answer form's….

## [0.201.0] — 2026-08-15
Artifact rows show their update time and are deletable from the sidebar (F336, user order 2026-08-14): the artifact list (runs, routines and….

## [0.200.2] — 2026-08-15
Finish-guard no longer flags the noun "report" (F332, R360): a routine whose DELIVERABLE is a PDF/LaTeX report could not write an honest finish….

## [0.200.1] — 2026-08-15
`util` miss now names a matching routine-local script (F330, R367): calling the `util` action with the name of a `scripts/` helper used to dead-end….

## [0.200.0] — 2026-08-14
Library filter is one autosuggest input (user order).

## [0.199.0] — 2026-08-14
Drag-to-reorder inside an expanded group (user order).

## [0.198.3] — 2026-08-13
Out-of-band library edits get committed at boot (R332/R335).

## [0.198.2] — 2026-08-13
Snake_case script names are callable (R336/R337).

## [0.198.1] — 2026-08-13
UI coverage for the group editor's Shared config section (D82) — it had shipped without one, which the house rule requires for every UI change.

## [0.198.0] — 2026-08-13
`personal-messaging` permission — `signal`, `telegram`, `whatsapp`, `zulip` plus the `chat`/`messaging` tag gate, so a new util in that class is….

## [0.197.2] — 2026-08-13
A group member recorded capability dials it never chose, shadowing its group.

## [0.197.1] — 2026-08-13
Saving a group member's permissions floored away every capability the group supplied.

## [0.197.0] — 2026-08-13
Group-level routine config, inherited by members (D82).

## [0.196.0] — 2026-08-13
Util capability gating by TAG CLASS (`util_tags:`).

## [0.195.2] — 2026-08-13
libgit holdouts folded in (F318, completing F285).

## [0.195.1] — 2026-08-13
Expired migration retired (F315).

## [0.195.0] — 2026-08-13
Group-chain health events (F316).

## [0.194.0] — 2026-08-13
Compaction events render what actually happened (F309, user report on c-20260810-213335): the transcript's compaction line knew only the flat….

## [0.193.0] — 2026-08-13
Every row of the run/conversation rail's files card is viewable and downloadable (user order, twice-asked; F311, absorbs D83/R238): new `GET….

## [0.192.1] — 2026-08-13
Script children no longer see the util library (F308): `run_script` reused `utils_lib.scoped_env`, which serves util calls too and therefore kept….

## [0.192.0] — 2026-08-13
The library repository has no settings surface (operator decision): the Settings page's Library section, `GET/PUT /api/settings/libraries` and the….

## [0.191.0] — 2026-08-12
Per-routine `scripts/` return — as tooling, not as a second interpreter (operator clarification of the 0.189.0 removal: the SYMMETRY doctrine was the….

## [0.190.0] — 2026-08-12
Test suite wall clock cut on the 4-core instance, no coverage change.

## [0.189.0] — 2026-08-12
The per-routine `procedure` feature, in full (operator reversal of the 2026-08-12 symmetry rule): the `procedure` action, `rsched/procedures.py`, the….

## [0.188.0] — 2026-08-12
Disabled routines wear an "off" tag in the table's schedule cell — the 0.187.0 row dim alone was too subtle as the only always-visible disabled marker.

## [0.187.0] — 2026-08-12
Week-strip legend retired; color identity moves to the routines (operator ask).

## [0.186.0] — 2026-08-12
Week strip: two-day zoom + scroll (operator ask — seven days across one panel width was unreadably dense).

## [0.185.0] — 2026-08-12
Week strip: scheduled groups render their real fire path (D71/R313).

## [0.184.1] — 2026-08-12
Message folder cards rendered a literal "null" for absent optional fields (an outbox/received row with no detail, a consumed message with no report….

## [0.184.0] — 2026-08-12
Full recipe/procedure symmetry, wired live (operator rule 2026-08-12; completes the 8c1bfe5/72a49c0/fab03a5 series): a routine is ONE thing with TWO….

## [0.183.0] — 2026-08-12
Two-phase group fire (F292; operator standing order R214.3b).

## [0.182.0] — 2026-08-12
D74 phases 2–4 — Messages everywhere (operator order 2026-08-05, completing 0.180.0's read model).

## [0.181.0] — 2026-08-12
Procedures run in the routine's own venv with the routine's own filesystem permissions (operator correction of 0.179.0): a procedure executes via….

## [0.180.0] — 2026-08-12
Messages read model (D74 phase 1 — operator order 2026-08-05): the four-folder per-routine view (`rsched/readmodels/messages.py`, `GET….

## [0.179.0] — 2026-08-12
Per-routine procedures (D88 phase 1, option A — user order 2026-08-10): a routine may carry `procedures/<name>.py` — PEP 723 scripts, private to the….

## [0.178.0] — 2026-08-12
One git plumbing home (F285): `libgit` now owns the single `git()` invoker, the push-hook installer (source: `deploy/post-commit`, the same file….

## [0.177.0] — 2026-08-12
Per-call model-role override on `llm` and `subtask` (D81): the optional `model` field names a ROLE (`main|subroutine|tool_call|uncensored`) — the….

## [0.176.0] — 2026-08-12
Optional secrets no longer prompt on every call (F290 engine half + R314, the "very old bug"): a `?`-declared secret (D51 — page-fetch's….

## [0.175.0] — 2026-08-12
A conversation can schedule a group (R311/R312 — direct user requirement): `manage_group create/update` take a flat `cron` field — the group….

## [0.174.0] — 2026-08-12
Wizard vocabulary retired from prose and labels (F282/D59): ~50 comments, docs passages and UI strings that described the retired standalone wizard….

## [0.173.0] — 2026-08-12
One spool mechanic (F286): the durable request-spool file IO (dir layout, atomic write, chrono naming, listing) lives once in `rsched/spool.py`;….

## [0.172.0] — 2026-08-12
Routines overview no longer renders vestigial member crons (R313): a member of a scheduled group shows `⛓ <group> — <the group's schedule>` in both….

## [0.171.0] — 2026-08-12
Continued-conversation note says the conversation continues in place (R307): the engine's follow-up boot note now tells the model that anything left….

## [0.170.0] — 2026-08-10
Summary: "✓ mark all read" (F303).

## [0.169.0] — 2026-08-10
Whole-group pause (user order 2026-08-10).

## [0.168.1] — 2026-08-09
Rules-migration completion pass for stage prose (R297, from routine-improver's sweep-32).

## [0.168.0] — 2026-08-09
A live "browser" section in the conversation right rail (D86 selected A / R262 pt2).

## [0.167.0] — 2026-08-09
`kind` is now a declared field on RoutineConfig rather than a known key pydantic dropped, so `conversation` and `template` are both readable.

## [0.166.1] — 2026-08-08
`git-sync --continue` stalled a held rebase in the engine's container: `rebase --continue` opens an EDITOR to let a human amend the replayed commit's….

## [0.166.0] — 2026-08-08
`git-sync --on-conflict hold` leaves the rebase IN PROGRESS and returns `conflicts: [{path, kind}]` — `both-modified` / `modify-delete` / `add-add`,….

## [0.165.3] — 2026-08-08
Six of ten seeded utils had drifted from the live library — revised in production by routines and never back-ported, so `util-seed/` no longer….

## [0.165.2] — 2026-08-08
Every LIBRARY workflow pattern still declared the retired rule slugs in `includes:` (`global-utils`, `ledger-discipline`) — including `converse` and….

## [0.165.1] — 2026-08-08
The `practice-library` permission doc survived the 0.164.0 deploy on the live instance.

## [0.165.0] — 2026-08-08
New bundled `library-sync` routine (disabled, daily 06:00): stage the instance's routines and redacted config into the library repo's working tree,….

## [0.164.0] — 2026-08-08
Ownership split in two.

## [0.163.0] — 2026-08-08
Web Push notifications were discarded whenever the device was asleep.

## [0.162.0] — 2026-08-08
Schema-storm fast-fail (D87-A, F297, R255).

## [0.161.0] — 2026-08-08
"Allow once" now covers secret and filesystem grants (D76, operator-selected opt A).

## [0.160.5] — 2026-08-07
Same-second mid-run edits now replay in queue order (F298).

## [0.160.4] — 2026-08-07
Plain `ask_user` questions no longer render literal `\n` (D85-A, F291, R242).

## [0.160.3] — 2026-08-07
A message sent to a finished conversation now appears immediately (F295).

## [0.160.2] — 2026-08-06
`abort_process`'s two dead parameters (F283).

## [0.160.1] — 2026-08-06
A granted write root that didn't exist yet silently vanished from the util jail (F293, R244).

## [0.160.0] — 2026-08-06
Mid-conversation folder access (D82).

## [0.159.1] — 2026-08-06
4 proven-dead CSS classes (F288).

## [0.159.0] — 2026-08-06
Editing a routine while a run is active no longer bounces with a "busy" 409 — the edit is queued and applied at run end (D78 option A, F279).

## [0.158.1] — 2026-08-06
The routines table no longer double-lists grouped routines (F281, reviewer order). 0.157.0's flat-list-stays-complete choice meant every grouped….

## [0.158.0] — 2026-08-06
The window guard (F278).

## [0.157.0] — 2026-08-06
Inline ⏸ pause / ▶ resume on every routine — card actions AND table rows.

## [0.156.0] — 2026-08-06
Item priorities (⚑).

## [0.155.0] — 2026-08-05
A day's cap on trigger-initiated fires.

## [0.154.3] — 2026-08-05
Correct the `fire_refused` (F276) justification — it was based on a wrong diagnosis.

## [0.154.2] — 2026-08-05
Summary opens on Unread by default (operator, 2026-08-05).

## [0.154.1] — 2026-08-05
A due cron fire that produces no run is now audible — F276.

## [0.154.0] — 2026-08-05
A live trigger's cooldown is editable in place.

## [0.153.0] — 2026-08-05
Terminal ack for reports.

## [0.152.1] — 2026-08-03
Conversation context overflow on small-window orchestrator models — F265, 4th recurrence (R113/R114).

## [0.152.0] — 2026-08-03
The new-conversation composer can start a conversation in admin mode (D66).

## [0.151.1] — 2026-08-02
`kindsurface.py`'s module docstring cited the wrong action-kind count (F272).

## [0.151.0] — 2026-08-02
The dashboard "this week" strip draws same-group routines on one shared row (F271).

## [0.150.0] — 2026-08-02
Rewind a conversation to a chosen turn and re-open it live (D69).

## [0.149.1] — 2026-08-02
Context overflow now enforced by trimming, not just by an earlier compaction trigger (F265, third recurrence).

## [0.149.0] — 2026-08-02
Routine group membership is now visible on the Routines page (F269, R107).

## [0.148.5] — 2026-08-02
Conversation context overflow RECURRED after 0.148.1 — the output reservation now keeps a safety margin (F265).

## [0.148.4] — 2026-08-02
A blocking approval no longer shows twice in a conversation (F264).

## [0.148.3] — 2026-08-02
Concurrent open-EventSource gauge, to diagnose the multi-run UI "freeze" (F263).

## [0.148.2] — 2026-08-02
Ordered markdown lists now enumerate sequentially in chat + finish-summary rendering (F266 / R103).

## [0.148.1] — 2026-08-02
Compaction now reserves room for the model's OUTPUT, fixing context-length overflows on small-window models (F265).

## [0.148.0] — 2026-08-01
Answering an already-resolved question settles gently instead of erroring (F259).

## [0.147.1] — 2026-08-01
pytest now names flaky reruns in the summary (`-rR` in `addopts`).

## [0.147.0] — 2026-08-01
Settings sections now share the `settingsSection` primitive (operator-selected D64 / A', the next D57 interface-quality increment).

## [0.146.0] — 2026-08-01
Admin conversation — the UI affordance + rotation runbook (operator-selected D63: 1A + 2A).

## [0.145.0] — 2026-08-01
Admin conversation — a token-gated bypass of the capability layer (operator request, D62).

## [0.144.0] — 2026-08-01
`manage_group` action — routine GROUP management from a conversation (operator request, D61).

## [0.143.0] — 2026-08-01
Conversations is now the landing page and the first nav item (operator request).

## [0.142.0] — 2026-08-01
The standalone new-routine wizard — retired (D59, operator-selected big-bang).

## [0.141.0] — 2026-08-01
Routine creation from a conversation — the `create_routine` action (D58).

## [0.140.2] — 2026-08-01
Dropped a dead pre-0.49 backwards-compat branch in the search indexer (F254).

## [0.140.1] — 2026-08-01
The routine overview (and every view's live refresh) no longer freezes with stale state after the event stream drops (F253).

## [0.140.0] — 2026-08-01
Every routine-config section now reads and looks the same as the conversation composer's settings (D57 phase 2).

## [0.139.1] — 2026-08-01
A message sent to a finished conversation while the server is restarting is now refused with a clear "not saved — resend once" notice instead of….

## [0.139.0] — 2026-08-01
The Settings page is now grouped by what you're configuring, so it teaches a cognitive model (F248).

## [0.138.0] — 2026-08-01
New-conversation setup now shows its settings as clearly-titled sections (D57 phase 1, F244).

## [0.137.1] — 2026-08-01
Dashboard list view now marks a running routine, like the card view does (F247).

## [0.137.0] — 2026-07-31
Routine groups — "Run now" arming surface (D53 Phase B, complete).

## [0.136.0] — 2026-07-31
Routine groups — sequential-fire engine (D53 Phase B, daemon half).

## [0.135.0] — 2026-07-31
Routine groups — CRUD page + nav (D53 Phase A, UI half).

## [0.134.0] — 2026-07-31
Routine groups — store + CRUD API (D53 Phase A).

## [0.133.2] — 2026-07-31
Dashboard search/sort controls were destroyed by live refreshes while routines ran (F229 — "UI non-responsive with >1 routine running").

## [0.133.0] — 2026-07-31
`util name=search args=["<keywords>"]` — keyword tool discovery (D52 Phase 3).

## [0.132.1] — 2026-07-31
Run/conversation composer input still shared the control row on narrow screens (F238 regression): the run-view message input was created with an….

## [0.132.0] — 2026-07-31
Conversations can bind OAuth connections (D55, closes R70): the Connections card — bind a Google/Notion/… account per provider so its access token is….

## [0.131.0] — 2026-07-31
Working-plan strip on the run view (D54): a run's living decomposition (`state/plan.md`) — the same store the engine already inlines into the prompt….

## [0.130.0] — 2026-07-31
Run-page composer: dropped the dead mode dropdown and gave the input its own line on narrow screens (F237, F238): the end-of-run composer had a….

## [0.129.0] — 2026-07-31
Installable PWA so decision notifications reach a phone (operator request): the console now ships a web app manifest (`static/manifest.webmanifest`,….

## [0.128.0] — 2026-07-31
End-of-run input always continues THIS run; the next-run message queue moved to the routine page (F233): the run page's end-of-run composer no longer….

## [0.125.0] — 2026-07-30
Red baseline repaired (F221): `tests/test_utils.py::_ctx` test double lacked the `read_roots`/`write_roots`/`granted_now`/`grant_args` surface that….

## [0.124.1] — 2026-07-29
`bootstrap.migrate_secret_grants` deleted after convergence (one-shot `secret_grants:` → `grants:` rename from 0.124.0): the pass ran on the….

## [0.124.0] — 2026-07-29
Unified access grants — the four-state model (`entities.py`, `engine/requests.py`, `web/grants_apply.py`; docs/traits-permissions.md): every….

## [0.123.0] — 2026-07-29
Main-thread FREEZE telemetry (`static/trace.js` + `web/api_traces.py`, F218 — instrumentation for the "UI freezes during active runs" report, N12).

## [0.122.0] — 2026-07-29
Dashboard "this week" strip: bar width is now a 5-run moving average of runtime (`static/components/weekgrid.js`, F210 — operator request).

## [0.121.0] — 2026-07-29
A 429's `Retry-After` hint is now honored before failover (`endpoints/base.py`, F220).

## [0.120.0] — 2026-07-29
Run composer: a typed draft now survives a refresh and the input clears on send (`static/views/run.js`, F215).

## [0.119.0] — 2026-07-29
Provisioned secret NAMES are surfaced to every run (`engine/capabilities.py`, D46 — operator decision, option A).

## [0.118.0] — 2026-07-29
Blocking / permission questions are no longer mirrored to Discord (`engine/decisions.py`, D48 — operator decision).

## [0.117.0] — 2026-07-29
`engine/outputs.py` — `spill()` no longer crashes the turn on a malformed context (F211). 0.116.0's spill store computed `base`/`rel_dir` (which call….

## [0.116.0] — 2026-07-26
Util output too large for its observation is saved, not destroyed (`engine/outputs.py`).

## [0.115.0] — 2026-07-26
Usenet as an opt-in capability.

## [0.114.0] — 2026-07-26
Conversations get a spine and permission to be long.

## [0.113.0] — 2026-07-26
Darknet access as an opt-in capability: one util, one permission, one container — and no engine code.

## [0.112.0] — 2026-07-26
The util sandbox now exposes operator-staged shared read-only asset dirs to every run.

## [0.111.0] — 2026-07-26
`read_file` now END-truncates an over-cap read on whole-line boundaries instead of the head+tail elision.

## [0.110.0] — 2026-07-26
Utils now run with the calling routine's own directory as their working directory (CWD).

## [0.109.0] — 2026-07-26
`report_bug` and `hand_off` are merged into ONE action, `report`.

## [0.108.1] — 2026-07-26
`conversations.attachment_note` named the `vision` util in the `[attached files — …]` block it prepends to every message carrying an attachment.

## [0.108.0] — 2026-07-26
`hand_off` — the inter-routine referral channel.

## [0.107.0] — 2026-07-25
Human message bodies render markdown (`static/components/transcript.js`).

## [0.106.0] — 2026-07-25
New read model `readmodels/items.py` merges the four scattered sources into one shape (spec: `docs/items.md`): the self-audit `report.json` (findings….

## [0.105.0] — 2026-07-25
The action surface is PROJECTED onto the kinds a run may emit (new `engine/kindsurface.py`).

## [0.104.0] — 2026-07-25
OAuth consent scopes come from `<PROVIDER>_OAUTH_SCOPES`, with no hardcoded fallback: a `scoped` provider (Google, Slack) now builds its consent….

## [0.103.0] — 2026-07-25
Run messages carry file attachments (F202, user-requested): the run page composer gains the same 📎 attach / chips / paste-to-attach affordance as….

## [0.102.0] — 2026-07-24
Abandoned wizard sessions auto-expire (D44, user-settled: 12h): listing sessions now archives any ENDED session (clarify result waiting unconsumed,….

## [0.101.0] — 2026-07-24
Wizard build failover (F197): the stage-generation pipeline re-resolves the model chain after every failed attempt, so a hard endpoint failure (the….

## [0.100.0] — 2026-07-24
Wizard live build progress (F192): the decompose pipeline reports each step (outline → main.md → stage k/N by name → traits) through a `progress`….

## [0.99.0] — 2026-07-24
write_util EDIT MODE (D42-B): `write_util` now accepts `anchor`/`replacement` (+ optional `all`) as the alternative to re-emitting the complete….

## [0.98.0] — 2026-07-24
Decompose pipeline (D41 redesign): routine generation no longer rides ONE huge JSON completion.

## [0.97.0] — 2026-07-24
Wizard decompose reliability (D41): the stage-generation LLM call now retries once, gets real output headroom (`max_tokens ≥ 32000`) and a 300 s….

## [0.96.1] — 2026-07-24
Anti-batching override in the run prompt (F180): the claude-CLI harness advertises "call multiple tools in one reply", but with `--output-format….

## [0.96.0] — 2026-07-24
`sym diff` — scoped, symbol-addressed change review: old side from any git ref (default HEAD), new side from disk, diff cut per symbol with class….

## [0.95.0] — 2026-07-23
Self-audit efficiency/effectiveness batch (evidence-driven — profiling showed 55–77% of run observation volume was shell-based code exploration): the….

## [0.94.0] — 2026-07-23
Self-audit works lookup-first from a generated codemap: the new library `codemap` util (stdlib-only, offline, seconds) derives a compact map of this….

## [0.93.0] — 2026-07-23
The permissions & practice-module panels explain themselves: every capability row (gated actions, reserved utils, run-history depth, subtask-pattern….

## [0.92.0] — 2026-07-23
Run view: the revise-recipe affordance is now an "editable recipe" checkbox right next to the composer input on finished routine runs (off by….

## [0.91.0] — 2026-07-23
Run page: a dedicated ✎ revise recipe button beside the composer input on terminal routine runs — preselects the revise mode; the placeholder states….

## [0.90.1] — 2026-07-23
Real approvals were recorded as DECLINED (F161): util-approval answers arrive as free text (Discord mirrors blocking questions to the phone), and….

## [0.90.0] — 2026-07-23
Local Claude-subscription usage widget (D33, option A): the claude-cli endpoint card in Settings → endpoints now shows the LOCAL token tally for the….

## [0.89.0] — 2026-07-23
Global scheduling pause (D34, option A): a dashboard button drops a durable `.control/pause.request` sentinel (sibling of the restart sentinel).

## [0.88.2] — 2026-07-23
The system prompt no longer lies to the routine-improver about its own recipe (`engine/composer.py`): the ownership paragraph stated "Your own recipe….

## [0.88.1] — 2026-07-23
A boot-time mount-key sweep can no longer kill scheduling (`machines.py`): `sweep_stale_mount_keys()` runs on `run_forever`'s boot path before the….

## [0.88.0] — 2026-07-23
Routine overview hero (`static/views/routine-overview.js`): a compact instrument band at the top of every routine page — live/idle/disabled status….

## [0.87.5] — 2026-07-23
A capabilities mapping is floored from birth, on every path: the conversation default-creation path and the `/conversations/defaults` preview now run….

## [0.87.4] — 2026-07-23
A broken connection/machine binding no longer fails silently: the resolver warnings the executor used to discard ("key X unset", "machine Y not in….

## [0.87.3] — 2026-07-23
One test double / helper where five-to-seven copies were: `FakeRunner` (scheduler, triggers, schedule-once, hooks; the detached suite subclasses it….

## [0.87.2] — 2026-07-23
Mega-view splits (the ≤~350-line rule, applied to the frontend): `settings.js` 874 → 99 lines (eight `settings-.js` section modules — github,….

## [0.87.1] — 2026-07-23
Shared components: `components/referchip.js` (the refer-to chip the run view and chat composer both mount — one convention, one implementation) and….

## [0.87.0] — 2026-07-23
`engine/executor.py` (595) → executor (dispatch, util runner, llm subcall) + `engine/fileops.py` (read/view/write/edit, memory actions, read_trait,….

## [0.86.2] — 2026-07-23
Dead code removed (each verified caller-less): `grants._PERMISSIVENESS` + `grants.unsatisfied_requires`, `lint_materialized_text` + `PLACEHOLDER_RE`,….

## [0.86.1] — 2026-07-23
Badge refreshes are coalesced.

## [0.86.0] — 2026-07-23
`rsched/registry.py` — the catalog/run-index read-model moves OUT of `daemon/` (every importer rewritten; nothing re-exported).

## [0.85.4] — 2026-07-23
Remote machines: `known_hosts` parsing anchors on the key-TYPE token — a `.pub` paste used to pin `base64 comment` and every connection then refused….

## [0.85.3] — 2026-07-23
A resumed parent gets its children's results back.

## [0.85.2] — 2026-07-22
SSE tickets are SSE-only credentials.

## [0.85.0] — 2026-07-22
The average is computed in the browser from the `recent_runs` window each card already carries (`avgRuntime` over `elapsed_s`); no API change.

## [0.84.0] — 2026-07-22
Machine catalog (`config.yaml` `machines:`, `ServerConfig.machines` → `MachineConfig`): an operator-only, instance-wide list of SSH hosts (host /….

## [0.83.2] — 2026-07-22
Engine ([autocommit.py](src/rsched/engine/autocommit.py), [recipes.py](src/rsched/recipes.py)): the run-end autocommit, the pre-run recipe snapshot….

## [0.83.1] — 2026-07-22
Shared library repo commits are serialized and scoped ([libgit.py](src/rsched/libgit.py)): the three duplicate `git_commit` helpers….

## [0.83.0] — 2026-07-22
`engine/revise.py` + the loop ([loop.py](src/rsched/engine/loop.py:102)): a marker the `/revise` endpoint drops in the run dir is read ONCE at loop….

## [0.82.0] — 2026-07-22
Editing a claude-cli endpoint no longer wipes its `credentials_env` / `key_env_file`.

## [0.81.0] — 2026-07-21
Practice modules are changeable after creation — a Practice modules panel on the routine page and in the conversation header adds or drops traits on….

## [0.80.0] — 2026-07-21
A curated practice-trait set in the library — eleven new opt-in traits distilled from Anthropic's prompt-engineering guidance, the Claude Code….

## [0.79.1] — 2026-07-21
A finish→reopen no longer loses the pre-finish leg's util histogram and counters in `status.json` (F140 — completes the F131/F132 fix).

## [0.79.0] — 2026-07-20
Settings → Secrets: manage multi-entry (JSON-map) secrets one entry at a time.

## [0.78.4] — 2026-07-20
Settings → Secrets now shows each needed secret's FORMAT.

## [0.78.3] — 2026-07-20
Settings → Secrets no longer lists OAuth connection tokens (e.g.

## [0.78.2] — 2026-07-20
The "declare the credential env vars you read" util gate had a blind spot (`utils_lib. _secrets_read`): it caught direct literals….

## [0.78.1] — 2026-07-20
Settings → Connections: each provider row now links straight to where you create its OAuth app ("create app ↗" → the provider's dev console: Notion….

## [0.78.0] — 2026-07-20
A sticky side table-of-contents on long pages (`static/components/toc.js`): on wide viewports a fixed rail parks in the right margin (mirroring the….

## [0.77.1] — 2026-07-20
Settings → Connections: the OAuth base-URL field was mislabeled "Redirect URL", which invited pasting the full `…/oauth/callback` (doubling the path).

## [0.77.0] — 2026-07-20
OAuth connections (docs/oauth-connections.md): connect an external service account (Notion first) via OAuth in the web UI, and a routine acts on its….

## [0.76.3] — 2026-07-20
`ruff check` and `mypy` now run inside pytest (`tests/test_quality.py`), so the one gate the engine actually enforces covers them.

## [0.76.2] — 2026-07-20
F97, actually fixed — the util-stats snapshot dir was never writable in the container, and the four-release chase (0.68.0–0.68.3) diagnosed the wrong….

## [0.76.1] — 2026-07-20
A resumed run reset its per-run telemetry counters to the resumed leg's own tally (self-audit F131/F132; bug report from routine-improver 2026-07-20).

## [0.76.0] — 2026-07-19
The `remove_util` action permission reverted to unchecked on every "Save permissions" (self-audit F130; operator bug report filed from….

## [0.75.0] — 2026-07-19
Reject an ok-finish that CLAIMS a high-signal action the run never took (reviewer AUDIT decision D31=B; self-audit finding F127).

## [0.74.1] — 2026-07-19
Auto-rerun the flaky Playwright UI suite (reviewer AUDIT decision D30=A).

## [0.74.0] — 2026-07-19
`report_bug` — an ungated, default-on "report potential bugs" action for EVERY routine (reviewer AUDIT decision D29=A).

## [0.73.0] — 2026-07-19
Decision answers now sync to the Audit page too (reviewer AUDIT note: "responses to decisions are still not synced everywhere the decision surfaces").

## [0.72.1] — 2026-07-19
Conversation title/tags now use the conversation's OWN model, not the system model.

## [0.72.0] — 2026-07-19
Schedule-once UI card (D28) — the frontend for the 0.71.0 one-shot backend.

## [0.71.0] — 2026-07-19
`schedule_run` action + `scheduling` permission — one-shot future runs (D27).

## [0.70.1] — 2026-07-19
New-routine draft field no longer refills with the last routine's task (F110).

## [0.70.0] — 2026-07-19
`remove_util` action — routine-executable util curation (D25).

## [0.69.1] — 2026-07-18
Audit page now renders the report's own markdown (F105).

## [0.69.0] — 2026-07-18
New "Summary" tab — each routine's latest finish message in one glance surface.

## [0.68.3] — 2026-07-18
util-stats snapshot failure was silent — the ACTUAL F97 root cause is a filesystem permission, not a `util_stats()` raise.

## [0.68.2] — 2026-07-18
util-stats snapshot STILL never materialized — the real F97 root cause. 0.68.1 only guarded a corrupt transcript, but the snapshot dir….

## [0.68.1] — 2026-07-17
util-stats snapshot no longer silently disappears when one transcript is corrupt (F97).

## [0.68.0] — 2026-07-17
Persisted util-stats snapshot — one source of truth for the Stats tab and routines (F97).

## [0.67.4] — 2026-07-17
Run-page question form now updates when a run re-asks within the same phase (F93).

## [0.67.3] — 2026-07-17
Settings → LLM endpoints: the system-model description now states its role-fallback behaviour.

## [0.67.2] — 2026-07-17
A conversation now sees its own task in the system prompt.

## [0.67.1] — 2026-07-17
Dashboard routine card no longer counts snoozed questions as open.

## [0.67.0] — 2026-07-17
The `meta` tag is now a plain tag — no special-casing.

## [0.66.1] — 2026-07-17
`rsched lint` works under the util sandbox.

## [0.66.0] — 2026-07-17
Outcome-gated self-improvement: recipe-version health + one-click roll-back.

## [0.65.0] — 2026-07-17
Per-model output `max_tokens` in the catalog.

## [0.64.0] — 2026-07-17
Instance-wide full-text search.

## [0.63.0] — 2026-07-17
Util-subprocess sandbox (Landlock).

## [0.62.0] — 2026-07-17
Event-driven routine triggers (webhook path).

## [0.61.0] — 2026-07-17
Run-history heartbeat strip on the dashboard.

## [0.60.0] — 2026-07-17
⚙ capabilities & budgets on the new-conversation composer.

## [0.59.0] — 2026-07-16
The run page is the whole new-routine setup surface (D11 UI half, completing the wizard unification).

## [0.58.1] — 2026-07-16
Run-page messages to a live clarify session now reach it (self-audit D13=B follow-up).

## [0.58.0] — 2026-07-16
Clarify sessions are now REAL runs of the `clarification` routine (self-audit D13=B, first slice).

## [0.57.2] — 2026-07-16
Decision-card option buttons no longer overflow right on narrow screens (self-audit F80).

## [0.57.1] — 2026-07-16
Test suite: 3× faster, +12 behavior tests, coverage 84.8% → 88% (user order).

## [0.57.0] — 2026-07-16
The note channel (user order): any action may carry an optional `note` — 1-3 SELF-CONTAINED lines worth keeping beyond the context window (a….

## [0.56.1] — 2026-07-16
`api_wizard.py` split into a three-module wizard package (F63 budget).

## [0.56.0] — 2026-07-16
`tuning.yaml` — the deliberation carve-out redesigned away (user order, same-day design review of 0.55.0): `deliberation` was behavior mis-filed in….

## [0.55.0] — 2026-07-16
The deliberation slider (user order): a per-routine/per-conversation knob over how much of the model's thinking lands ON PAPER — the persistent prose….

## [0.54.1] — 2026-07-16
Flaky `test_dialog_reply_` decisions tests (recurring F71).

## [0.54.0] — 2026-07-16
"Refer to" on every message (the messenger reply analog).

## [0.53.0] — 2026-07-16
Clarification template routine (audit decision D10).

## [0.52.0] — 2026-07-16
A self-restart no longer kills an in-flight routine clarification.

## [0.51.0] — 2026-07-16
Nano-GPT endpoint cards show the account balance like OpenRouter ones (user order): the credits route now sniffs the provider from `base_url` —….

## [0.50.2] — 2026-07-16
`server_tz()` consults `/etc/timezone` before the `/etc/localtime` symlink: Docker bind-mounts through the image's symlink (stale NAME over correct….

## [0.50.1] — 2026-07-16
Conversations and detached background runs now survive container recreation: the compose file was missing bind mounts for `~/conversations` and….

## [0.50.0] — 2026-07-16
write_file overwrites must be grounded (the Claude-Code-style read-before-write rule, scoped to where it matters): overwriting an existing file….

## [0.49.1] — 2026-07-16
`steps/` → `stages/` everywhere — one module-dir convention.

## [0.49.0] — 2026-07-16
The stage modules ARE the state graph — nothing inferred from prose.

## [0.48.1] — 2026-07-16
Full-repo `ruff check` is green again: the seed trees are now excluded from lint (`extend-exclude = ["library-seed", "util-seed"]` with the reasons….

## [0.48.0] — 2026-07-16
File-activity rail card (user order): the run view and the conversation view now show which files a run read / wrote / edited — per-path counts….

## [0.47.0] — 2026-07-16
Conversations view adopts the run page's layout (user order): the chat owns the full 1180px main column; the conversation list parks in a LEFT margin….

## [0.46.1] — 2026-07-16
Conversations view: `mdInline` was used but never imported.

## [0.46.0] — 2026-07-16
A slash command keeps the speaking turn with the user — it never hands the turn to the model.

## [0.45.1] — 2026-07-16
Command autocomplete was unreadable: the dropdown referenced a CSS token that doesn't exist (`--panel`), rendering transparent over the chat.

## [0.45.0] — 2026-07-16
Chat slash commands — the user can run the same actions and utils as the assistant.

## [0.44.0] — 2026-07-16
Library items are deletable, not just editable: traits and global utils gain a delete button in their editors (themed confirm, committed to the….

## [0.43.0] — 2026-07-15
The state-graph rail is an instrument panel: every `assistant_action` transcript event now carries the phase that was active while it was produced,….

## [0.42.0] — 2026-07-15
The bearer token no longer rides SSE query strings (where it leaked into access logs).

## [0.41.0] — 2026-07-15
Decisions page is a grouped inbox: the priority view renders sections — Blocking (a run is waiting on you) → Deferred → Meta → Settled (answered,….

## [0.40.0] — 2026-07-15
Run view: one message input with an explicit mode selector replacing the shifting two-button arrangement.

## [0.39.0] — 2026-07-15
Routine page saves in place — no full-page reload anywhere.

## [0.38.0] — 2026-07-15
One shared answer form (`components/answerform.js`) replaces the six hand-rolled copies (Decisions page, run view, conversation panel, wizard,….

## [0.37.0] — 2026-07-15
Every native `confirm()`/`prompt()` replaced with themed dialogs (`components/dialog.js` — the token gate's overlay language, keyboard-first: Enter….

## [0.36.0] — 2026-07-15
Uncensored-referral audit: every referral — a turn the main model refused that the `uncensored` model answered (turn loop), or an `llm` call the tool….

## [0.35.0] — 2026-07-15
Monthly spend aggregation — answers "what does this routine cost me and is it growing": the workflow-usage stream now records each finished….

## [0.34.0] — 2026-07-15
Decision lifecycle on the Decisions page — fields on the ONE record shape, not a new record type.

## [0.33.0] — 2026-07-15
Policy gates as tests (`tests/test_policy.py`, wired into pre-commit): (1) the delete-after-convergence rule is machine-checked — one-shot migration….

## [0.32.0] — 2026-07-15
`engine/loop.py` and `engine/composer.py` split under the ≤~350-line standard, behavior-preserving (every prompt string byte-identical;….

## [0.31.0] — 2026-07-15
Browser UI test harness (`tests/ui/`): Playwright drives the REAL console — the FastAPI app + static frontend served by uvicorn on an ephemeral port….

## [0.30.0] — 2026-07-15
Child-task process-model decision record (docs/subtasks.md § Process model): evaluated migrating `spawn`/`subtask` threads onto the….

## [0.29.0] — 2026-07-15
One outbound notification seam (`rsched/notify.py`).

## [0.28.0] — 2026-07-15
Step modules are now "stage modules" (`stages/`).

## [0.27.0] — 2026-07-15
Per-model attributes moved off endpoints into a named model catalog.

## [0.26.0] — 2026-07-15
Detached background tasks — long fire-and-forget in conversations (`detach`).

## [0.25.0] — 2026-07-15
Sequential subtasks — recursive task decomposition as a first-class concept.

## [0.23.0] — 2026-07-15
Recompile no longer silently reverts routine hand-edits (the "rematerialization" bug).

## [0.22.0] — 2026-07-15
The graceful self-restart now DRAINS in-flight new-routine wizard builds instead of only cleaning up their fallout (complements 0.20.1's boot-time….

## [0.21.0] — 2026-07-15
Refusal referral now covers the main orchestrator loop and subroutine loops (extends the 0.20.0 `llm`-tool-call referral; AUDIT decision D8 → C).

## [0.20.1] — 2026-07-15
Wizard builds orphaned by a server restart/crash no longer hang forever.

## [0.20.0] — 2026-07-15
Optional `uncensored` model role + refusal referral for the `llm` tool-call.

## [0.19.0] — 2026-07-15
Run timestamps are now unambiguously UTC end-to-end — the ~2h clock skew is gone.

## [0.18.0] — 2026-07-15
Two conversation budgets, settable before the conversation starts.

## [0.17.0] — 2026-07-15
Conversation state diagram now lights the current state.

## [0.16.0] — 2026-07-14
Cost budget: a `-1`-capable `max_cost` whole-dollar cap, enforced in `budget_violation`/`budget_warning`/`child_budgets`, reported in `status.json`,….

## [0.15.0] — 2026-07-14
Playbooks: a one-shot playbook library — the save/use-instruction analog for conversations (distil a conversation into a reusable, parameterized….

## [0.14.1] — 2026-07-14
claude-cli pairs stream-json input with stream-json output for image turns (`3f89cf5`).

## [0.14.0] — 2026-07-14
Native multimodal input: a `view_image` action, a per-endpoint capability flag, and a `vision`-util fallback for text-only main models — image/PDF….

## [0.13.1] — 2026-07-14
LLM task manager orphans in-flight children when their process closes, instead of leaking them (`f202e2c`).

## [0.13.0 and earlier] — 2026-07-08 … 2026-07-14 — Initial development
Core engine: the action schema + schema guard, the turn loop, executor, composer, transcript, and inbox; a fabrication guard (a finish before any….
