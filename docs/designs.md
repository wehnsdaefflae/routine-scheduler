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
delete one the moment it stops being wanted. An OPEN QUESTION never goes here — only the builder
reads this file, so a "Decide: …" entry reached nobody who could answer it (five sat here from
2026-10-01 to 2026-11-05); ask it as a self-audit `D` row, which reaches the Decisions page, and
write the entry once it is answered (`tests/test_policy.py` refuses a question heading).
From 2026-08-26 to 2026-09-22 this file had no reader at all: five entries accumulated and
none was built, the oldest waiting thirty-three days through roughly a hundred and eighty
releases, because the builder's queue could not see it.

Started 2026-08-26 against 0.230.x, on the operator's order to clear the queued design
backlog. An entry headed by an item id answers that Messages-page finding; an entry
decided in conversation before any finding exists says so and carries none.

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

## One name for a child run: "subrun" and "subtask" go (decided 2026-10-17, in conversation)

**Decided by the operator in conversation 2026-10-17; no finding.** His words: *"sub-runs /
subtasks (why two terms for the same thing? fix that)"*.

**Problem.** CLAUDE.md and `engine/child.py` define ONE concept, the child run, with three
scheduling modes. The vocabulary around it never followed. The same thing is called four names
depending on where you look:
- **child run** — `engine/child.py`, `engine/childrun.py`, docs/child-runs.md, the `CHILD RUN
  FINISHED` notification;
- **subrun / sub-run** — the budget keys `max_subruns` / `max_subrun_depth` (DEFAULT_BUDGETS,
  every settings pattern, 36 live routine.yaml files), the `subruns` action kind, the transcript
  events `subrun_start` / `subrun_end`, the console ("sub-runs" rail section, "sub-run tree",
  "sub-run selector") — measured 2026-10-17: ~180 occurrences in src, ~180 in tests, 64 in static;
- **subtask** — the `subtask` action kind (the sequential mode), ~90 in src;
- **spawn** — the `spawn` action kind (the parallel mode).
Worse, `static/components/subruntree.js` and `views.css` use "subtasks" for the SEQUENTIAL children
and "subruns" for the PARALLEL ones, so "subrun" means both "any child" and "a parallel child".
The operator, reading a routine's budget ("max_subruns") beside a recipe's "subtask", reasonably
read them as two mechanisms.

**Shape.** One noun everywhere a person or a model reads it: **child run** (plural "child runs";
"children" where a short token is needed). Concretely:
- budget keys `max_child_runs` / `max_child_depth`; the one-shot migration rewrites every
  routine.yaml, every settings pattern and the conversation configs (MIGRATION marker, per the
  migration rules), and `DEFAULT_BUDGETS` and the routine page's labels follow;
- the action kinds: `spawn` and `subtask` become ONE kind, `child`, with a flat `mode` field
  (`parallel` | `sequential`, the vocabulary `engine/child.py` already owns), and `subruns`
  becomes `children`. KINDS, the schema, kindsurface, prompt-anatomy.md and its test move
  together; the composer's kind copy says "child run";
- transcript events `subrun_start` / `subrun_end` become `child_start` / `child_end` — EVENT_TYPES,
  the web renderer and the meta routines move together, and stored transcripts are migrated
  one-shot (a resume re-renders history, so old event names must not survive);
- the console: "child runs" in the run rail, the tree and the selector; the tree marks mode with
  its arrows, not with two nouns;
- docs swept (CLAUDE.md, docs/child-runs.md, docs/architecture.md, help.js, docstrings).
A conversation `branch` stays a mode of the same concept and keeps its user-facing verb.

**First increment.** The model- and person-facing vocabulary: the `child` kind with `mode` (the
two old kinds removed, not aliased — no backwards compatibility), `children` for `subruns`, the
prompt wording and prompt-anatomy.md, and the console labels; red-first tests that the schema
offers no `spawn`/`subtask`/`subruns` kind and that the rendered prompt never says "subrun" or
"subtask". The budget keys, event names and their migrations are the second increment, in the
same campaign.

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

## Conversation branches: inherited child numbering, and a branch of a branch (E3.7)

(a) A branch copies its parent's transcript, `subrun_start` events included. Verify whether the
branch's first child reuses a number the inherited transcript already holds (the sub-run tree and the
hand-back paths key on it); fix it, or delete this half if it does not.
(b) The ⚑ refusal flag and ⟲ rewind read a reply's opening from the transcript
(`engine/rewind.reply_opening`); a branch records only its immediate fork point
(`parent: {slug, turn}`), so in a branch of a branch a reply inherited from before the
grandparent's fork can be misread. Record the whole lineage in the branch header and read it there.

**First increment.** (b), red-first with a two-level branch.

---

## Every git write goes through libgit (C1.10)

**Problem, CORRECTED while building the first increment.** All three wrappers were ALREADY
one-line pass-throughs to `libgit.commit`, so nothing bypassed it: the lock, the SIGTERM-first
timeout and the `commit_failed` filing were in force at every call site. What this entry buys is
INDIRECTION REMOVAL — a per-module `git_commit` hides which primitive a writer reached and is
where a divergent default (`routines_home=None`, an unscoped stage) would spread unseen — not
coverage. The behavioural red-first the entry implied ("the lock is respected") is green before
the change and proves nothing; the enforcing test is structural.

**SHIPPED (0.448.1):** `workflows.library`'s wrapper is gone, its 7 call sites converted, subjects
byte-identical, and `tests/test_libgit.py::test_no_pass_through_forwarder_stands_in_front_of_libgit_commit`
holds the line.

**What remains.** `library_docs` and `utils_lib`, which the enforcing test names as held back.
`utils_lib` is the expensive one: 7 direct test calls plus 7 monkeypatch seams
(`test_loop`, `test_util_widening`, `test_run_approval`) move with it, so it is its own increment.

---

## Files over the size bar (≤ 350 lines)

The bar is ENFORCED since 0.413.0 (F611): `tests/test_file_size.py` ratchets every one of the 107
files already over it to its current length and stops the other 755 from crossing, so this entry
is now about PAYING THE DEBT DOWN, not about noticing it. Measured 2026-10-11 on `c686aa48`:
`engine/loop.py` (453), `engine/window.py` (402), `engine/inbox.py` (398), `static/util.js` (381)
— each has grown since this entry was written, which is what an unenforced standard does.

Split along responsibilities they already have — inbox's filing vs draining, util.js's formatting
vs DOM helpers, window's eviction warning, loop's observe/turn-record helpers. Then run
`python tests/test_file_size.py --update`: a shrink that leaves its ceiling untightened is
permission to re-grow, which is why the gate reports one as a failure.

The 20 worst are all test modules (`test_loop.py` 3268, `test_api.py` 3011, `ui/test_flows.py`
2127) and `static/views.css` (2024); `docs/designs.md:380` covers the test-helper half.

**First increment.** `engine/inbox.py`, the most imported.

---

## Read models: an incremental search index (R1.6)

The search index is rebuilt whole; index a run's transcript when it finishes and drop what
retention prunes.

**First increment.** Index one finished run's transcript incrementally, leaving the whole-rebuild
path as the fallback.

**(a), the util-stats snapshot, SHIPPED in 0.448.1** — `daemon/util_stats_watch.py` computes it
once on `run_*` events (5 s debounce, 60 s cap) instead of in every engine at run end. Measured
honestly while building: the snapshot is 0.225 s cold against the live homes and the fleet runs
~23.6 root runs/day, so the saving is ≈5 s of CPU/day. The reason it was still worth shipping is
structural — one writer instead of N for a file no engine reads, and the per-run cost was the one
that only grows (the whole durable usage stream, parsed per run with nothing memoized). Two
documented behaviour changes: `rsched run-once` no longer refreshes the snapshot by itself, and
the file may lag a finish by up to 60 s.

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
  seed-utils should report the three utils it carries, `remote`, `vision` and `git`, "already
  current". Anything else there is a real finding. (`reminder-census` was the fourth until the
  operator deleted it from the library on 2026-10-05; `bootstrap.sync_seed_utils` never
  resurrects a deleted util, so its seed, its test and its migration row were removed.)
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
  that need no fixture — move to `tests/helpers.py`, about 70 import lines; fixtures stay. Since
  F611 that 684 is a frozen CEILING in `tests/size_ratchet.json`, so conftest cannot grow further
  while this is undone, and `tests/helpers.py` (under the bar) may not cross it — the builders
  that move need their own module once it gets close.
- CLAUDE.md's Standards names the two helper modules as the home of shared test builders, with the
  rules the consolidation kept: a helper with ONE caller stays local, and variants become keyword
  arguments — never "unified" by picking one.

The gate is the consolidation's: no assertion changes meaning, and both suites collect the same
node ids before and after.

**First increment.** The CLAUDE.md line, then `test_loop`'s four.

---

## Dependencies and the base image's Debian release (decided 2026-11-05)

The operator approved all four (2026-11-05), each building on its own:

- `psutil` — pid liveness with process create-time for the abort fallback (pid reuse).
- `filelock` — EVALUATE replacing `paths.file_lock` (which raises on timeout); adopt only for a net
  reduction in code and a gain in clarity, else record why not here and delete the line.
- `regex` — match-time limits for reminder patterns, so one catastrophic pattern cannot stall a
  hold check.
- Debian trixie for the three images (bookworm is in LTS) — at the next image refresh.

(`httpx2`, offered in the same question, waits until Starlette requires it.)

**First increment.** `psutil` in the abort fallback, with a pid-reuse test.

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

## Undo a run, and per-reply artifact snapshots (decided 2026-10-08, in conversation)

Carries item 4 of "adopt the list".

**Shape.** "Undo this run" on the run page: revert that run's autocommit limited to its
deliverables and the recipe (never `state/`), refused when a later commit touched the same paths.
For a conversation (unversioned), snapshot `artifacts/` per reply so ⟲ rewind restores the files
too, not only the transcript. **First increment:** the conversation artifact snapshot + rewind.

---

## Claude quota: a 429 from the quota read honours `Retry-After` (decided 2026-11-08, in conversation)

The REMAINDER of "Claude quota: the scoped weekly limits in `limits[]`", whose first increment
shipped in 0.526.0: `normalize` now reconciles both payload shapes into one representation keyed
by scope (`endpoints/subscription_quota.py`), validates every `limits[]` entry on its own, and
reports a limit it could not read as unknown rather than dropping it; `quota.js` renders scoped
rows from their own display names and the header chip reads the tightest cap across every row.

**Shape, still owed.** A 429 from the quota read honours `Retry-After` PER CREDENTIAL and the
card says when it will read again, rather than retrying on the next poll. Today
`cliproxy_quota.read_quota` turns any non-200 into one soft error string and the console simply
re-asks — so a rate-limited account is hammered at the console's own cadence and the operator is
told nothing about when a figure will exist again.

---

## A second opinion from another model family, as a library rule (decided 2026-11-08, in conversation)

From a review of getpaseo/paseo, whose advisor/committee skills get an independent read from a
DIFFERENT provider family, with no edit rights, before a contested decision.

**(a) IS BUILT** — a catalog model carries a one-line `note` ("when to use this model"), edited
on its catalog row and returned by `list_models` beside each name, so a run choosing a `model`
for `spawn`/`subtask`/`llm` or a `role_swap` position reads why rather than only what exists.
Advice, never a routing rule: nothing in the engine reads it to decide anything, and unset
prints nothing at all. What remains is (b) alone.

**Shape (b).** A library rule, `second-opinion`, whose operative line asks a run facing an
irreversible or contested judgment (a release decision, a triage verdict, a reply that cannot be
unsent) for one independent read from a child run on a model of a different family, briefed
self-contained and with no write rights, and to name in its finish where the two disagreed. It
names the capability, never a model or a util, and is bound per routine like every rule.

**THE RULE IS WRITTEN** — authored to the live library and seeded in `library-seed/rules/`
(0.528.0), with its provenance and the honest note that it rests on an argument rather than on
effectiveness data in `docs/curated-rules.md`. What remains is not code: a rule binds NO routine
until the operator's config says so, so the open step is **binding it to `self-audit` first** and
then having rules-review read its use back. This entry stays only to carry that one step.
