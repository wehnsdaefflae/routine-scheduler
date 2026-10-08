# Run analytics: measuring changes, recipe-version health, per-util stats & prompt-cache health

The scheduler improves its routines through use — the routine-improver edits recipes
directly. Two measurement layers close that loop: every run is attributed to the **recipe
version** that produced it, and every util call is counted by **outcome**. Both survive
run-dir retention because they ride the durable workflow-usage stream
(`~/routines/.control/workflow-usage.jsonl`).

That stream holds one record per LEG, not per run: a continued or resumed run appends another
record under the same `run_id`. A leg's `tokens`, `cost` and `compression` tally are its own,
while `turns`, `utils`, `referrals` and `asks_deferred` are the run's running totals (a resumed
leg's boot reseeds them). Every reader below therefore counts RUNS —
`readmodels/usage_stream.usage_runs`, the stream folded once per change: the per-leg fields
summed, the running totals read from the newest leg. Summed leg by leg, a conversation answering
ten replies with one util call each read as 55 calls.

A third layer measures what a run COSTS rather than what it did: prompt-cache health, the
one reading that separates carrying context cheaply from paying for it twice. A fourth measures
what an efficiency mechanism RETURNS — lossless output compression, per routine, on that same
durable stream.

A fifth measures the runs that never happened. Work the fleet owed and did not do leaves no
run behind, so it leaves nothing on any of the four layers above; the health stream is where
it lands, and [Blocked work](#blocked-work-apihealthblocked) is where it is read.

## Recipe versions

A routine's recipe is `main.md` + `stages/` + `tuning.yaml` — exactly the
file set runs may never write (the routine-improver's fs_write_root is the one unlock).
Its **version** is the last git commit that touched any of those files, NOT the dir's
HEAD: the engine autocommits state and outputs at the end of every run, so HEAD moves
constantly while the recipe stays put.

At run start the engine stamps the current recipe commit into the run
(`rsched/recipes.py`):

- If recipe files are **dirty** (the improver edited them since the last run; nothing
  commits a target dir until its own next run), they are first committed as a
  recipe-only `recipe: pre-run snapshot` — so every recipe version is a real, revertable
  commit, cleanly separated from state noise.
- The commit lands in `status.json` (`recipe_commit`) and in the run's workflow-usage
  record. Conversations and other unversioned dirs stamp `null` — they have no recipe
  history by design.
- Beside it the record carries `library_commit`, the LIBRARY's HEAD as of the run's END
  (depth 0 only, one read per run). A rule revision reaches every holder at once and moves no
  recipe version, so it is the only thing that dates the change the trend below reacts to.
- And a `fingerprint` (depth 0, engine/runrecord.py): the engine release, the main model by
  catalog name and by provider id (`model_id`) with its effort, the deliberation level, a hash of
  the behaviour-relevant config, a hash per held rule — and `trial`, the id of the model trial the
  run was fired under (below).

## Model trials

A recipe version answers "did this CHANGE help?"; a **model trial** asks the same of the model
(operator, 2026-10-08): is this routine's model more than its work needs, or not enough? A trial is
ordinary config — `trial: {id, models, runs, reason}` in routine.yaml (`config/trialconf.py`),
e.g. `{id: t-20261008-sonnet-high, models: {main: sonnet-high}, runs: 5, reason: …}` — that a
capable maintenance routine (config-optimizer) PROPOSES as an `ask_user` `config_patch` and the
operator applies with the Decisions page's one **approve & apply** (`{"trial": null}` clears one).
The filing check refuses a malformed trial or a model the catalog lacks on the turn it is filed
(`engine/config_bridge.trial_value`), and the PATCH refuses both again, plus a model whose window
cannot run a turn — a trial is a `models:` binding for a few runs, so it meets the same checks.

From the accept on, everything is DERIVED (`rsched/trials.py`) and nothing writes config again:

- **Active** while fewer than `runs` of the routine's runs have recorded it — depth-0 records
  folded per run (`usage_stream.usage_runs`) whose `fingerprint.trial` is the trial's id. Counted
  from this stream, so a restart or a retention sweep loses no run and a fire that never reached
  the engine (a gate skip, a failed launch) fakes none.
- **Armed per fire.** While active, EVERY fire — schedule, lane, trigger, catch-up, manual — gets
  `trial.json` (`{id, models, runs}`) in its new run dir before the engine exists (the runner, beside
  a hand-started run's brief), and the runner names the models to `engine-run` as `--model
  role=name`. The engine runs the routine on them for that run (children included), and the run's
  record carries `fingerprint.trial` beside `fingerprint.model` (the catalog name it ran on) and
  `fingerprint.effort` (engine/runrecord.py). A resume reads the run dir's OWN `trial.json`, so a run keeps the
  trial it started under even once the trial has finished. `rsched run-once` arms nothing.
- **Finished** at `runs` recorded runs: the trial simply stops applying. The field stays in
  routine.yaml as history until the operator's next accepted change clears or replaces it — which
  is why `trial` is outside the fingerprint's config hash: the runs after a finished trial must
  compare equal to the identical runs before it. A new trial needs a new id; re-proposing an old
  id finds its runs already counted.
- **Ignored** while a model it names is not in the catalog (renamed or removed since the accept):
  applied, the run would die resolving a model nobody serves. The routine page lists it among the
  routine's problems and `rsched validate` prints it; no fire is armed.

The routine page's header carries ONE chip while a trial is active — `trial · <model> · k of N
runs`, the reason in its tooltip — and, once it finished, `trial finished · results in
Development`, linking to the routine's change view (`#/changes/<slug>`), where the trial's runs are
read against the runs on the routine's own model. `GET /api/routines/{slug}` carries the state as
`trial` (`{id, models, runs, reason, recorded, state, problem}`, or null).

## The health view (Changes → a routine → Recipe health)

How a routine's recipe versions have done is DEVELOPMENT information, so it lives in the
routine's development view (`#/changes/<slug>`, rail group *Develop*), not on the production
routine page — which keeps one line pointing there (see [the console](#in-the-console-production-and-development-apart)).

`rsched/readmodels/run_health.py` buckets the routine's depth-0 usage records — its OWN, never an
archived routine's that held the slug before it (`incarnations.live_runs`) — by recipe version:
runs, ok/partial/failed/aborted, fail rate, median turns and tokens, and
deferred-question churn (`asks_deferred` — decisions the runs threw over the wall: a
deferred ask, or a blocking ask that timed out / was parked / died with an abort).

Records that predate the stamp are attributed **by date** — the newest recipe commit not
after the run — and shown as `date-mapped`: pre-stamp recipe edits were only committed at
the NEXT run's end, so date attribution can be off by one run around old changes. Honest,
not exact.

### The regression flag

A deterministic heuristic — plain thresholds, no statistics libraries — compares the runs
just after the newest recipe change with the runs just before it
(`run_health.regression_flag`):

| constant | value | reason |
|---|---|---|
| `REGRESSION_WINDOW` | 5 | ≈ a week of a daily routine; one flaky run is only 20% of the sample |
| `MIN_RUNS` | 3 | fewer runs on either side is a coin flip, not evidence |
| `FAIL_RATE_JUMP` | +0.4 | one extra failure in 5 (+0.2) is flake; two is a pattern |
| `BALLOON_RATIO` | 1.5× | median turns/tokens growth that marks real ballooning |
| `TURNS_FLOOR` / `TOKENS_FLOOR` | +5 / +20k | absolute floors so a 2→3-turn routine's ratio never flags noise |

`partial` (budget-stopped) counts as not-ok: a recipe change that makes runs blow their
budgets IS a degradation. The flag renders as a banner in the routine's development view
naming the commit and the numbers behind each reason, and the production routine page's one
development line turns SUMMONS and says `recipe regression flagged` (`run_health.recipe_regression`,
read through `GET /api/changes/{slug}/summary`). **Flag-first**: nothing reverts automatically.

### The time-window trend

The same thresholds, applied a second way: the last `REGRESSION_WINDOW` depth-0 runs against
the `REGRESSION_WINDOW` before them, whatever recipe produced either side
(`run_health.recent_trend`, payload key `trend`).

The version-keyed flag above cannot see the most expensive kind of regression there is. Its
buckets key on `recipe_commit`, so it moves only when THIS routine's recipe moves — and a
library RULE revision reaches every holder at once without touching a single recipe
(`error-recovery` reached 30 routines on 2026-09-16, `web-research` 136 on 09-18,
`independent-verification` 34 on 09-21). Measured: self-audit's weekly medians went 49,409
tokens / 146 turns / 87% ok (w36) to 338,024 / 158 / 43% (w37) and back to 95,782 / 289 / 69%
(w39), with no recipe change under any of it — so `versions` held one bucket, `regression`
never evaluated, and nothing on the instance said a word.

One heuristic, two keys, deliberately: a reader comparing the two reads a difference in WHAT
changed, never in how it was judged. `trend` needs no git history at all, so a conversation
gets one too.

The trend is also PUSHED, not only served: the engine evaluates it at run end, once this run's
own record has landed, and emits `cost_trend_degraded` while it is flagged
(`engine/runtime._log_cost_trend`, carrying the window and both sides' medians and fail rates
as structured fields). A payload nobody opens is a reading nobody has — the nightly audit reads
the health stream, so that is where a regression this size has to appear.

### How a partial finish ended

`endings` (same payload) counts this routine's last week of `budget_exhausted` and
`run_partial` health events. Both land in the usage stream as `partial`, so a routine that
outgrew its ceiling and one that keeps finding a source down read identically there;
only the health stream says which budget forced a finish, and `last_detail` names it.

### One-click roll-back

`POST /api/routines/{slug}/recipe/revert {commit}` restores the recipe files to their
state just before that commit and commits only those paths — `routine.yaml` (the user's
config) and `state/` are never touched. The revert commit is itself the next recipe
version, so health tracking continues seamlessly. Made while a run is active, the revert
is queued and applied when the run ends (D78-A, `pending_edits`), like a recipe file edit.

## Measuring changes (`/api/changes`, Develop → Changes)

The recipe-health flag above sees one kind of change (the recipe) through four coarse signals
(status, turns, tokens, deferred asks). Every change that can alter what a routine does is now
measured, from the runs themselves, with nothing declared:

- **What made a run.** Every depth-0 usage record carries a `fingerprint` (`engine/runrecord.py`):
  the engine release, the main model by catalog name and by provider id (`model_id`) and its
  effort, the deliberation level, a short hash of the behaviour-relevant config, a short hash of
  each held rule's text, and the id of a model trial when the run was fired as one. The recipe and
  library commits ride the record already. The config hash covers an ALLOWLIST
  (`runrecord.BEHAVIOUR`: models, connections, machines, grants, budgets, permissions, shared
  reminders, capabilities, read and write roots, the ladder); every other field is listed in
  `runrecord.NOT_BEHAVIOUR` — who the routine is, WHEN it runs (cron, zone, triggers, gate, off
  switch), retention, `improve`, and what is measured as its own component (deliberation, rules,
  tuning — part of the recipe — and trial). `tests/test_runrecord.py` fails on a field in neither
  set: 0.398.0 excluded YAML keys rather than field names, so every cron edit read as a behaviour
  change.
- **How it went.** Beside it, `quality`: the accounting lines owed, met, unmet and not due
  (Done-when lines and goals), stages skipped, claims the verifier challenged and the ones that
  stood disputed, actions a caution held, schema retries, the person's interventions while the run
  worked, wall time, cache reads and writes. Run-cumulative (reseeded on a resume), so the fold
  keeps the newest leg's values.

A routine's input differs from run to run, so no run's OUTPUT is scored. What stays comparable is
the process: the same routine answering for the same Done when should be about as correct, as
complete and as cheap from one run to the next. `readmodels/change_signals.py` reads fifteen
signals per run in three dimensions — CORRECTNESS (failed or aborted, claims challenged and
disputed, holds, failed util calls), COMPLETENESS (owed lines met, lines unmet, partial finishes,
stages skipped) and EFFECTIVENESS (median tokens, turns and minutes, interventions, schema
retries, cache read share) — each with a direction and a noise threshold (a median also needs an
absolute floor, so a 2→3-turn routine never reads as 50% worse).

A signal compares only runs it was COUNTED the same way in (`change_signals.COUNTED_SINCE`):
when a release changes what a signal counts — the ruler, not the routine — its entry moves, and
runs before it have no reading for that signal. 0.369.0 had the claim verifier check every
Done-when `met`, 0.370.2 on every endpoint, and 0.371.1 stopped a resumed run repeating a hold, an
assist or a challenge — the fleet's challenged claims went from 0.03 a run to 1.2 overnight on
2026-09-30, which read as every change of that day making the fleet less correct.

`readmodels/change_keys.py` reads each run's behaviour key (recipe, config, model, `model_id`,
effort, deliberation, a rule's text, a trial) and `readmodels/change_effects.py` finds the
CHANGES: boundaries between consecutive runs whose keys differ. The up to five runs before and
after, each side only runs of one key, are judged signal by signal, each dimension
`better`/`worse`/`mixed`/`same`, and the change gets one verdict — `improved`, `regressed`,
`mixed`, `no effect`, `measuring` (the newest change, still collecting runs) or `too few runs`
(another change came first; a routine whose recipe changes every run cannot be said to have
improved, and saying so is the finding — on this instance's history, 96% of all routine changes).

What reaches MANY routines at once is judged across them, POOLED (`readmodels/change_fleet.py`):
each routine adds the runs on either side of its boundary that carry its own key unchanged, its
cost signals divided by its own median (`change_signals.normalized` — a routine of a million
tokens and one of ten thousand weigh alike), and the pool is judged like one routine's windows on
the `POOLED` signals (no absolute floors: they mean nothing on a ratio).

- **Engine releases** (`releases`, every one, newest first) are not part of a routine's key —
  several land a day, and as a key they would cut every history into windows too short to judge.
  A routine whose own key ALSO changed at the run that first ran the release is `confounded` and
  left out; `smeared` is the median number of OTHER releases inside the pooled windows (0 judges
  the release alone). A release inside a routine change's windows is named beside that change
  (`engines`).
- **Model and rule changes** (`fleet`, one row per kind · name · from → to) carry each routine's
  own verdict, and a verdict pooled over the routines where this was the ONLY change at that run
  (`alone`) — a library migration that rewrote thirty rules at once otherwise handed each of them
  the whole batch's verdict. Where it also changed together with other things, `together` is the
  batch's pooled reading, labelled as the batch.
- **The fleet week by week** (`timeline`, `readmodels/change_timeline.py`) reads the stretch a
  release verdict smears over: per ISO week, every routine counted once, cost signals against the
  routine's own median, rates as they are; the week's releases, its models by `model_id`, and how
  many of its runs were rebuilt or carry no quality. A routine's own weeks
  (`change_effects.routine_timeline`) ride `GET /api/changes/{slug}`.

`readmodels/model_fit.py` groups one routine's runs by what served them (model, `model_id`, effort,
deliberation, trial) on the same signals — the table the config-optimizer reads to judge whether
a model is overkill or not enough, and why a model TRIAL exists: one routine's runs on one model
say nothing about another.

A slug names a routine only for a while: archiving moves its dir to `.archive/<slug>-<run_ts>` and
frees the slug for a new routine (`do-my-taxes` and `steward-hub-maintainer` were each archived and
created again). Runs are grouped by INCARNATION (`readmodels/incarnations.py`) — a run belongs to
the first archive stamped after it started, else to the live routine — so the per-routine changes
are the live routines' alone, while releases, the fleet roll-up and the timeline count the
archived ones too (`archived` names them): a routine that is gone still ran on those releases.
Every reader that compares or sums one routine's runs keys them the same way
(`incarnations.resolver` / `live_runs`): recipe health and its time trend, the run-end
`cost_trend_degraded` check, a trial's run count, and the Stats tab's per-routine spend and
compression rows, where an archived routine is a row of its own under its archive's name.

Flag-first: nothing reverts or proposes on its own.

### History before the record

The record began with 0.398.0 (2026-10-08); the measurement reaches back to the usage stream's
first run (2026-07-12), because a one-shot boot migration REBUILT every earlier run's fingerprint
and quality from what the instance still holds (`migrate_runrecords.py`, with
`runhistory.py` and `runevidence.py`; all three go with the migration). As of the instant each run
STARTED:

| component | rebuilt from | unknown when |
|---|---|---|
| engine | `main`'s REFLOG — when the branch moved, not when a commit was written | before the reflog |
| config, rules held, deliberation, named model | the routine's `routine.yaml` + `tuning.yaml` as committed then, loaded by today's loader and hashed by the live hasher; the run's own status names the deliberation it used | today's loader refuses the old file |
| rule texts | the library's rule file as committed then | — |
| `model_id` | the run's own transcript header, else its status | the run dir is gone |
| model (catalog name) | the routine's own `models.main` | the routine ran on the system model, whose history is on record nowhere |
| effort | — | always: the catalog is unversioned, and before 0.396.0 the subscription transport dropped effort anyway |
| recipe | the record's `recipe_commit`, else the recipe version committed then | — |

`quality` comes from the run's `status.json` and transcript where retention kept them (two runs
in three): a key a file lacks, or a count its release did not RECORD yet
(`runevidence.RECORDED_SINCE`), stays absent — unknown, never zero. The person's interventions
are counted as a live run counts them: mid-run messages that are not an answer the run asked for,
nothing the engine wrote (it stamps a `source` on every note, assist and warning), no report and
no machine channel.

A rebuilt fingerprint says so (`source: "reconstructed"`) and names what it could not recover in
`unknown`. **An unknown component is a wildcard**: it never makes a change, a change's `what`
names only components known on both sides, and every verdict drawn on a rebuilt run carries
`reconstructed`, which the console marks `≈`. The runs fingerprinted live by 0.398.0 were
corrected in the same pass: their config hashed again under the allowlist, and the `model_id`
0.398.0 did not record read from their own files.

The engine reached a run at the daemon's next RESTART, not at the merge, and nothing records the
restart — so a run started between a merge and the restart is attributed to the newer release:
the one error the reflog cannot remove. A pruned run dir an operator still has in a backup can be
STAGED for the migration under `.control/migrations/run-records-evidence/<slug>/<run_ts>/` (its
`status.json` and transcript) before the boot that runs it; the directory is removed once the
rewrite lands. What it did is recorded in `.control/migrations/run-records.json`.

### In the console: production and development apart

The operator's order (2026-10-08) was to *clearly distinguish production related information from
development related information*, without cluttering a console that is text heavy already. The
rail says it first: **Work** (Conversations, Decisions, Messages) and **Fleet** (Routines, Browser,
Desktops) are PRODUCTION — the routines doing their jobs and what they need from you; **Develop**
(Changes, Stats, Library) is DEVELOPMENT — how the system is changing and whether that helps;
**System** keeps Settings and Help. The Develop group is one quiet band of IRIS (the palette's
structure colour): a tinted block of the wide rail, a tinted run of icons on the narrow one, a
tinted segment of the phone's bottom bar — never a row of its own. The development pages carry
an iris `development` kicker over their title and iris section ticks.

- **Changes** (`#/changes`, `views/changes.js`, `GET /api/changes`) — a **Weeks** strip of small
  multiples (`components/sparkline.js`: runs, failed, lines met, tokens× against each routine's
  own median on a log scale clamped ×0.25–×4 with a dashed ×1.00, interventions and challenges per
  run; no reading is a gap, mostly-rebuilt weeks are shaded, the releases first run that week are
  ticks, each week's column is its own readout); **Releases**, the newest 12 then "show all"
  (version · first seen · routines pooled · the pooled verdict · token ratio · met change in
  percentage points; "+N releases in window" and "N left out" only when non-zero; a `too few
  runs` release recedes); **Model & rule changes**, 10 then "show all" (the verdict pooled where it
  was the only change, "alone in A of R"; "with other changes: <verdict> (R routines)" when it also
  came in a batch; each routine's own chip, more than three `too few runs` folded into one count,
  archived routines named "(archived)" and unlinked); and **Routines** (each routine with a
  measured change · how many · the newest verdict · when). `≈` beside a verdict means it rests on
  rebuilt runs (`components/change-folds.js`, which also keeps a list's folds open across a live
  re-read).
- **One routine** (`#/changes/<slug>`, `views/changes-routine.js`, `GET /api/changes/{slug}`) — its
  own Weeks strip (tokens×, turns×, lines met, interventions); its changes newest first (when ·
  what changed, e.g. `recipe 1a2b3c4→3c4d5e6`, `model Opus high→Sonnet high` · runs before →
  after · the verdict chip · three tiny marks for correctness, completeness and effectiveness · the
  engine releases inside its windows, as a range "0.379–0.391 (4 releases)" past two), each row
  opening onto its signal table; consecutive `too few runs` changes fold into one line "N changes
  came faster than it ran · dates", more than three rule entries read "N rules revised", and the
  list shows 10 then "show N older"; the **Model fit** table, where a rebuilt group shows its
  provider id and "≈ N"; and **Recipe health** (the version table, the regression banner with its
  roll-back, the cautions' tallies).
- **The production routine page** (`#/routine/<slug>`) carries exactly one line about any of it,
  under the routine's name (`components/dev-line.js`, `GET /api/changes/{slug}/summary`):
  `DEVELOPMENT · 3 measured changes · latest ▲ IMPROVED`, a quiet link into the routine's view.
  It wears SUMMONS when the newest change regressed or recipe health flags the newest recipe
  change — there a person should look.

The verdict chips speak the chip state vocabulary (`components/change-marks.js`): improved is ok,
regressed the failure tone, mixed the warning one, `no effect` and `too few runs` quiet, and
`measuring` signal without a pulse. Every mark carries a glyph and its word in a tooltip, so no
state is told by colour alone. The pages re-read on a finished run only (and on a reconnect),
debounced (`components/run-finished.js`) — never on `llm_task`/`llm_process`.

## Per-util execution stats (Stats tab → Global utils)

Every util call is counted by outcome in the engine (`RunContext.util_stats`) and folded
into the run's usage record:

- **ok** — exit 0.
- **error** — the util ran and failed (non-zero exit).
- **usage_error** — exit 2, argparse's bad-arguments convention: the deterministic
  "called with wrong syntax" signal (a util not using argparse may exit 1 for
  everything; then its usage errors count as plain errors).
- **missing** — called by a name that isn't in the library.
- **denied** — a permission refusal (reserved util switched off, or the util kind
  excluded by the workflow's `tools:`). Denials are rejected inside the schema-retry
  cycle and never reach the executor, so they are counted at the validation seam
  (`engine/actions.util_rejection_outcome`) — the only place they exist.
- **rejected** — a malformed call (schema/field problems).

User slash commands run the same gates and count the same way. The catalog pseudo-utils
(`list`, `show`) are discovery, not execution — never counted. Nor is a call its run's abort
ended (`aborted` on the observation, exit 130): stopping the run says nothing about the util,
so neither the engine nor the transcript backfill counts it. Subrun records carry their
own counts; parents never fold them in (the read-model sums records at every depth).

`rsched/readmodels/util_stats.py` joins three sources into the Stats tab table:

1. **Library git history** (one `git log` walk, memoized on the library repo's reflog, which
   every commit and pull appends to): created = oldest commit touching `utils/<name>/`,
   last revised = newest.
2. **The stream**: per-run outcome breakdowns, first/last execution timestamps.
3. **Transcript backfill** for pre-stream history: runs whose records lack the `utils`
   key are scanned for util observations (every level of the run tree — a child's own
   children nest under its `sub/` — gzip included), memoized per file behind a stat
   fingerprint and pruned to the runs still uncounted. Backfill sees executions only —
   rejected/denied calls never became observations back then, so those counts honestly
   start at the stream's adoption.

## Output compression (Stats tab → Output compression by routine)

A mechanism that spends run time to save tokens has to be able to show which of the two it is
actually doing, per routine. Compression is engine behaviour rather than a setting, so a routine
with no row is one with no counted run since the tally began, and a row with no applications is
one whose outputs never qualified. Each run tallies its own compression outcomes
(`RunContext.compression_stats`) into its workflow-usage record;
`rsched/readmodels/compression_stats.py` rolls the records up per routine, and `since` names the
oldest counted record so the table never implies a longer history than it has.

The reading is three columns wide: `applied ÷ attempts` is the hit rate, `~tokens saved` is the
recorded estimate (preview characters ÷ 4 — never a billing figure), and `rejected` is the half
with no upside, where the compressor produced a result the engine's own verification refused and
the original was kept. Rejections cost the same wall clock as applications, so a row where they
dominate is a routine paying for the feature and getting nothing back. Mechanism, eligibility and
the verification itself: [docs/output-compression.md](output-compression.md).

## Prompt-cache health (Stats tab → the `prompt cache` card and the `cache` column)

Every slice carries BOTH halves of the cache traffic — reads (`tokens_cached`, ~0.1x
price) and writes (`tokens_cache_write`, ~1.25x) — because the ratio between them is the
only reading that tells a healthy run from a broken one, and the reads alone actively
mislead.

The failure it exists for: when a transport stops resuming its session, the static
system+tools prefix keeps hitting, so `cached_in` stays large and the volume columns look
normal — while the whole conversation is re-WRITTEN every turn instead of re-read. That is
a 12.5x multiplier on identical work, and in September 2026 it ran for four days
(read share 95% → ~49%) and exhausted a weekly subscription limit before anyone saw it;
the run's prompt tokens actually FELL 4x over the window.

- `endpoints.base.cache_read_share(usage)` computes reads ÷ (reads + writes) for one usage
  record, and returns `None` when the transport reports no cache traffic at all — an
  endpoint that does not cache is absent from this reading, never degraded by it.
- The Stats tab shows the instance-wide share as a headline card and per-row shares in
  every slice table, marked red under 50%. The SLICE is the point: the September regression
  was one endpoint going bad next to healthy ones, which a total hides and a per-endpoint
  row makes obvious.
- At run finish the engine emits a `cache_read_degraded` health event when a run's share
  falls under `CACHE_SHARE_FLOOR` (0.5) with at least `CACHE_SHARE_MIN_TOKENS` (200k) of
  cache traffic behind it (`engine/runtime.py`). The event carries `cache_read_share`,
  `cache_read_tokens` and `cache_write_tokens` as structured fields so a health sweep can
  filter on them, and self-audit reads the same log.

Per-turn detail lives in each run's `llm-tasks.jsonl` sidecar: a healthy run's `cached_in`
GROWS turn over turn; a broken one has it PINNED at the static prefix while `cache_write`
tracks the conversation.

## A cooling primary model (`model_failover`, `model_chain_exhausted`)

The same shape of invisible cost, in the model layer. A catalog model's `fallbacks:` chain
makes a cooling primary SURVIVABLE per run — the turn fails over, the run finishes `ok`, and
the extra wall-clock and tokens are charged to nobody's report. So the fleet can spend its
busiest hours on its second-choice model and every health signal reads clean.

Measured on 2026-09-16: 11 of 13 fleet runs opened with `All credentials for model
gpt-6-astra are cooling down` (81 errors across the fleet); all 13 finished `ok`, and the
health stream for that window held exactly one event, about an unrelated oversize file.

- `engine/degrade.py` emits `model_failover` on EVERY switch — the event that answers "what
  is the fleet actually running on today". It carries `model` (the chain HEAD, the key to
  group by), `from_model`, `to_model` and a `reason` (`rate_limit` · `auth` · `server` ·
  `refusal` · `empty` · `other`) as structured fields. Over ten days in September 2026 there
  were 123 switches and one `model_chain_exhausted`, so a stream carrying only the latter
  reported ~1% of the movement: a dead proxy refresh token and a weekly quota pushed 28 runs
  onto metered models at `effort: max` and the operator found it days later, by reading
  transcripts.
- `model_chain_exhausted` is the same seam's harder failure: a role's WHOLE chain unusable
  mid-turn — every member failed hard or is cooling. It carries `model`, `last_model` and
  `cooldown_s`. A `model_failover` costs money; a `model_chain_exhausted` costs the run.
- Read both by the HEAD, not by the count: one event is a provider hiccup, while a run of
  them sharing one `model` is a primary that is not serving the fleet. The per-run transcript
  `error` event with its `failover` payload stays the record of which model served a given
  turn; these are the fleet-level aggregate that no per-run artifact can give you.
- A switch is FORWARD-ONLY inside a run (`endpoints/failover.py`), so repeated
  `model_failover` events for one run_id mean successive members failing, never the same
  primary flapping back in every five minutes.

## Blocked work (`/api/health/blocked`)

Everything above measures runs that HAPPENED. Eight health events record the opposite —
`fire_refused`, `lane_fire_refused`, `lane_fire_paused`, `lane_chain_stopped`,
`lane_chain_member_skipped`, `scheduler_tick_error`, `trigger_capped` and `commit_failed`
— and no run page, Items row or Stats slice can carry any of them: the first seven produce
no run at all; a commit that did not land happens after a run's finish is written, or
outside any run (a web edit, a boot seed sync, a migration). They had fourteen writers and
no reader inside the product: the nightly audit opened `.control/health-events.jsonl` over
ssh and the console showed none of it, which is how F316's week of missed lane fires
passed with zero signal.

`readmodels/health_stream.py` is the ONE parser of that file and the fold behind
`GET /api/health/blocked?days=<1..90>`: one row per (event, subject) with `count`,
`first_ts`, `last_ts` and the NEWEST `detail`, newest first, plus `vocabulary` — what each
event name means — so a console renders a label it was not compiled with. `subject` is the
event's own `routine` field: a routine slug for a refused fire or a capped trigger, an
opaque LANE ID for a lane's refused, paused or stopped fire (resolve it against the lane
store, never by reading a prefix), the repo's directory name for a commit that did not land
(a routine's slug, or the library's own directory), empty for a scheduler tick. `total: 0`
is the healthy reading.

What both folds read — the parsed stream, each event's stamp already read as an instant — is
memoized on the stream's stat fingerprint with single-flight misses: this rides a bus-event
refresh path, and an un-memoized parse per request is what starved the daemon behind
`/api/items` and `/api/questions`. The WINDOW is cut at every request over that shared list,
because it moves with the clock while the file stands still: a fold memoized on the file alone
answered for the moment it was first computed until the next append. Stamps are compared as
instants, never as text — `now_iso` writes the host's local time with its offset, so a string
compare against a UTC cutoff moved the window's edge by that offset.

## Who reads the flags

A routine's development view (the health banner and the one-click revert, with the production
routine page's one development line turning summons while a flag stands) and **routine-improver's
target ORDER**: its `orient` stage attaches the same two window medians and the same 1.5×
ratio to every candidate, and `select-targets` puts the flagged ones first, most-moved
first. That is what the flags are for — a sweep spends its hour where the numbers moved,
and this is the only place on the instance that compares a routine against its own past.

## Follow-ups

- Auto-revert by the routine-improver (act on a flag instead of ordering its sweep by it)
  is deliberately out of scope — flag-first until the heuristic has earned trust.
