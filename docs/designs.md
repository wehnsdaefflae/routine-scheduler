# Designs not yet built

Specs for work that is **decided but unbuilt**. Each entry states the problem from real
evidence, the shape, and the FIRST increment — enough that whoever picks it up is not
re-deriving it. An entry is deleted the moment it ships (its narration moves to the
subsystem doc it belongs to); an entry that stops being wanted is deleted too. Nothing here
describes current behaviour, so nothing here is a reference for how the system works today.

**Who builds from this file.** The `scheduler-builder` routine reads every `## ` heading here at
orient as one of the sources of its queue and deletes the entry in the commit that ships it.
`self-audit`, which keeps the item ledger, gives each entry an `in_progress` decision row whose
detail names this file as where it was decided, so the Items page can see it. Adding an entry
here is therefore an order, not a note: write one only for work that is actually decided, and
delete one the moment it stops being wanted.
From 2026-08-26 to 2026-09-22 this file had no reader at all: five entries accumulated and
none was built, the oldest waiting thirty-three days through roughly a hundred and eighty
releases, because the builder's queue could not see it.

Started 2026-08-26 against 0.230.x, on the operator's order to clear the queued design
backlog. An entry headed by an item id answers that Messages-page finding; an entry
decided in conversation before any finding exists says so and carries none.

---

## D118 — background actions: every action runnable in the background (decided 2026-09-04)

Operator: *"okay if it's too big for now then you plan the full feature and its implementation."* Build it in the phases below, each test-gated.

### The problem

A run — and a conversation is a run resumed in place each reply — advances **one action per turn**,
and every action is dispatched **synchronously**: the loop calls `actionroute.dispatch_action`, waits
for the observation, appends it, and only then takes the next turn
(`engine/loop.py`, the `obs = actionroute.dispatch_action(self, action, ctx)` line). That is the
right model for a scheduled routine — nobody is watching it work — but in a **conversation** a slow
action freezes the human: a `page-fetch` of a heavy site, a long `llm` subcall, a `util` that scrapes
for two minutes, a `pytest-run` that takes ten. The user sits and waits, unable to say anything that
becomes a turn until the observation lands (a mid-work message is only *injected*, picked up at the
next turn — after the slow action finishes).

We already background exactly ONE thing: `detach` (see `docs/background-tasks.md`,
`daemon/detached.py`, `daemon/detached_delivery.py`). But detach is heavy and coarse — it spawns a
**whole child RUN** as its own OS process, with its own budget and fresh context, for a big
self-contained job, and delivers a *finish summary* back. You cannot background a single `util` call
and keep working in the same context.

**D118 asks for the general case:** let the agent mark *any* (safe) action to run in the background,
return the turn immediately, keep the conversation live, and deliver the observation back when it is
ready.

### Current mechanics this builds on (all real today)

- **Synchronous dispatch.** `engine/actionroute.py::dispatch_action(loop, action, ctx)` runs the
  action and returns the observation dict; `engine/loop.py` writes it as an `observation` transcript
  event and loops. One in-flight action at a time.
- **Async result delivery already exists for children.** `engine/loop.py` calls
  `announce_finished_subruns(self)` at the top of each turn; `engine/subruns.py` /
  `engine/obs_children.py` track spawned/subtask children and surface their completion as an
  observation the agent reads on a later turn. This is the exact shape a backgrounded action needs:
  *start now, collect later, announce at a turn boundary.*
- **Detached delivery into a conversation.** `daemon/detached.py` runs a detached unit as its own
  process under `background_home` (a `ServerConfig` field) and `daemon/detached_delivery.py` delivers
  its result back into the originating conversation as a message. The delivery-into-a-live-thread
  plumbing is done; D118 reuses it for finer-grained units.
- **The transcript vocabulary.** `EVENT_TYPES` (`engine/transcript.py`) already has
  `subrun_start`/`subrun_end`; a backgrounded action fits the same start/observation pair.
- **Reply targeting (D117, shipped 0.288.0).** `finish.reply_to` lets a reply name WHICH earlier
  message it answers. Once results arrive out of order (below), that legibility stops being a nicety
  and becomes necessary — D117 is the deliberate precursor.

### The proposed model

1. **A `background: true` flag** on an action (schema field on the flat `ACTION_SCHEMA`,
   `engine/actionschema.py`; allowed per-kind in `KIND_FIELDS`, `engine/actions.py`). Only for kinds
   that are safe to defer (see the safety matrix). The agent sets it when it wants to keep talking
   while the work runs.
2. **Non-blocking dispatch.** For a backgrounded action, `dispatch_action` hands the work to a
   background worker (reusing the detached-process machinery, sized to a single action rather than a
   whole run) and **returns immediately** with a *started* observation: a handle id, the kind, and a
   one-line "running in background" note. The turn ends; the loop continues; the conversation is live.
3. **Deferred observation.** When the background action completes, its real observation is queued and
   **announced at the next turn boundary**, exactly like `announce_finished_subruns` — appended as an
   `observation` event tagged with its handle so the transcript stays coherent. The user is notified
   (a pending→done indicator in the chat, mirroring the subrun/detached UI).
4. **The conversation keeps its speaking turn.** Because the started-observation ends the turn, the
   user can send messages that DO become turns while the work runs; the agent interleaves them with
   background completions. `finish.reply_to` (D117) makes an out-of-order reply legible ("↩ re your
   scrape request: 42 hits").

### The hard part: which actions may be backgrounded

The one-action-per-turn contract keeps state changes ordered. Backgrounding breaks that ordering, so
the safety matrix is the crux, not the plumbing:

| Class | Examples | Backgroundable? |
|---|---|---|
| Pure reads / external fetches | `util` (scrape/search), `page-fetch`, `llm`, `read_file`, `pytest-run` | **Yes** — no shared-state mutation; the observation is the only effect |
| Local state mutations | `write_file`, `edit_file`, `write_util`, `memory_write` | **No (phase 1)** — a later synchronous action can read stale state; ordering hazard |
| Control / lifecycle | `finish`, `ask_user`, `report`, `spawn`, `subtask`, `wait`, `kill` | **No** — already async (children) or must be synchronous (finish/ask) |

Phase 1 backgrounds only the read/fetch class — the ones that actually make a human wait — and leaves
mutations synchronous. Backgrounding mutations needs a dependency/ordering model (phase 3) and is its
own decision.

### Open decisions to settle before/while building (surface as their own D-items)

- **Concurrency cap.** How many background actions per conversation at once (a small N, e.g. 3)? A
  cap plus back-pressure, or unbounded?
- **Budget accounting.** A backgrounded `util` costs no model tokens but consumes wall-clock and a
  worker slot; a backgrounded `llm` costs tokens. Where do those book against the per-reply budget?
- **Cancellation.** Does the user/agent get a `kill`-equivalent for a background action? (Reuse
  `kill n`.)
- **Failure delivery.** A background action that errors delivers its error observation the same way —
  confirm it never silently vanishes (the `failure-visibility` rule).
- **Does this touch the "one action per turn" contract?** The started-observation preserves it (one
  action starts, one observation returns — just deferred). Confirm CLAUDE.md wording still holds;
  if it must change, that is a contract decision, not a self-evident edit.
- **Routines vs conversations.** Backgrounding only helps where a human waits. Consider gating the
  `background` flag to conversation runs (like `reply_to`), or allowing it for routines that spawn
  many independent reads.

### Implementation plan (phased, each test-gated)

- **Phase 0 — spec.** This document. *(done)*
- **Phase 1 — read/fetch backgrounding, happy path.** `background` schema field +
  `KIND_FIELDS` (read/fetch kinds only); `dispatch_action` routes a flagged action to a single-action
  background worker built on `daemon/detached.py`; a *started* observation returns immediately; a
  completion queue + `announce_*` delivers the real observation at the next turn. Tests: a flagged
  `util`/`llm` returns a started-observation same turn, the real observation lands on a later turn,
  transcript stays coherent (`test_loop.py`, `test_actions.py`).
- **Phase 2 — conversation UX.** Chat shows a "⏳ running in background" chip that resolves to the
  result; the user can send turns meanwhile; completion announced. Tests: `tests/ui/` flow — start a
  background action, send a message, see both resolve in order. Wire the pending indicator like the
  subrun fold.
- **Phase 3 — mutation ordering.** A dependency rule (or explicit barrier) so a backgrounded mutation
  cannot be read stale; only then widen the safety matrix. Its own decision item.
- **Phase 4 — ergonomics.** Concurrency cap, cancellation via `kill`, budget accounting, and the
  agent naming which background result a reply addresses (D117 `reply_to`).

### Why not just use `detach`?

`detach` is the right tool for a *big, self-contained* job that deserves its own run, budget and
context (a bulk scrape, a slow build). D118 is the opposite end: keep the SAME context and just not
block on one slow step. Both share the delivery-into-a-live-conversation plumbing; D118 adds a
lightweight, same-context unit of work on top of it. Keep both — they answer different needs.

---

## The outcome label for a rule assist's hold (decided 2026-09-22)

**Decided in the review that shipped 0.365.0; no finding.** The last of the six seams that
review named but did not finish; the other five — the `engine/inbox.py` split, the last
hand-rolled `msg-*` writers and the single-writer scan, the fourth collected-children
renderer, the two daemon-side inbox predicates, the shared-store notes drain inside the digest
builder, and the badge and push counting no proposals — have shipped and are narrated in
docs/messages.md, docs/triggers.md, docs/run-gates.md, docs/lanes-tags.md and
docs/notifications.md.

A pre-action rule assist HOLDS a run and cannot be labelled: the one `hold`-payload assist in
the library stood at 72 turns across three routines with no way to tell a useful hold from a
false positive, which is the one number that decides whether a trigger keeps a turn-costing
rung. The four coupled parts — `remind_feedback` routed by id shape, `state/assists.json`
growing the reminder's stat fields (with a one-shot migration), the health read model's four
columns, and the `assist_hold` observation asking for the label — are specified in
docs/rule-assists.md, "What is still deferred".

**First increment.** All four together: until every one lands the label has nowhere to go.

---

## A ⚑ priority flag on handed-over work reaches the builder (decided 2026-10-01, in conversation)

**Decided in conversation 2026-10-01; no finding.** The operator split self-audit into maintenance
(`self-audit`) and feature development (`scheduler-builder`) and asked for no overlap between them.
This is the one place the code still assumes the old shape.

**Problem.** `priorities.owned_priority_items` resolves the owner of every flagged `F<n>`/`D<n>` to
`self-audit` (`SELF_AUDIT_SLUG`), because every such row lives in self-audit's `report.json`. Since
the split, a row self-audit has handed over is the builder's work: its `detail` carries the line
`Handed to scheduler-builder <date>: …` and its status is `in_progress`. So a ⚑ the operator sets
on, say, a decision being built lands in self-audit's state digest — the routine that may not build
it — and never in the builder's. The operator's "work this first" signal reaches the wrong routine
for exactly the items it is most likely to be set on.

**Shape.** Ownership of a flagged `F`/`D` is read from its row at read time, never stored (the
module's existing rule): `scheduler-builder` when the row's `detail` contains the hand-over line
AND its status is `in_progress`; `self-audit` otherwise (an `addressed` row back with the ledger
keeper, so a flag on finished work still reaches someone who can clear or answer it). `R<n>`
resolution is unchanged — a `DECIDED:` report already targets the builder. One module owns the
marker: a `HANDED_TO_BUILDER` constant beside `SELF_AUDIT_SLUG` (and a `BUILDER_SLUG`), so the
recipes' wording and the resolver cannot drift apart silently — a test pins the constant to the
line self-audit's `write-report` stage writes. `readmodels/items.py` mirrors the resolver (its
docstring says the two must agree), so the Messages page names the same owner the digest does.

**First increment.** `owned_priority_items` reads `report.json` for BOTH slugs, resolves each
flagged `F`/`D` by the rule above, and returns it to exactly one of them; the module docstring and
the priorities passage in `docs/items.md` say the new rule. Red-first tests with a fixture
report.json: a flagged handed-over `D` appears in `digest_section(…, "scheduler-builder")` and NOT
in self-audit's; a flagged un-handed `F` appears only in self-audit's; an `addressed` handed-over
row returns to self-audit.

---

# Handed over from the 0.371.0–0.372.2 review (2026-10-01)

Everything below was PROPOSED by the review of 0.371.0 and the work that built 0.372.0–0.372.2,
and not built there. The operator's orders (2026-10-01): *"the proposed changes all need to go to
self audit or the feature development routine"*, and *"make sure your proposed changes end up with
self audit after the next deploy"*. So each is an entry here — `self-audit` gives it a decision
row, `scheduler-builder` builds it. Entries headed **Decide:** carry a choice that is the
operator's: their FIRST increment is to put the options to the operator on the Decisions page
(`ask_user`, the options and the review's recommendation verbatim) and then build what was chosen —
that ask is the order, not a reason to wait. Ids in parentheses (E1.6, C1.2, …) are the review's
own notes, kept so the trail can be followed.

---

## An empty `tools:` list in a workflow allows every action (E1.6)

**Problem.** `engine/loopsetup.py`: `loop.allowed_tools = set(allowed_tools) | {"finish"} if
allowed_tools else None` — an empty list is falsy, so a workflow declaring `tools: []` (nothing but
the always-kinds) gets `None`, which means EVERY kind. A restriction fails open.

**Shape.** `is not None`; check that `kindsurface.effective_kinds` and the linter's
`_tools_problems` read `[]` the same way.

**First increment.** The change, with a red-first test: a workflow with `tools: []` refuses
`write_file` and allows `finish` and `report`.

---

## The finish's claim guard counts held and refused actions as taken (E4.3)

**Problem.** `finishgate` hands `unbacked_action_claims` `{r["kind"] for r in loop.turn_records}` —
every action that landed a record, including one a reminder or rule HELD (it did not run,
`engine/hold.py`) and one the reserved finish turn refused. A summary saying "reported the outage"
passes on a `report` that was held and never re-emitted. Separately, `loop.assist_undo_points` and
`loop.reminder_owed` are set by their layers' `configure` but not declared on `EngineLoop`.

**Shape.** "Taken" is what EXECUTED: the predicate `boot` already uses for `executed_actions`
(not rejected, not `hold.is_hold`). Declare the two attributes.

**First increment.** Confirm `turn_records` holds held and refused actions; red-first test: a run
whose only `report` was held, finishing `ok` with "reported …" in its summary, is deferred.

---

## A live budget raise withdraws a spent reserved finish turn (E1.4)

**Problem.** The first budget violation grants a reserved finish turn (schema narrowed to
`finish`). Budgets are LIVE config (`configflow`), adopted at a turn boundary — but a run whose
operator raises the spent budget still stands in its reserved turn and can only finish.

**Shape.** Adopt a pending live config change BEFORE the budget check at the boundary; when the
violated resource is back within its limit, clear `_finish_reserved`, restore the full schema, and
say so in one engine note. A non-finish the reserved turn refused gets its observation.

**First increment.** Red-first ScriptedEndpoint test: budget spent → reserved turn → control.json
raises `turns` → the next turn may `write_file` and the run continues.

---

## A resumed leg keeps two more counters, and a replayed observation keeps its tails

**Problem.** (a) `loop.failures` (the "failed twice this run" count behind the repair hint) and
`_schema_off` ("schema off for the rest of the run") reset on every leg, so after a resume a call's
second failure reads as its first. (b) A replayed observation loses its TAILS — the reminder note,
rule-assist line, history pointer and once-grant note `EngineLoop._tails` appended after the
observation was recorded — so a resumed leg's prompt differs from what the model read: the provider
cache misses from the first such message, and cautions the model was shown are gone.

**Shape.** (a) rebuild both in their layers' `rebuild`, the `engine/guardscope.py` pattern; (b)
record the rendered tails on the observation event as a payload key and have
`history.replay_messages` append them.

**First increment.** (b), red-first: an observation that carried a `[REMINDERS: …]` tail replays
with it.

---

## Model-facing wording that drifted (E3.6, E3.10, E4.5, T2.9, E2.5)

Each changes what the model reads, so `docs/prompt-anatomy.md` and its test move in the same commit.

- The `wait` observation's mode wording and the `CHILD RUN FINISHED (<mode>)` notification say the
  same thing two ways; `engine/child.py` owns the mode vocabulary — one wording.
- `docs/prompt-anatomy.md` §5's full verbatim example is stale: regenerate it from a composed prompt.
- The hold observation's proceed sentence (`obs_hold._PROCEED`) when a reminder AND a rule match
  the same action.
- The action schema's `request` description names allow-now where the decisions include
  allow-once (D76).
- An `llm` reply over the observation cap loses its middle: spill it like util output
  (`engine/outputs.py`) and name the path.
- After a ⚑ refusal flag on a conversation's FIRST reply, the resumed leg is told it was
  "interrupted… continue from the last observation" over an empty history; a kickoff-style note fits.

**First increment.** The `llm` spill — the only item that loses information.

---

## Abort by run id; the runner's active map keyed per home (T3.2, W1.3, D1.3)

**Problem.** `runner.abort(slug)` aborts whichever run of that routine is live, and `runner.active`
is keyed by slug across the three homes, so a routine, a conversation and a background task with
the same slug collide; the web abort names a run id that the runner resolves to a slug.

**Shape.** Key the active map by home and slug (or run id); `abort` takes the run id the web route
already has.

**First increment.** Red-first test: two homes, one slug, both active — aborting one leaves the
other running.

---

## Conversation branches: inherited child numbering, and a branch of a branch (E3.7)

(a) A branch copies its parent's transcript, `subrun_start` events included. Verify whether the
branch's first child reuses a number the inherited transcript already holds (the task tree and the
hand-back paths key on it); fix it, or delete this half if it does not.
(b) The ⚑ refusal flag and ⟲ rewind read a reply's opening from the transcript
(`engine/rewind.reply_opening`); a branch records only its immediate fork point
(`parent: {slug, turn}`), so in a branch of a branch a reply inherited from before the
grandparent's fork can be misread. Record the whole lineage in the branch header and read it there.

**First increment.** (b), red-first with a two-level branch.

---

## Every git write goes through libgit (C1.10)

**Problem.** `library_docs`, `utils_lib` and `workflows.library` each wrap `git commit` themselves
(~20 call sites, e.g. `engine/authoring.py`). CLAUDE.md's rule: a git call goes through
`libgit.git` / `libgit.commit` — SIGTERM-first, lock-aware, a failure filed.

**First increment.** Replace the wrapper with the most call sites by `libgit.commit(…,
routines_home=…)`, keeping each message, with its tests.

---

## Files over the size bar (≤ ~350 lines)

`engine/loop.py` (413), `engine/inbox.py` (391), `engine/window.py` (380), `static/util.js` (381).
Split along responsibilities they already have — inbox's filing vs draining, util.js's formatting
vs DOM helpers, window's eviction warning, loop's observe/turn-record helpers.

**First increment.** `engine/inbox.py`, the most imported.

---

## Read models: the util-stats snapshot once in the daemon; an incremental search index (R1.3, R1.6)

(a) `engine/runtime.py` writes the util-stats snapshot from EVERY engine at run end; compute it once
in the daemon, in a thread like the limits refresh, on `run_*` events (debounced).
(b) The search index is rebuilt whole; index a run's transcript when it finishes and drop what
retention prunes.

**First increment.** (a).

---

## Console: engine timeouts served not copied; the finish line's "today"; the digest's rule_confirm

(a) `static/components/actiontime.js` copies the engine's action timeouts (and `wait`'s 600): serve
them in `/api/status`, built from the engine's constants (name the `wait` one), and read them there.
(b) `finish-line.js` computes "today" on the viewer's clock: `api_finishline.payload` returns the
scheduler's local day and the client uses it — a date outcome near midnight otherwise reads
differently from another zone.
(c) `settings-digest.js` never names `rule_confirm`, so an override on it shows no difference.

**First increment.** (b) — a wrong date is a wrong judgement — with red-first UI tests.

---

## Console: a Content-Security-Policy (S4)

**Problem.** The console sends no CSP. Verified in the review: a blob iframe INHERITS the console's
policy, so a strict `script-src` would break every HTML artifact's inline scripts (they open
sandboxed through `components/blobtab.js`); `index.html` has one inline theme script; `el()` sets
`style=` everywhere.

**Shape.** Now: `Content-Security-Policy: object-src 'none'; frame-ancestors 'self'` — after
checking that no artifact viewer uses `<embed>`/`<object>` (PDFs). Once artifacts are served from
their own route: a full policy, the theme script hashed, `style-src 'unsafe-inline'`, `frame-src`
allowing the browser-view relay.

**First increment.** The narrow header as middleware, with a test.

---

## The refusal shortcut misses curly apostrophes

`engine/refusal.REFUSAL_MARKERS` are typed with a straight apostrophe, and models often write
`I can’t` (U+2019): the opening-sentence shortcut never matches those and leaves each to the
classifier call. Normalise apostrophes and quotes in the opening sentence before matching.

**First increment.** The normalisation, red-first with `I’m sorry, but I can’t help with that.`

---

## Pipeline output within the model's cap; schedule_once's dead `expires_at` (C1.1, T3.5)

(a) `workflows/pipeline.py` asks for `MAIN_MAX_TOKENS` / `STAGE_MAX_TOKENS` = 16 000 whatever the
model; one whose window-derived output cap (`endpoints/limits._output_cap`) is smaller is asked for
more than it can return. Request the smaller of the two.
(b) `schedule_once.arm(expires_at=…)` is set only by a test and exposed nowhere: delete the
parameter and its field (the dead-code rule; exposing it in the console was the alternative the
review weighed and did not recommend).

**First increment.** (a), red-first with a model whose discovered output cap is 8 192.

---

## Deploy leftovers (SD2.5–6) and a pinned claude CLI

- `deploy/` chrome entrypoint: supervise Xvfb and Chrome with `wait -n`, so one dying ends the
  container (and `restart:` brings both back) instead of leaving half a sidecar.
- `deploy/setup-remote-agent-user`: add the `render` group for GPU access.
- `deploy/install.sh`: set `UV_PROJECT_ENVIRONMENT` explicitly (verify what the host install expects).
- `deploy/backup.sh`: identify the share with `findmnt -T` beside the device check.
- `Dockerfile`: pin the `@anthropic-ai/claude-code` version (hadolint DL3016), bumped deliberately
  like uv — today it is whatever is newest at build time.

**First increment.** The chrome entrypoint's supervision.

---

## CLAUDE.md gaps the review found

- A blob URL carries the console's ORIGIN: a new tab opens only through `components/blobtab.js`
  (`newTabHref`) — the rule behind 0.371.0's token-theft fix, nowhere in CLAUDE.md.
- `tests/test_stylesheet_tokens.py` pins the palette tokens — name it beside the design-system
  paragraph and the global-chrome gotcha.
- Deploy: `~/.credentials` is optional (bundle and backup tolerate its absence);
  `tests/test_deploy_state.py` pins compose against `deploy/state-paths.sh`.

**First increment.** All three in one commit.

---

## After 0.372.2 reaches the instance (self-audit housekeeping)

Self-audit's own items for the first run after the deploy that brings 0.372.2 — none needs the
builder.

- Tell `doppelcheck-maintainer` and `steward-hub-maintainer` (an addressed `report` each) that
  their notes about Node are stale — doppelcheck's memory names `/usr/bin/node v20.20.2` and a
  `/tmp` Node 22 workaround, steward-hub's state `/usr/bin/node`; the image now runs Node 24 LTS at
  `/usr/local/bin`.
- Delete `~/routines/.permissions-adopted.json`: its writer (`bootstrap.adopt_permissions`) is gone.
- Read `.control/migrations/seed-utils.json` and `.control/migrations/enabled.json`: anything they
  left as it was is named there. (The live library's `git`, `remote`, `pytest-run`,
  `reminder-census` and `vision` were brought current by hand on 2026-10-01, library commits
  7d06482 and 92bd051, and 0.372.1 made util-seed's `git` and `remote` the library's files — so
  seed-utils should report all four utils it carries, `remote`, `vision`, `git` and
  `reminder-census`, "already current". Anything else there is a real finding.)
- Once library-sync has exported after the operator rotated the routine token, confirm the library
  repo's `config/config.yaml` reads `routine_token: REDACTED`.
- Confirm Opus answers through `claude-proxy` again. 0.372.0 stopped forcing the action tool, and
  from the moment 0.372.1 went live every Opus turn came back `stop_reason: tool_use` with nothing
  to read and failed its run over to GLM 5.3; 0.372.2 forces it again. Since the deploy there
  should be no `empty completion` error on a claude-proxy model and no failover away from one for
  it. Any empty completion now carries `stop_details.unread` — the reply's block types, a tool
  call's name and its input's type: file that shape as a finding against
  `endpoints/anthropic_api.py`, because the cause was never confirmed (the deployed proxy config
  disables the cloak whose tool renaming would explain it).
- Settle R2111. scheduler-builder reported
  `tests/test_run_gate_checks.py::test_testing_the_gate_reports_without_running` failing on main at
  e570769; it passes on 0.372.x in the review's environment. Run it on the instance and close the
  row either way.
- Tell `scheduler-builder` (an addressed `report`) what its `audit/D152` campaign will meet when it
  merges main, unless it already has. The branch was pushed 2026-10-01 14:28 CEST from e570769 and
  calls its release 0.371.0, but main has since shipped 0.371.0–0.372.2: the release takes the next
  number above main's newest CHANGELOG header. Every file its commit message names changed on main
  in between — `engine/loop.py` by ~540 lines, `engine/remind.py`, `reminders.py`,
  `reminder_checks.py`, `engine/actionschema.py`, `docs/reminders.md`, `docs/prompt-anatomy.md`.
  And its pushed commit holds only `tests/test_reminder_kinds.py`: the source changes the message
  describes are not on origin, so they must be committed in its worktree before the merge.

**First increment.** The three reports.

---

## Shared test builders: the last cross-module imports, and conftest under the size bar

Continues the operator's decision "Test helper consolidation" (2026-10-01): that work moved 109
duplicated helpers into `tests/helpers.py`, `tests/ui/helpers.py` and `tests/conftest.py` without
changing a test, and proposed three things it was not scoped to do.

- Helpers still imported from one TEST MODULE into another move into `tests/helpers.py` (or
  conftest, when they need its fixtures): `test_loop`'s `_server`, `probe`, `TS` and `_run` (nine
  importers), `test_loop_referral`'s (four), `test_run_gate`'s (three), and the single importers of
  `test_assists`, `test_requests`, `test_util_outputs`, `test_reports`, `test_deploy_state`,
  `test_api`, `test_token_calibration`, `test_oversize_prompt` and `tests/ui/test_mobile_nav`. A
  private helper of one test file should not have a second owner.
- `tests/conftest.py` is 684 lines (574 before the consolidation). Its plain builders — the ones
  that need no fixture — move to `tests/helpers.py`, about 70 import lines; fixtures stay.
- CLAUDE.md's Standards names the two helper modules as the home of shared test builders, with the
  rules the consolidation kept: a helper with ONE caller stays local, and variants become keyword
  arguments — never "unified" by picking one.

The gate is the consolidation's: no assertion changes meaning, and both suites collect the same
node ids before and after.

**First increment.** The CLAUDE.md line, then `test_loop`'s four.

---

## Decide: a CHANGELOG version may never repeat or go backwards

`tests/test_policy.py` checks only that `__version__` equals the newest CHANGELOG header. Several
writers release into one main now — self-audit, scheduler-builder, operator sessions, review
sessions — and on 2026-10-01 `audit/D152` called its release 0.371.0 after main had shipped a
different 0.371.0: a merge that keeps its entry on top passes the policy test, and the console's
version goes backwards. Options: (a) the policy test also fails when two headers carry the same
version or a header is not lower than the one above it — all 509 headers pass today; (b) keep, each
writer renumbering by hand. **Review's recommendation: (a).**

**First increment.** The decision; then the check, red-first against a repeated header.

---

## Decide: the settings-pattern recommender's fallback (C1.2)

`patterns/recommend.choose` falls back to the alphabetically FIRST pattern when none matches the
routine's workflow — an arbitrary pick presented as a recommendation. Options: (a) fall back to
`one-job`, the neutral pattern; (b) recommend no pattern and say none fits; (c) keep.
**Review's recommendation: (a).**

**First increment.** The decision; then the chosen fallback with a red-first test.

---

## Decide: the reminder layer and run history for NEW conversations

New conversations start at `reminders: none` and `runs: none`, while routines default to `local`
and `last` (`DEFAULT_CAPABILITIES`, every settings pattern) — and CLAUDE.md's reason for that
default ("a layer nobody switches on never learns anything") holds for conversations too, though a
hold costs a turn in a live chat. Options: (a) conversations default like routines; (b) keep both
off; (c) reminders `local`, runs `none`. **Review's recommendation: (a).**

**First increment.** The decision; then the composer's default with tests.

---

## Decide: does the browser dock give the screen's one seat back?

Once connected (on first open, 0.372.0), the dock keeps the screen's one viewer seat while folded
and while hidden on `#/browser` — the full-screen page that needs that seat to be interactive.
Options: (a) release on fold and on `#/browser`, reconnecting on show; (b) release on `#/browser`
only; (c) keep. **Review's recommendation: (b) at least.** Check first whether `#/browser` is
refused a seat while the dock holds it — if so that half is a defect, fixed without asking.

**First increment.** That check; then the decision.

---

## Decide: dependencies and the base image's Debian release

- `psutil` — pid liveness with process create-time for the abort fallback (pid reuse).
  Recommended: yes.
- `filelock` — replace `paths.file_lock`, which raises on timeout. Recommended: evaluate for a net
  reduction.
- `regex` — match-time limits for reminder patterns; declined before. Recommended: no, until a
  catastrophic pattern is seen.
- `httpx2` — Starlette's test client deprecation (a `filterwarnings` ignore holds it today).
  Recommended: when Starlette requires it.
- Debian trixie for the three images (bookworm is in LTS). Recommended: at the next image refresh.

**First increment.** The decision; each accepted item then builds on its own.
