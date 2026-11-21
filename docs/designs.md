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

## A merge queue for main: whoever is green merges next, and the merge renumbers (decided 2026-11-21, code review)

**Decided by the operator 2026-11-21 (code review), no finding.** He asked for this after seeing
scheduler-builder's run of 2026-11-21 lose most of its window to releases landing under it.

**Problem.** Three kinds of writer ship to `main` with nothing ordering them: `self-audit`,
`scheduler-builder` and the operator's interactive sessions. On 2026-11-21 sixteen releases landed
in one day. A release gate (`candidate_gate`: quality → fast → ui) takes 25-40 minutes, and the
gate LOCK (`/tmp/rsched-gates.lock`) serializes the TEST runs but not the MERGES. So whoever goes
green second finds `main` moved: `scheduler-builder:20261121-140004` was overtaken four times
(0.581.0 → 0.582.0 → 0.583.0), re-gated three times and spent about two hours of its window on it.
None of the overtaking commits overlapped a file of its own. Its own note
(`.memory/scheduler-main-moves-under-a-release-gate.md`) records the costs: a refused
fast-forward that reads like a success when only the push is checked, a version number taken
while the gate ran, and a verdict that describes bytes no longer being merged. The operator's
sessions hit the same wall the same day, renumbering twice.

**Shape.** One merge path that every writer uses, with three properties:
1. **The version is assigned at MERGE time, not at candidate time.** The merge step rebases the
   candidate, gives its top CHANGELOG entry and `__version__` the next minor above `main`'s
   newest header, and commits that renumbering. "Your number was taken" stops being a conflict
   anyone resolves by hand. `test_changelog_versions_never_repeat_or_go_backwards` keeps
   pinning the result.
2. **Green-ordered serialization.** A merge LEASE (its own lock file, separate from the gate
   lock) is taken only by a candidate holding a green verdict on its current base. One merger
   at a time: rebase onto `main`, classify the delta, merge, push, read `origin` back by commit
   AND version, release the lease. A candidate still gating never blocks a green one.
3. **The re-gate rule is code, not judgement.** It classifies the files in `<base>..main` that
   the candidate did not write:
   - docs, CHANGELOG and memory files only → no re-gate;
   - test modules only, none of them a conftest, a shared helper (`tests/helpers.py`,
     `tests/ui/helpers.py`) or a fixture the candidate's tests import → run those modules plus
     the quality stage on the rebased tree;
   - anything else (source, static, conftest, helpers, config) → a full re-gate from `fast`.

   The classification and the stamp that covers the merged bytes go into the merge commit's
   message, so "which verdict covers which bytes" stays answerable.

Interactive sessions use the same entry point. A writer that bypasses it is what the queue
exists to stop, so the post-commit hook warns when `main` advances without the merge step's
trailer line.

**First increment.** The merge step as ONE script in `deploy/` (stdlib only, so self-audit's
`candidate_gate` and the builder's close-out can call it, and a session can run it by hand):
rebase, renumber, classify, then ff, push and read back under the lease. Tests for the
renumbering and the three delta classes. One line in CLAUDE.md's Commands, and the builder's
and self-audit's recipes switch their merge step to it. The hook warning is the second
increment.

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
2127) and `static/views.css` (2024); "Shared test builders" below covers the test-helper half.

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

## The D289 attention block: render the seven classes where the operator reads (decided 2026-11-21, code review)

**Decided by the operator 2026-11-21 (code review), no finding.** He kept
`readmodels/attention.py` and ordered its renderer built. D289 (answered 2026-11-14) is the
decision it serves: *"Lead with the seven classes as a labelled block at the top, narrative below
it"*, on the surfaces R3365 named — Decisions rows, routine cards, run summaries and the
finish-summary rendering — under his requirement that every row be self-contained (*"worst case
scenario is content i need to know about being referenced only by an identifier"*).

**Problem.** 0.547.0 shipped the CLASSIFICATION and nothing else: `attention.classify_run`
returns a run's rows in his order (a)…(g), each with `text` and `detail`, and no route, read model
or `static/` module calls it — its own docstring says "the labelled block's layout is the next
increment and reads this". Two further gaps are in the module as it stands:

- **The MARKED half has no writer.** `_marked` reads `status.json`'s `attention` list for classes
  (c) communication, (f) pivot and (g) dead-end, and nothing writes that key: zero `status.json`
  files fleet-wide carried it on 2026-11-19 (self-audit's deep sweep). Three of his seven classes
  can therefore never appear.
- **The (b) fold re-reads the whole health stream per run.** `_events` opens
  `.control/health-events.jsonl` on every `classify_run` call; the docstring's "a caller folding
  many runs reads the stream once" is not yet true. A routine-card surface folding one run per
  routine would read the stream once per card on every refresh.

**Shape.**

- **One read model, one fold.** `readmodels/attention.py` gains `attention_for_runs(home,
  run_dirs)`: the stream is read ONCE and grouped by `run_id`, then each run is classified with
  its group (`_events` takes the pre-grouped rows instead of a path). Memoized on a stat
  fingerprint of the stream and the run dirs' `status.json` through `readmodels/memo`, single
  flight, because the routine cards refresh on `run_*` bus events.
- **Served where the narrative already is.** `GET /api/runs/{run_id}` (`api_runs.run_detail`)
  adds `attention: [...]` for that run; the routine list (`/api/routines`, whose `last_run` feeds
  `static/views/dashboard-rows.js`) adds `last_run.attention`; the Decisions read
  (`web/decisions_read`) adds the asking run's rows to each record. No new endpoint: each surface
  already fetches the record the block belongs to.
- **One renderer.** `static/components/attention-block.js` renders the rows as a labelled block —
  one line per row, the class label from `CLASSES` (`a context to decide`, `b unexpected event`,
  …), `text`, then `detail` in the reading face below it, `×N` for an event row's
  `occurrences` — in the order the server sent (his order; never re-sorted client-side). Empty
  rows render nothing at all, so a run with nothing for him reads exactly as today. Mounted:
  above the summary in the run page's `finish-banner` (`components/transcript.js`), above
  `lr-sum` on each routine card (compact: label + text, `detail` in the hover), and at the head
  of each Decisions card's context. The narrative stays below, unchanged.
- **The marked half gets its writer.** `finish` takes an optional `attention` field: a list of
  `{class, text, detail}` restricted to `MARKED` (`c`, `f`, `g`), validated at the action seam
  (an unknown or derived class is refused in the observation, the same rule `_marked` applies on
  read), and `loopend.finish_run` writes it to `status.json` beside `goals`. The schema text is
  paid for under the shrink-only prompt budget (`tests/prompt_budget.json`) by cutting the same
  number of characters elsewhere in the always-on contract, or the ceiling is raised by hand with
  the reason in the commit. `docs/prompt-anatomy.md` and `tests/test_prompt_anatomy.py` move with
  it.

**First increment.** `attention_for_runs` with the one-pass stream fold and its memo, `attention`
on `GET /api/runs/{run_id}`, and `attention-block.js` mounted on the run page's finish banner —
with a fast test of the fold (one stream read for N runs, rows identical to `classify_run`) and a
browser test (a run with a met goal, a partial finish and a pending ask shows the block above
the summary in (a)…(e) order; a run with none shows no block). The routine cards, the Decisions
rows and the `finish.attention` field are the next three commits, in that order.

---

## The hook stream's REPLACE step: what fires next, and who spoke into which run (D152, decided 2026-11-21, code review)

**Decided by the operator 2026-11-21 (code review), no finding.** He kept the three
still-mirrored hook sources — injections, run-tree hooks and one-shots — and ordered the D152
campaign finished: its "wrap first, replace last" ends with a surface that reads the stream. This
is compatible with the `d152-one-read` branch, which only stops comparing the reports and
reminders stores and touches none of the three sources below.

**Problem.** `.control/hooks.jsonl` is written for every one-shot (`hooks/scheduleproj.record_armed`
/ `record_event`), every mid-run injection (`hooks/store.record_injection`, from
`engine/hookmirror.injection_delivered`) and every run-tree hook (`hooks/store.record_runtree`,
from `engine/runtree.send`) — and no surface reads any of those rows. `scheduleproj`'s docstring
says so of its own reader: `fires_at` and `due_before` "are exercised by tests, not yet by a
page". So the two questions the stream exists to answer for the INSTANCE still have no answer
anywhere in the console:

- **What fires next?** `/api/schedule/week` lists one-shots PER ROUTINE from the spool
  (`_one_shot_fires`), mixed into a cron strip; nothing lists the instance's pending one-shots,
  soonest first, with who armed each and why.
- **Who spoke into which run?** An injection's `via` (`trigger.via`, one of `engine/channels.VIAS`:
  the operator's composer or run view, a report, a branch or background hand-back, a trigger, a
  one-shot's provenance, an escalation rung, a run-tree hook) is recorded on every row and shown
  nowhere; a run-tree hook whose write failed is recorded `disposition: open` and shown nowhere.

**Shape.**

- **One read model, `readmodels/hook_activity.py`,** folding the stream once (memoized on its
  stat fingerprint through `readmodels/memo`) into two lists:
  - `next_fires(now, horizon)` — the schedule hooks still PENDING: an arming row
    (`source: schedule_once`, `trigger.at` set) with no later row about the same id whose
    `trigger.event` is in `scheduleproj.TERMINAL_EVENTS`. The fold comes FIRST and
    `scheduleproj.due_before(pending, now + horizon)` orders it, because `due_before` over raw rows
    would list every fired one-shot as overdue — the ending is a later row, not a change to the
    arming one. Each row: target routine (`split_addressee`), `fires_at`, `payload.reason`,
    `payload.requested_by`. A pending row whose instant is already past is shown as such, never
    dropped: it is a one-shot the daemon has not consumed yet.
  - `deliveries(since)` — injection and run-tree rows, newest first: the run (`run:<id>` →
    link), the turn, WHO (`via` rendered in words — "you, from the run view", "a report",
    "a branch hand-back", "escalation rung", "parent run" / "child <label>" for run-tree rows,
    `channels.user_authored` deciding person vs machine), the text's first line, and for a
    run-tree row its disposition (`open` reads as "not delivered — the write failed").
- **One route, one panel.** `GET /api/hook-activity?horizon_h=24&since_h=24` serves both lists
  (operator-only like the other fleet reads; not on any bus-event path — the panel refreshes on
  its own 60 s timer and on `run_finished`). The panel lives on the Routines page (Fleet group,
  production) beside the watch ribbon: "Fires next" (one line per pending one-shot, soonest
  first) and "Spoken into runs" (the last day's deliveries). Never on the blocked-work surface —
  nothing here is blocked.
- **The replace itself.** `/api/schedule/week`'s `one_shots` come from `next_fires` instead of
  the per-routine spool scan, under `reportread`'s rule so no reading can lose a one-shot: the
  stream decides, and when the spool holds a request the stream's fold does not, the spool is
  served and `hook_stream_diverged` is filed naming the id. FIRING stays on the spool — consuming
  the request file is the non-repeating guarantee, and `scheduleproj`'s docstring is right that a
  fold is strictly worse for that.

**First increment.** `readmodels/hook_activity.py` with both folds and tests built from real
row shapes (an armed-then-fired one-shot is NOT in `next_fires`; an armed-then-cancelled one is
not; an armed one past its instant IS; an injection's `via` is carried verbatim; a run-tree row
with `disposition: open` reads as undelivered), the route, and the panel with a browser test.
The `/api/schedule/week` switch and its divergence filing are the second commit, after a week of
the parity check running in report-only mode shows zero divergences on this instance.

---

## Conversation branches: a UI people can find and use (decided 2026-11-21, code review)

**Decided by the operator 2026-11-21 (code review), no finding.** He kept conversation branches
(F325) and diagnosed their non-use as the UI's fault: *"unused only because the UI is bad"*. Four
points, plus a lazy lineage read.

**Problem.** Everything a branch needs exists server-side (`branches.fork_conversation`,
`branches.hand_back`, `POST /api/conversations/{slug}/branch`, `…/handback`, `GET …/lineage`),
and the console hides it:

1. **The fork control is hover-only.** `⑂` is one of the bubble's corner controls
   (`components/chat.js` `branchBtn`), at `opacity: 0` until the bubble is hovered and `.45`
   then (`views.css`, "Mellowed (operator order 2026-08-23)"), beside three other glyphs. On a
   touch screen it is never visible at all.
2. **Branches are a lineage LINE in the header** (`components/branches.js` `branchControls`):
   names joined by commas, no state, no way to tell a branch working from one long finished.
3. **A hand-back lands as if the operator had typed it.** `hand_back` files an inbox message with
   `via="branch"`, which the engine records as a `user_injection` carrying `via`; the chat's
   `user_injection` case (`components/chat.js`) ignores `via` and renders a USER bubble, so the
   parent shows the branch's result as the person's own words, and the copied files
   (`artifacts/from-branch-<slug>/`) are named only inside that text.
4. **Starting a branch is two separate acts.** A fork creates the branch as a FINISHED run and
   navigates to it (`forkAt`); the person must then type its first instruction there. `ForkBody`
   takes `turn` and `name` and nothing to start with.
5. **The lineage read is paid on every conversation view.** `branchControls` calls
   `GET /conversations/{slug}/lineage` on mount, and that route reads every conversation's
   `routine.yaml` to find children — for the overwhelming majority of conversations, which have
   neither a parent nor a branch.

**Shape.**

1. **A visible "branch from here" on every reply.** A labelled text control (`⑂ branch`) in the
   reply's footer row, always visible at reading contrast — not a corner glyph revealed by hover.
   The other corner controls are untouched; this one moves because it is the one being promoted.
2. **Branches in the conversation rail, live.** A `branches` rail section (`components/rail.js`
   `add`, shown only when there is a parent or a child) listing the parent (with its fork turn)
   and each branch: name, state chip (the same `chip(state)` the background section uses), fork
   turn, last update, and "handed back" once it has. Fed by `lineage` extended with each child's
   `state`, `updated` and a `handed_back` flag (the parent's inbox archive holds a
   `via: branch` message from that slug), refreshed on `run_started` / `run_finished` bus events
   whose run id is one of those slugs — never polled.
3. **Hand-back as a card at the turn it lands.** `chat.js` renders a `user_injection` whose `via`
   is `branch` as a hand-back card, not a user bubble: "branch “<name>” handed back" linking to
   the branch, the summary, and each landed file under `artifacts/from-branch-<slug>/` as a link
   into the artifacts panel (the paths `child.collect_handback` returned are already in the
   message; carry them on the inbox record's `extra` so the renderer does not parse prose). The
   same card shape serves `via: background` (see the detach order).
4. **One click, optional instruction.** The `⑂ branch` control opens one small dialog: name
   (prefilled "<parent> (branch)") and an optional first instruction. `ForkBody` gains
   `instruction: str = ""`; when given, the route files it through the same message path
   `POST /conversations/{slug}/message` uses, so the branch starts working at once — the
   operator's own click, which is one of the three things allowed to start a run. Empty
   instruction keeps today's behaviour (the branch opens idle).
5. **Lineage only when there is one.** The conversation list row (`conversations_common._item`)
   carries the conversation's own `parent` slug (the raw `parent` key `_parent_record` reads,
   already loaded by `registry.scan`). The view derives "has a parent" from its own row and "has
   branches" from any row naming it as parent, and calls `/lineage` only when either holds.

**First increment.** Points 3 and 5 — the hand-back card (with `extra` carrying the landed
paths) and the lazy lineage read — with a browser test that a branch hand-back renders as a card
with its file links and NOT as a user bubble, and a test that a conversation with no family makes
no `/lineage` request. Then 1 and 4 together (the visible control opens the dialog), then 2.

---

## Playbooks: reachable where a conversation starts and where it ends (decided 2026-11-21, code review)

**Decided by the operator 2026-11-21 (code review), no finding.** He kept playbooks and ordered
their discoverability fixed: saving a conversation as a playbook and starting from one must be
reachable where a person starts and ends a conversation — the new-conversation screen LISTS the
playbooks, and a finished conversation OFFERS "save as playbook".

**Problem.** Both moves exist (`docs/playbooks.md`: Reuse, Capture, Refine) and both are tucked
away:

- **Starting.** The new-conversation screen (`static/views/conversations-new.js`
  `mountComposerOnly`) offers playbooks as a `<select>` labelled "playbook" under the message
  box, defaulting to "no playbook · start fresh"; a playbook's `when` and `axis` appear only after
  it is picked. A person who does not already know a playbook exists has nothing to see.
- **Ending.** `components/playbook-row.js` renders "＋ save as playbook" as a ghost button under
  the composer of EVERY conversation, at all times; D175's offer (`web/playbook_offer.py`,
  `SUBSTANTIAL_TURNS`) only un-ghosts it and adds a line there. The moment a conversation ENDS —
  the reply whose finish carries `final: true` (written to the finish event by
  `loopend.finish_run`; nothing in `static/` reads it) — has no offer at all.

**Shape.**

- **The new-conversation screen lists them.** Above the message box, a "start from a playbook"
  list built from the `GET /api/playbooks` read the screen already makes: one card per playbook
  with its title, its one-line `when`, its `axis` ("varies: …") and its tags — visible without a
  click, in the library's order. Clicking a card selects it (the existing `pbSel` value and
  placeholder switch), and a selected card shows a clear "start fresh instead" to undo. With no
  playbooks in the library the list is absent, not an empty box. The `<select>` goes.
- **A finished conversation offers to save it.** When the newest reply's finish event carries
  `final: true` and the conversation is terminal, the chat renders the offer directly under that
  reply: "This conversation is done — save it as a playbook?" with the save button (or "update
  playbook: <slug>" when `detail.playbook` is set, the existing `mode: "update"`), posting to the
  same `POST`/`PUT /api/conversations/{slug}/playbook`. `playbook_offer.offer` takes the final
  flag as a second reason to `show` beside `SUBSTANTIAL_TURNS`, so the predicate stays one
  testable server function and the console still never recomputes it. The always-present ghost
  button under the composer stays for the cases neither reason covers.
- **docs/playbooks.md** "The three moves" table names the two new places.

**First increment.** The playbook list on the new-conversation screen, with a browser test (two
seeded playbooks render as cards with `when` and `axis`; clicking one selects it and the empty
first message is accepted; an empty library renders no list). The final-reply offer and the
`offer()` change are the second commit.

---

## Detach: say when to use it, show it in the rail, let the operator start one (decided 2026-11-21, code review)

**Decided by the operator 2026-11-21 (code review), no finding.** He kept `detach` and ordered it
pushed harder: (1) the conversation-side copy tells a conversation to detach work that will
outlast the reply — long builds, sweeps, waits — instead of blocking on it or abandoning it,
paid for within the shrink-only prompt budget; (2) detached tasks visible in the conversation rail
with live status and their delivery; (3) the operator can start one explicitly from the composer.

**Problem.**

1. **The copy describes the kind but not the moment.** The `detach` bullet
   (`engine/kindprose.KIND_PROSE`, 687 characters, offered only to a ROOT conversation —
   `engine/loopsetup.py` adds the kind for `runkind.is_root_conversation` alone) names "a large
   scrape, a bulk conversion, a slow build" and ends "only for jobs too long to finish in this
   reply — otherwise do the work directly". It never names the two failure shapes the operator
   sees: a reply that BLOCKS on a long wait (a `wait`/poll loop, a build it sits through) and a
   reply that ABANDONS the work ("I've started it, check back later" with nothing running).
   `tests/test_prompt_budget.py` composes a ROUTINE, so the conversation's own copy of the
   contract is not under the shrink-only ceiling at all.
2. **The rail shows tasks only partly.** `static/views/conversations.js` already has a
   `background` rail section, hidden until a task exists, with a state chip, the label and a
   cancel button — refreshed by a 15 s poll, not by the run's events, and the row's `delivered`
   flag (served by `api_background.list_background_rows`) is never rendered: whether a finished
   task's result reached the conversation, and where its files landed
   (`artifacts/from-bg-<taskid>/`), is invisible.
3. **No way for the operator to start one.** `POST /api/conversations/{slug}/background`
   (`api_background.launch_background`, prompt + label) exists "so a human (or a test) can launch
   one directly", and nothing in `static/` calls it.

**Shape.**

1. **The copy names the moment, at no net cost.** Rewrite the `detach` bullet to lead with the
   rule — work that will outlast this reply (a long build or test run, a sweep over many items, a
   wait on something external) is DETACHED, never waited out in the reply and never left
   unstarted with a promise — and drop the clauses that restate the kind's mechanics the schema
   already carries. The new bullet may not be longer than 687 characters. Extend
   `tests/test_prompt_budget.py` with a `conversation` measurement (a root conversation's
   composed contract, seeded at its current size in `tests/prompt_budget.json`) so the
   conversation-side copy is held to the same shrink-only rule from now on.
   `docs/prompt-anatomy.md` and `tests/test_prompt_anatomy.py` move with the wording.
2. **The rail section, live and with its delivery.** The `background` section refreshes on
   `run_started` / `run_finished` bus events whose run id belongs to one of this conversation's
   task slugs instead of the 15 s poll (the poll's content-armed timer goes). Each row adds:
   elapsed time while live; on a terminal task, "delivered" (or "awaiting delivery") from the
   `delivered` flag; a link to the task's own run page (`#/run/<run_id>`); and, once delivered,
   the landed files under `artifacts/from-bg-<taskid>/` as links into the artifacts panel. The
   delivery itself renders in the chat as the hand-back card the branches order defines for
   `via: branch`, with `via: background` — never as a user bubble.
3. **Start one from the composer.** A "⇥ background" button beside send: it takes the composer's
   text as the task prompt (first line as the label, editable in a one-field dialog) and posts it
   to the existing `POST /api/conversations/{slug}/background`. The task appears in the rail at
   once; the conversation's own reply is not started — a person delegating a job is not a person
   asking the assistant something.

**First increment.** Point 3 and point 2 together (the button, the live rail with delivery and
links), with a browser test that a composer-started task appears in the rail, goes live, and
after delivery shows "delivered" and its file link. Point 1 — the copy and the `conversation`
budget measurement — is the second commit.
