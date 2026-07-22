# Run analytics: recipe-version health & per-util stats

The scheduler improves its routines through use — the routine-improver edits recipes
directly. Two measurement layers close that loop: every run is attributed to the **recipe
version** that produced it, and every util call is counted by **outcome**. Both survive
run-dir retention because they ride the durable workflow-usage stream
(`~/routines/.control/workflow-usage.jsonl`).

## Recipe versions

A routine's recipe is `main.md` + `stages/` + `traits/` + `tuning.yaml` — exactly the
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

## The health view (routine page → Recipe health)

`rsched/run_health.py` buckets the routine's depth-0 usage records by recipe version:
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
budgets IS a degradation. The flag renders as a banner on the routine page naming the
commit and the numbers behind each reason. **Flag-first**: nothing reverts automatically.

### One-click roll-back

`POST /api/routines/{slug}/recipe/revert {commit}` restores the recipe files to their
state just before that commit and commits only those paths — `routine.yaml` (the user's
config) and `state/` are never touched. The revert commit is itself the next recipe
version, so health tracking continues seamlessly. Guarded like every web-side routine
edit: 409 while a run is active.

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
(`list`, `show`) are discovery, not execution — never counted. Subrun records carry their
own counts; parents never fold them in (the read-model sums records at every depth).

`rsched/readmodels/util_stats.py` joins three sources into the Stats tab table:

1. **Library git history** (one `git log` walk, memoized on HEAD): created = oldest
   commit touching `utils/<name>/`, last revised = newest.
2. **The stream**: per-run outcome breakdowns, first/last execution timestamps.
3. **Transcript backfill** for pre-stream history: runs whose records lack the `utils`
   key are scanned for util observations (root + sub transcripts, gzip included),
   memoized per file behind a stat fingerprint. Backfill sees executions only —
   rejected/denied calls never became observations back then, so those counts honestly
   start at the stream's adoption.

## Follow-ups

- Auto-revert by the routine-improver (act on the flag instead of just raising it) is
  deliberately out of scope — flag-first until the heuristic has earned trust.
