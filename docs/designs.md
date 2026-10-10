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

## Background actions: phase 3, the one part still undecided (decided 2026-09-04 as D118, phases 1-2+4 shipped)

**D118's phases 1, 2 and 4 are BUILT IN FULL** — the `background: true` flag, the conversation UX,
the concurrency cap, budget accounting, and (since D176 answered its surface question with option
(a)) cancellation via `kill handle=bg1`. Their narration now lives where current behaviour belongs:
`docs/background-tasks.md` ("A single action in the background"), plus `docs/architecture.md` and
the `engine/background.py` module docstring. Read those for how it works; this entry is only what
is still unbuilt, kept because deleting the whole D118 entry would have deleted phase 3 with it.

### Phase 3 — mutation ordering (its own decision, never part of D118's build)

Only reads and fetches may be backgrounded (`engine/actions.BACKGROUNDABLE_KINDS`): a mutation
(`write_file`, `edit_file`, `write_util`, `memory_write`) is refused, because a later synchronous
action would read state the deferred one has not written yet. Widening the allowlist needs a
dependency or explicit-barrier model so a backgrounded write cannot be read stale. D118's own text
called this "its own decision item" and it still is — nothing about it has been decided, and the
cheap half (leave it refused) is what ships today and works.


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

---

# Decided 2026-10-08, in conversation (the research review)

The operator reviewed twelve research questions on 2026-10-08 and marked each follow-up "now" or
"later". The "now" items shipped that day (goals 0.395.0; effort reaching Claude models 0.396.0;
change measurement, the production/development split and model trials; the document reader).
Everything below is a "later": decided, queued here, no finding behind it. Overlapping asks are
merged into one entry and say which asks they carry.

---

## A goals card in the conversation side panel (decided 2026-10-08, in conversation)

**Problem.** Since 0.395.0 a conversation keeps what the person asked for as goals (`b1`, `b2`, …,
engine/goals.py) and every reply declares itself final or not, but the person sees the goals only
as `goal` observations inside the work folds and in `status.json`. The side panel (artifacts,
state graph, files) has no view of them.

**Shape.** A `goals` card in the conversation's right panel (components/rail.js section), fed by the
conversation detail read (its newest run's `status.json` `goals` + `final`): one line per goal —
mark (○ open / ✓ met / ✗ dropped), id, text; the met evidence and the person's quoted words on
hover; the verifier's `disputed` objection as a faint note. The newest reply's `final` shown once
at the card's head ("final reply" / "handed back — N open"). No editing in v1: a person changes
goals by saying so.

**First increment.** The card, the detail field it reads, a browser test (open/met/dropped, both
themes, the narrow layout), docs/conversations.md "Goals and final replies".

---

## Engine-recorded deliverables, judge eval sets, and untrusted-content provenance (decided 2026-10-08, in conversation)

Carries the operator's "take the three ideas" (OpenWorker) and items 1, 3 and 5 of "adopt the
list" (Claude Code internals).

**1 · The engine records each run's deliverables.** Today a run NAMES what it produced in prose,
or nothing. At the finish (loopend.finish_run) the engine collects what changed under the run's
`artifacts/` (and declared output folders), plus child hand-back paths, plus files modified since
the run started that the run's own actions wrote — utils and scripts included, not only file
actions. It records them in status.json (`deliverables: [{path, size, kind}]`) and a footer in
result.md; the run page, the dashboard row and the Messages item show them as links. The model
never declares the list (the same split as the engine-indexed archive). A file written outside the
routine's dir gets a "written outside its own folder" row.

**2 · Eval sets for the judge models.** The verifier and the `decide` models judge without ever
being measured. Build a corpus from production records — `disputed` finish-line/goal entries,
`claims_unsupported` deferrals and their re-assertions, labelled by a person on a review page
(agree with the judge / with the run) — kept under `.control/` (it holds transcript text), and a
`RSCHED_LIVE_TESTS=1` run per judge model that must pass before a judge model is switched.

**3 · Untrusted-content provenance.** Utils declare in their header that they bring in outside
content (`ingests: web|mail|…`). The first OUTBOUND action after such content entered the run
(messaging, publishing, a push, a `shell` with network) is HELD once through the existing hold
seam (engine/hold.py, a new source) and the outcome counted like a reminder's tally; promote it to
an access request only if the counts justify it.

**First increment.** Item 1 (deliverables in status.json + the run page links), with tests; items
2 and 3 are their own commits after it.

---

## Memory consolidation by the routine's own run, and recall over its memory index (decided 2026-10-08, in conversation)

Carries the operator's "adapt the two ideas" (always-on memory agent).

**Problem.** `.memory/` notes accumulate and are never consolidated; `state/notes.md` grows to
1,581 lines (self-audit) while only its last 10 lines reach the next run; 26% of runs in routines
with ≥5 notes read no note at all.

**Shape.** (a) A library rule (or an extension of an existing memory rule) with a `pre-finish` or
`boundary` ASSIST whose named predicate (engine/assist_predicates.py) fires when `notes.md` grew
≥ N lines since the last `memory_write`, or the memory INDEX holds > 25 entries: "promote durable
notes, merge duplicates, date the facts, delete stale ones" — done by the routine's own run through
`memory_write`, so the INDEX stays engine-owned and the work is in the transcript. No daemon-side
rewriter, no second loop. (b) engine/recall.py gains `.memory/INDEX.md` as a second store, with the
same deterministic overlap scorer, single pointer, score floor and cooldown, delivered as an
observation tail; measure how often a `memory_read` follows a pointer before widening it.

**First increment.** (b) with its measurement; then (a) and the rule's one-shot migration
(docs/rule-assists.md).

---

## Interactive diagrams in a conversation: sandboxed live artifacts (decided 2026-10-08, in conversation)

**Problem.** The operator wants interactive charts and diagrams inside a conversation. Flint (the
first candidate) is a 0.x compiler that still needs Vega (≈380 KB gz) and would be the first
third-party JavaScript in `static/`, running in the console's own origin.

**Shape.** A LIVE ARTIFACT: the run writes a self-contained HTML document (`artifacts/<name>.live.html`)
or a fenced ```` ```live ```` block in a reply, and the console renders it INLINE in the chat inside
a sandboxed iframe (`sandbox="allow-scripts"`, no `allow-same-origin`: an opaque origin with no
access to the console, its token or its storage) sized to its content via postMessage. A small set
of VENDORED libraries (Mermaid for diagrams, Vega-Lite for charts, a graph layout lib) is served
from a separate static path the iframe may load, pinned by sha256 in a SOURCES file — third-party
code never executes in the console's origin. The setting `charts: on|off` (per routine or
conversation) only adds one prompt line teaching the form; rendering is always on, so old
transcripts look the same. Errors render as a card with the source. The artifacts panel renders
the same files full-size. Flint can become one of the vendored compilers later, behind the same
sandbox.

**First increment.** The sandboxed inline renderer for `.live.html` artifacts and ```live fences
with Mermaid vendored; a browser test proving the iframe cannot read `localStorage` or call the API;
the prompt line behind the setting; docs/conversations.md.

---

## Change contracts and a `recurred` status (decided 2026-10-08, in conversation)

Carries the operator's "add the `expect` and `recurred`" and "change contracts for recipe edits".

**Problem.** Since the change-measurement release every change is judged automatically from its
runs (readmodels/change_effects.py), but only on general signals. A change made to fix ONE thing
(a Done-when line unmet, a health-event kind, an error class) is never checked against that thing,
and a finding closes as `addressed` the moment its fix ships — nothing says when it came back.

**Shape.** Every proposer that changes behaviour — routine-improver and revise-recipe edits,
rules-review revisions, accepted config patches, scheduler-builder releases, util patches — writes
an `expect` contract beside the change: `{change, component, targets: <a signature: health event
kind | error class | done-when line | signal name>, direction, runs: N, rollback: <commit>}` (the
routine's `state/changes.jsonl`, a library-level file for rules and releases). A read model scores
each contract once N runs have run on the changed key: `confirmed`, `no_effect`, `regressed`,
`underexposed` — reading the targeted signature in the runs AFTER against BEFORE, beside the
general verdict. The item ledger gains a `recurred` status: an `addressed` finding whose signature
reappears after its fix shipped is reopened as `recurred` (docs/items.md precedence), and a
`no_effect`/`regressed` contract is fed back to the proposer that wrote it (its digest) as a
rejected-edit record (SkillOpt's buffer).

**First increment.** The contract file shape and writer helper, the scoring read model with
back-tests on the 27 shipped rows of scheduler-builder's `state/shipped.jsonl`, and one proposer
(routine-improver) writing contracts.

---

## routine-improver waits for evidence before it edits again (decided 2026-10-08, in conversation)

**Problem.** The median recipe version gets 2 runs and only 14% of recipe changes had ≥3 runs on
both sides, so most changes can never be judged (`too few runs`).

**Shape.** routine-improver may not edit a routine whose CURRENT recipe version has fewer than 3–5
runs (read from the change-effects read model: the newest change's `runs_after`), unless it is
repairing a failed run; and it applies ONE capped batch of edits per visit. The read model exposes
an `evaluable` flag per routine. Target: the share of evaluable recipe changes from 14% to > 60%
with tokens per ok run no worse.

**First increment.** The `evaluable` flag, the recipe change in routine-improver's lens stages, and
a measurement after two weeks.

---

## Library rule revisions tested on past decision points before approval (decided 2026-10-08, in conversation)

**Problem.** A rule revision reaches 30–136 routines at once and is judged only after the fact.

**Shape.** For a candidate rule text, rebuild past runs up to chosen turns
(`history.replay_messages` + `rewind.cut_index_for_turn`), swap in the candidate text, and make one
model call per decision point: HELD-IN points are turns where the failure the revision targets
happened (the action should change), HELD-OUT points are ok decisions from other holders (the
action should not get worse). The result rides rules-review's `rule_confirm` request so the
operator approves with evidence. Limits stated in the result: single-step, the world has moved on
since the trace, judging actions is itself fuzzy, `runs/` keeps 30 runs per routine.

**First increment.** The replay harness as a library util callable by rules-review, run on the
last three rule revisions to see whether it would have predicted their measured effect (the change
read model now has that ground truth).

---

## Stage exit checks: deterministic sensors before the LLM verifier (decided 2026-10-08, in conversation)

Carries "stage exit checks" and item 2 of "adopt the list".

**Problem.** A `met` claim is checked only by an LLM reading a transcript tail (fail-open) and the
"producing stage never entered" proxy. Stage inputs/outputs exist only as free prose
(workflows/pipeline.py), and recipes write checks in prose ("state/signal.json dated TODAY is the
proof it ran").

**Shape.** A stage module's frontmatter may declare `outputs: [{path, check}]` with checks from the
gatekit vocabulary — `written_this_run` (from the transcript's file actions), `state` (a JSON
key/value, e.g. date == today), `script` (`scripts/check_<stage>.py` exits 0, in the jail). A new
`engine/exitchecks.py` runs them in finishgate BEFORE `verifier.refuted`; a failing check spends
the line's one challenge without an LLM call; `binding: true` per check turns a re-asserted `met`
into `unmet (check failed)` instead of `disputed`. Patterns gain an optional `CHECKS` literal the
lint validates; materialization (workflows/adapt.py) carries it into stage frontmatter.

**First increment.** exitchecks + the three check kinds + finishgate wiring, adopted by
llmsectest-weekday first; measure false `met`s caught and verifier calls saved.

---

## Offline replay of the engine's gates over archived transcripts (decided 2026-10-08, in conversation)

**Shape.** A library util (or a self-audit stage) that replays exit checks, reminder regexes, rule
assist predicates and the verifier prompt over archived transcripts and reports what each would
have done — the regression suite for a harness change. It cannot live in tests/ (production_guard
forbids reading the live homes). **First increment:** reminder regexes and exit checks (pure, no
model), then the verifier on a sampled set.

---

## A generic pattern: autoresearch-a-method (decided 2026-10-08, in conversation)

**Problem.** claudini (romovpa/claudini) hill-climbs code against a fixed evaluator with an outer
loop around Claude Code. The same shape fits many jobs here — not only LLM attack research: any
METHOD with a measurable score (a prompt, a heuristic, a scheduler policy, a trading or betting
model on a backtest, SQL or build performance, a model's hyperparameters).

**Shape.** A library workflow pattern `autoresearch-a-method`: one run = one iteration; the
schedule replaces `/loop`, LEDGER and `.memory/` replace AGENT_LOG. Stages: orient · collect
(script: poll machine tickets / results) · score (script: leaderboard over a TRAIN and a held-out
VALIDATION split; flags train↑ with validation flat = reward hacking) · audit (script: AST/static
checks for the method's forbidden moves — overriding the evaluator, seed fishing, reading results)
· design (the one judgment stage; optional parallel `spawn` of 2–3 variants) · implement
(`write_file` + `then_script` audit and smoke test) · submit (machine queue) · record. DONE_WHEN
d1–d4 (every ticket collected or running; each collected version scored on both splits; one new
version audited, smoke-tested, committed and submitted; the ledger names the idea and what to try
next). The finish line is run-judged ("beats the best baseline's validation score by X") plus an
`until`. NEVER: tune on the validation split; change the evaluator. A reminder holds any
submission naming the validation split; the run gate skips a fire when no ticket finished.

**First increment.** The pattern file (lint-green), one worked example in docs/examples.md, and a
first routine on predator's GPU for the GPT-2 demo track (its 6 GB only fits that).

---

## Memory notes carry `stale_after` and `verified` (decided 2026-10-08, in conversation)

**Shape.** `memory_write` gains two optional fields (from the Open Knowledge Format): `stale_after`
(YYYY-MM-DD) and `verified: {by: run|human:<who>, at}`. The engine renders them in the note's INDEX
line ("stale since …", "operator-verified 2026-10-02"), and a note past its `stale_after` is listed
under its own heading in the digest. Notes today write "verified live 2026-07-30" in prose; this
makes it data. **First increment:** the fields, the INDEX rendering, the digest heading,
prompt-anatomy.

---

## Undo a run, and per-reply artifact snapshots (decided 2026-10-08, in conversation)

Carries item 4 of "adopt the list".

**Shape.** "Undo this run" on the run page: revert that run's autocommit limited to its
deliverables and the recipe (never `state/`), refused when a later commit touched the same paths.
For a conversation (unversioned), snapshot `artifacts/` per reply so ⟲ rewind restores the files
too, not only the transcript. **First increment:** the conversation artifact snapshot + rewind.

---

## A per-run context breakdown (decided 2026-10-08, in conversation)

Carries item 6 of "adopt the list".

**Shape.** Record the prompt's size by SEGMENT at each turn (system/recipe, rules, digest,
capabilities, history, observations) beside `window.note_prompt_size`, written to status.json and
shown as one stacked bar on the run view and in Stats — diagnostic only, never in the cached
prompt. **First increment:** the measurement and the run-view bar.
