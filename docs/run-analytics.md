# Run analytics: measuring changes, recipe-version health, per-util stats & prompt-cache health

The scheduler improves its routines through use — the routine-improver edits recipes
directly. Two measurement layers close that loop: every run is attributed to the **recipe
version** that produced it, and every util call is counted by **outcome**. Both survive
run-dir retention because they ride the durable workflow-usage stream
(`~/routines/.control/workflow-usage.jsonl`).

That stream holds one record per LEG, not per run: a continued or resumed run appends another
record under the same `run_id`. A leg's spend (`tokens`, `cached_in`, `cache_write`, `volume`,
`cost`, `notional_cost`) and `compression` tally are its own,
while `turns`, `utils`, `referrals` and `asks_deferred` are the run's running totals (a resumed
leg's boot reseeds them). Every reader below therefore counts RUNS —
`readmodels/usage_stream.usage_runs`, the stream folded once per change: the per-leg fields
summed, the running totals read from the newest leg. Summed leg by leg, a conversation answering
ten replies with one util call each read as 55 calls.

A third layer measures what a run COSTS rather than what it did: prompt-cache health, the
one reading that separates carrying context cheaply from paying for it twice. A fourth measures
what an efficiency mechanism RETURNS — lossless output compression, per routine, on that same
durable stream.

And one layer is not about the process at all: a routine's **outcome** — what it exists to
produce for its principal, measured every run against what it cost ([Outcomes](#outcomes-what-a-routine-exists-to-produce)).
Every other layer here can read healthy while a routine produces nothing.

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
ordinary config — `trial: {id, models, efforts, runs, reason}` in routine.yaml
(`config/trialconf.py`), e.g. `{id: t-20261008-sonnet-high, models: {main: Sonnet},
efforts: {main: high}, runs: 5, reason: …}` — that a
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
  `trial.json` (`{id, models, efforts, runs}`) in its new run dir before the engine exists (the runner, beside
  a hand-started run's brief), and the runner names the models to `engine-run` as `--model
  role=name` and the efforts as `--effort role=level`. The engine runs the routine on them for that run (children included), and the run's
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
| `BALLOON_RATIO` | 2.7× | median turns/volume growth that marks real ballooning — the MEASURED p95 of adjacent-window variation on this instance (1063 adjacent 5-vs-5 windows of tokens, 2026-10-13: median 1.04, p75 1.38, p90 2.09, p95 2.73, max 7.16; re-measured on VOLUME 2026-10-17 over 740 windows: median 1.10, p90 2.04, p95 2.51, max 5.76), not a guess |
| `TURNS_FLOOR` / `VOLUME_FLOOR` | +5 / +2M | absolute floors so a 2→3-turn routine's ratio never flags noise; 2M is the median absolute change between adjacent windows' volume |

The cost half reads VOLUME — the whole context the model processed (below, "What a run
costs") — not `tokens`, which leaves the prompt cache out: a routine re-reading a 400k prefix
every turn never ballooned by tokens. A run the stream could not measure (`volume: null`) is no
reading, never a zero.

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

#### Whose regression is it — attribution before the finding names anyone

The reading above says a routine's window got worse. It does NOT say why, and the alarm
asserted an answer anyway: every flagged window was filed as THAT ROUTINE's regression. On
2026-09-27/28 a host-wide condition degraded everything that ran, and the stream carried three
findings naming three routines for one problem with the box
(`llmsectest-weekday:20260927-060004`, `library-sync:20260928-000003`, F574, F568).

So before the finding names anyone, the same window is read across the fleet
(`readmodels/trend_attribution.attribute`): every OTHER routine with at least `MIN_RUNS` runs
inside the subject's calendar span and `MIN_RUNS` before it is judged by `regression_flag`
against its own baseline — one heuristic, the same thresholds, two subjects. Three verdicts,
carried on the row as `scope`:

| `scope` | when | what the row says, and who it names |
|---|---|---|
| `host` | ≥ `HOST_MIN_PEERS` (3) comparable routines **and** a strict majority (> `HOST_SHARE` = 0.5) degraded with it | leads `host-wide: … a condition of the host, not one routine's regression`; carries **no** `routine`, and `detected_on` names where it was read |
| `routine` | ≥ 3 comparable routines and no majority | the reading, then `…so this degradation is this routine's own` — the routine is named, as before |
| `unattributable` | fewer than 3 comparable routines ran the window | the reading, then `cannot attribute: only N other routines had comparable runs …` — filed under the routine, with no claim about its cause |

`HOST_SHARE` is a **strict** majority because it was measured, not guessed: over this
instance's own stream (2026-10-21 — 1,738 folded depth-0 runs, 217 actors, 134 moments where
the trend flagged) the share of comparable routines degrading alongside a flagged one ran
median 0.11, p90 0.36, p95 0.40, **max 0.50**. Exactly half has happened once; more than half
never has, so the rule reclassifies none of this instance's real history as a host condition.
7 of those 134 moments (5.2%) had fewer than three comparable routines — the unattributable
case, and the 2026-09-27/28 firings are themselves in it: exactly one other routine had
comparable runs those nights, so an honest "cannot attribute, 1 other routine in the window"
is what the row now says.

An empty `routine` on a host row is `backup_stale`'s idiom for an instance fact — and it is
also what collapses the three rows into one, since the 12-hour cooldown below keys on
(event, routine) and every host row shares that empty key whichever routine's run detected it.
A degradation is reported under all three verdicts: what attribution changes is who is named
and what the sentence claims, never whether the reading appears.

It is pushed at most ONCE PER (event, routine) PER 12 HOURS, because a run END is not a run:
a resumed run finishes once per resume, so one `llmsectest-weekday` run filed 26 of these over
5h36m and the next day's filed 22 — 173 of the live stream's 1,289 rows for a condition only 80
runs had produced. Deduping the payload cannot collapse them: each finish appends its own usage
row, which shifts the 5-run window the trend is read over, so those 22 rows carried six
distinct `detail` strings. The suppression is on the clock instead, in `log_health_event`
itself (`health_events.REPEAT_COOLDOWN_HOURS`, which also governs `cache_read_degraded`), so no
call site can forget it; it is the `backup_watch`/`tickguard` idiom, read back from the stream
rather than latched in memory because a run-end writer has no long-lived object. 12 hours was
measured against the real stream: it collapses that worst run from 26 rows to 1 and reaches the
same 80 rows a per-run latch would, while swallowing the first row of only 2 of 80 runs (24
hours swallows 19 — a routine firing twice a day would lose its second reading). Only a
STANDING CONDITION is governed this way: an incident, a refusal or a cap that happened twice is
two facts, and nothing in `health_stream.BLOCKED_EVENTS` may ever be cooled down, since the
console's blocked-fleet fold counts those rows.

### How a partial finish ended

`endings` (same payload) counts this routine's last week of `budget_exhausted` and
`run_partial` health events. Both land in the usage stream as `partial`, so a routine that
outgrew its ceiling and one that keeps finding a source down read identically there;
only the health stream says which budget forced a finish, and `last_detail` names it.

A third count, `corrected`, is the legs that ended in one of those two and whose RUN was then
resumed and finished `ok` (`rsched/runcorrection.py`, F637/F703). The health stream is
append-only, so before this a resumed-to-ok run said "failed" there forever while its own
status.json, its result.md and the usage stream's newest-leg fold all said `ok` — two surfaces
permanently disagreeing about the same run. An `ok` finish now files a `run_corrected` event
carrying `superseded`, the event names it resolves, and both folds read it: a superseded row
leaves the blocked-work surface, and a `lane_chain_stopped` whose `stopped_by` member run was
corrected KEEPS its row (those members really did not run) with its not-ok verdict marked
superseded. The originals are never edited — a correction supersedes, it does not erase.

### One-click roll-back

`POST /api/routines/{slug}/recipe/revert {commit}` restores the recipe files to their
state just before that commit and commits only those paths — `routine.yaml` (the user's
config) and `state/` are never touched. The revert commit is itself the next recipe
version, so health tracking continues seamlessly. Made while a run is active, the revert
is queued and applied when the run ends (D78-A, `pending_edits`), like a recipe file edit.

## Measuring changes (`/api/changes`, Develop → Changes)

The recipe-health flag above sees one kind of change (the recipe) through four coarse signals
(status, turns, volume, deferred asks). Every change that can alter what a routine does is now
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
  worked, wall time, cache reads and writes — and `frictions`, what went WRONG by signature
  (`engine/frictions.py`, counted from the run's own transcript at its end; below). Run-cumulative
  (reseeded on a resume), so the fold keeps the newest leg's values.

A routine's input differs from run to run, so no run's OUTPUT is scored. What stays comparable is
the process: the same routine answering for the same Done when should be about as correct, as
complete and as cheap from one run to the next. `readmodels/change_signals.py` reads fifteen
signals per run in three dimensions — CORRECTNESS (failed or aborted, claims challenged and
disputed, holds, failed util calls), COMPLETENESS (owed lines met, lines unmet, partial finishes,
stages skipped) and EFFECTIVENESS (median volume, turns and minutes, interventions, schema
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

### The moment test

The rate verdict needs three runs of ONE key on each side, and most changes never get them. Every
change therefore also carries a second reading that needs far fewer, taken from Harness²
(arXiv 2610.14224): a failure that REPEATED before an edit is a real property of the harness, and
the edit is judged by whether THAT failure came back, not by whether an average moved
(`readmodels/change_frictions.py`, the `frictions` field of every change).

- **Signatures** (`engine/frictions.py`) name a failure the same way in every run of every
  routine, read from events the engine already writes: `schema`, `script:<name>` and
  `util:<name>` (a non-zero exit), `edit_miss`, `read_miss`, `memory_cap`, `refused:<kind>`,
  `deferral:<rung>` (the finish-gate rung that set the finish aside), `budget:<resource>` (from
  the `budget` key the reserved-turn injection now carries) and `unmet:d<n>` (the final
  accounting). A `shell` exit, an endpoint error and a refusal are not signatures — grep's "no
  match", and the transport failing, which no change to a routine addresses — nor is a goal,
  which never recurs. A run is counted once per signature, however often it hit it.
- **The verdict.** A signature carried by at least two of the runs before the change is CHRONIC;
  after it, it is `fixed` (in no run) or `persists`; one in no run before and two after was
  `introduced`. Two runs a side are enough to say it (`status: judged`); fewer reads `measuring`
  (the newest change) or `too few runs`.
- **Confounded, said out loud.** Where a change's own segment is too short, its window reaches
  across the neighbouring change until it has its two runs, and names each one crossed in
  `confounded` — the reading then belongs to those changes together, and the page marks it ⚠.
- **Aims.** A recipe commit may carry `Addresses: <signature>[, …]` on its own line. Each named
  signature is judged on its own: `fixed`, `persists`, or `unfounded` when the runs before the
  change never repeated it ("an edit that cannot name the moment is speculation"). An unreadable
  commit range leaves `aims` null — unknown, never "none declared".
- **Unknown is not clean.** A run with no `frictions` (recorded before 0.454.0 and not
  backfilled, or no transcript to read) is left out of both sides. The one-shot boot backfill
  (`migrate_frictions.py`) counted every run whose transcript retention kept, on its newest leg.

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
| effort | the routine's own `efforts.main` (or `system_effort`) | a rebuilt run before 0.420.0, when effort lived on the unversioned catalog row — unknown, and before 0.396.0 the subscription transport dropped it anyway |
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
  opening onto its signal table and its moment table; a last **failures** column carries the
  moment test (`components/change-moment.js`: `✓N gone` in the ok tone, `+N new` in the failure
  tone, an aim's verdict, ⚠ when the window was confounded); consecutive `too few runs` changes
  the moment test could not judge either fold into one line "N changes came faster than it ran ·
  dates" (one it judged keeps its own row), more than three rule entries read "N rules revised", and the
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

The Stats tab computes that table live per request. The PERSISTED copy
(`util_stats.snapshot_path()`, under `XDG_STATE_HOME` so a Landlock-jailed util can read it —
the `util-stats` global util is its only reader) is written by exactly one process: the
daemon's `daemon/util_stats_watch.py`, a bus subscriber that recomputes once per burst of
`run_*` events, in a thread, debounced (5 s of quiet, capped at 60 s). Every engine used to
write it at its own run end — all three sources re-read per run, for a file no engine reads —
and a burst of finishes paid for it once each. Consequences worth knowing: a run started
outside the daemon (`rsched run-once`) publishes no bus event and so does not refresh the
snapshot by itself (the next daemon-fired run does), and the file can lag a just-finished run
by the debounce window. Neither touches the Stats tab, which never reads it.

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

## Outcomes: what a routine exists to produce

Every reading above judges the PROCESS: did the run fail, did it meet its Done-when lines, did it
cost more than its usual. A routine can pass all of them and produce nothing — freelance-radar
did for four months (89 applications, zero interviews), grants-radar for 14 runs (no new
programme), and every judge on the instance, self-audit's included, read the routine's own
account and called it healthy.

So a routine declares its OUTCOME contract in routine.yaml (`outcome:`, `config/outcome.py`):
its `principal` and up to six `measures`, each `{id, label, kind, better}` — a `count` is a
running total (applications sent, euros won), a `level` a current reading (a weight, the
programmes on the list). Its own `scripts/outcome.py` reads its state and prints one JSON
object `{<id>: <number>}`; the engine runs it in the run's jail after the last turn of every
depth-0 run (`engine/outcome.py`) and records what it printed — checked for shape only — as
`outcome_measures` in status.json and in the usage row, `{"values": …}` or `{"error": …}`. A
failed or malformed reading is an error, never a zero.

`readmodels/outcome_series.review` reads that back over a window of weeks (default 4) — for each
measure the readings, a count's change or a level's first → last, and `per_unit`, the window's
volume per unit of progress (null when it made none); beside it the window's runs, volume and
notional dollars. `GET /api/changes/{slug}/outcome?weeks=N` serves it and the Outcome panel
LEADS the routine's development view (`components/outcome-panel.js`). It reads nothing the
routine says about itself. routine-improver's outcome review reads the same series first, for
every target, before any health claim (its recipe), and the setup surface's outcome row makes
the contract mandatory (docs/rules-permissions.md, "The outcome row").

A NEW routine is born with it. `create_routine` refuses to create or queue anything without
`produces` — `principal: <who>` and one `<id> · count|level · higher|lower — <what it counts>`
line per measure, in the person's words (`engine/outcome_words.py`) — because "what is this for,
counted how" is the question freelance-radar was never asked. Scaffold writes it into
routine.yaml as declared (the person's confirmation of the draft is the accept), the recipe
generator must give `## Done when` a line naming the measures, and the routine's first run writes
`scripts/outcome.py` from the records its stages keep — until it does, the outcome row reads
`unmeasured` and the engine records `{"error": …}`, never a zero.

## What a run costs (`volume` and `notional_cost`)

`tokens` — fresh input + output — is what a token BUDGET meters and nothing more: adapters keep
prompt-cache traffic out of it so a budget keeps its meaning. It is not a cost. freelance-radar's
last run read `tokens: 650,520, cost: 0.0` while it pushed 33 million cached tokens through the
model, and every ranking that read `tokens` (routine-improver's orient, the trend alarm, the Stats
tab, self-audit's cost alarm, which F545 then called "real but benign") put it among the cheap
routines. On a subscription transport `cost` is 0.0 on every call, so it ranks nothing either.

Every usage row therefore carries the spend in full (`usage_log.spend`, one place):
`tokens`, `cached_in`, `cache_write`, `volume` (all four token classes together — the whole
context the model processed) and `notional_cost` (the leg at list price). **Every cost ranking
reads `volume`**: the trend alarm and the version buckets (`volume_median`), the change signal
(`volume / run`, floor 2M), model fit, the Stats slices and the monthly spend series (which also
shows `notional`), the dashboard's spend line and heartbeat.

`notional_cost` comes from `endpoints/prices.py`, priced at the instrumentation seam the moment a
completion finishes — so a recorded figure is what those tokens cost THEN. One source per model,
decided by its ROUTE, never a chain of guesses: a metered route is priced by its provider (the
bill it REPORTED on the call, else the prices it publishes for that exact id — OpenRouter's
`/models`, Nano-GPT's `/api/models`); a subscription route at the model's LIST price from the
public reference catalog under the name vendors share (a proxy's `claude-haiku-4-5-20251001` is
OpenRouter's `anthropic/claude-haiku-4.5`). The rates are re-read daily with the model limits into
`.control/model-prices.json`. What no source publishes is UNPRICED — a model without a price, or
a call that used a token class whose rate its source does not publish, gets no figure at all, and
the cache's `unpriced` list names every such configured model; nothing is estimated, defaulted or
borrowed. Rows from before 2026-10-17 carry `notional_cost: null` (what those tokens cost at the
prices of the day was never recorded), and their `volume` was backfilled from what each run
itself recorded (`migrate_usage_volume.py`), `null` where nothing did.

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
  filter on them, and self-audit reads the same log. Like the cost-trend flag it is a STANDING
  CONDITION, so it is filed at most once per routine per 12 hours
  (`health_events.REPEAT_COOLDOWN_HOURS`): a resumed run reaches run end once per resume, and a
  degraded prefix is one fact however many times the run finishes.

Per-turn detail lives in each run's `llm-tasks.jsonl` sidecar: a healthy run's `cached_in`
GROWS turn over turn; a broken one has it PINNED at the static prefix while `cache_write`
tracks the conversation.

The sidecar's phases are `started`, `attempt`, `finished` and `failed`, all on one call id.
An **`attempt`** row (F655) means the call is being RETRIED: `attempt` is the try now starting,
`tries` the total allowed, `after` what ended the previous one (a 600 s read timeout, a 429, a
5xx) and `retry_in` the wait before it. A run that looks frozen is read here: three attempts of
600 s used to hide behind one `started` row, so a retrying call and a thinking one were
indistinguishable. In the LLM activity dock the same record shows as the status `retrying` —
warn ink, the row saying `attempt 2 of 3 — after …` — and it still counts as in flight.

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

Everything above measures runs that HAPPENED. 18 health events record the opposite —
`fire_refused`, `lane_fire_refused`, `fire_paused`, `routine_catchup_declined`,
`lane_fire_paused`, `lane_catchup_declined`, `lane_chain_stopped`,
`lane_chain_member_skipped`, `scheduler_tick_error`, `trigger_capped`, `commit_failed`,
`backup_stale`, `migration_expired`, `migration_due_soon`, `obligation_overdue`,
`finish_gate_failed`, `wrong_turn_detected` and `model_chain_exhausted`
— and no run page, Items row or Stats slice can carry any of them: the first seven produce
no run at all; a commit that did not land happens after a run's finish is written, or
outside any run (a web edit, a boot seed sync, a migration); a finish gate that RAISED
(F678) leaves a run that reads as finished on every surface, because it did finish — on its own
authored status and summary, with the checks that should have judged that ending never run; a
turn that came back WRONG (below) is a turn paid for at full prompt price whose work was
re-driven, which a run finishing `ok` shows nowhere; and an EXHAUSTED CHAIN (F716) is a turn that
could not be taken at all — every model in the role's chain failed hard or was cooling. That last
one does leave a failed run page, which is why it was left off this fold for so long, but when
the cause is the host's shared proxy the failure is never one routine's: on 2026-10-19 eight such
events, every one a claude-proxy 503 `auth_unavailable`, killed legs of three routines and a
per-routine run page is precisely the surface that cannot say "the fleet has no model". They had fourteen writers and
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

**A corrected run leaves this fold** (F637/F703). A run that ended not-ok, was resumed and
finished `ok` files `run_corrected` (`rsched/runcorrection.py`), and the fold resolves it: the
superseded row — the `attempts_exhausted` turn that ended the run, say — stops being counted,
because the work it owed was done after all. Two rows deliberately do NOT disappear. A
`lane_chain_stopped` whose `stopped_by` member run was corrected KEEPS its row with
`corrected: true` and a detail saying the not-ok verdict is superseded: the chain's remaining
members still never ran, which is the whole claim of that event, and reopening a closed chain
is a bigger change than correcting a record. And a `wrong_turn_detected` whose reason is
`schema_rejected`, `repeat_invalid` or `action_repeat` stays exactly as written — that turn was
paid for at full prompt price and a later resume does not give the spend back.

**And the console reads it** (R2653, 0.433.0): the Stats page's **Blocked work** section
(`static/views/stats-blocked.js`) fetches that endpoint on its own window and renders one row per
folded event — the server's `means` as each row's label, so the panel carries no vocabulary of
its own. For three years the endpoint had no console consumer at all, while its handler docstring
explained that it ships `vocabulary` "so the console renders a label it was not compiled with".
The panel renders **when there is nothing too** — an absent section and a healthy one are
indistinguishable, and only one of them reassures, which is also why `backup_stale` was added to
the vocabulary after nineteen unnoticed nights. A lane subject links to the Routines page and
never to a route built from the opaque id; an empty subject reads as "the instance".

Owed WORK lands here too (`daemon/owed_watch.py`, daily): `obligation_overdue` names a routine
whose `state/obligations.json` — what a person asked of it or approved, carried from run to run
until one meets it (engine/obligations.py) — has held an item open longer than a week, with the
item and what the last run left unmet. Its sibling never reaches this stream: a REPORT nobody
owns for a week (addressed to nobody, or to a routine that starts no run) becomes an
`unowned-report` decision on the Decisions page instead — route it to a routine or close it —
because a homeless report needs an owner chosen, not a count raised.

A WRONG TURN lands here too (`engine/wrongturn.py`, R2652): `wrong_turn_detected` is a turn that
came back WELL-FORMED AND WRONG, filed with `reason`, `turn`, `model` and `effort` as structured
fields. The seam it closes is a documented one — `engine/degrade.py` is headed *"when a turn comes
back WRONG"* and carries four ordered nets (two window shrinks, a media fallback, a chain advance),
and **all four fire on a turn that failed to ARRIVE**. A reply that arrives whole and is wrong is,
to that ladder, a success: the engine rejects it, corrects the model, re-drives the same turn, and
the run finishes `ok`. The only trace was a transcript `error` event plus two per-run counters
(`schema_retries`, `schema_forcefails`) that reach status.json and the usage record — so the
FLEET rate, and what turn of a run it happens at, were readable nowhere.

Four reasons, each a failure the engine already detects, none of them a new judgement about a
turn: `schema_rejected` (an invalid action — unparseable, schema-invalid, or refused by a
per-kind/capability/grant check), `repeat_invalid` (the IDENTICAL invalid reply again after being
corrected — a correction that taught nothing), `attempts_exhausted` (every schema attempt spent,
no action accepted, the run ends), and `action_repeat` (a schema-VALID action landing for the
third time in a row — `loop.REPEAT_WARN`, where each action is individually well-formed and the
run has stopped moving). ONE row per (reason, turn): a turn rejected three times is one wrong
turn, and the per-attempt record is already in that run's transcript.

It is a **MEASUREMENT and nothing else** — no effort is changed, no model is swapped, no retry is
added. That is deliberate and is the first increment of R2652: the rate it accumulates, read off
this fold, is what a later effort ladder would have to be justified by, and the rows double as
the `(turn, effort-that-sufficed)` corpus the token-lab experiments say this instance lacks. Read
it by `reason` and by `turn`, never as a bare count: a single `schema_rejected` that recovered on
the next attempt costs one extra attempt, while a run of `attempts_exhausted` or `action_repeat`
on one routine is a model that cannot hold the schema for the work that routine does.

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
