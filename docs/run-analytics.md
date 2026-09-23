# Run analytics: recipe-version health, per-util stats & prompt-cache health

The scheduler improves its routines through use — the routine-improver edits recipes
directly. Two measurement layers close that loop: every run is attributed to the **recipe
version** that produced it, and every util call is counted by **outcome**. Both survive
run-dir retention because they ride the durable workflow-usage stream
(`~/routines/.control/workflow-usage.jsonl`).

A third layer measures what a run COSTS rather than what it did: prompt-cache health, the
one reading that separates carrying context cheaply from paying for it twice. A fourth measures
what an optional efficiency feature RETURNS — output compression, per routine, on that same
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

## The health view (routine page → Recipe health)

`rsched/readmodels/run_health.py` buckets the routine's depth-0 usage records by recipe version:
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

## Output compression (Stats tab → Output compression by routine)

An optional feature that spends run time to save tokens has to be able to show which of the two
it is actually doing, per routine — otherwise it is enabled by default on a guess. Each run tallies
its own compression outcomes (`RunContext.compression_stats`) into its workflow-usage record;
`rsched/readmodels/compression_stats.py` rolls the records up per routine, joining each routine's
CURRENT mode so an empty row reads as "switched off" rather than "nothing qualified".

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

Everything above measures runs that HAPPENED. Six health events record the opposite —
`fire_refused`, `lane_fire_refused`, `lane_chain_stopped`, `lane_chain_member_skipped`,
`scheduler_tick_error`, `trigger_capped` — and each of them produces no run, so no run
page, no Items row and no Stats slice can carry it. They had fourteen writers and no
reader inside the product: the nightly audit opened `.control/health-events.jsonl` over
ssh and the console showed none of it, which is how F316's week of missed lane fires
passed with zero signal.

`readmodels/health_stream.py` is the ONE parser of that file and the fold behind
`GET /api/health/blocked?days=<1..90>`: one row per (event, subject) with `count`,
`first_ts`, `last_ts` and the NEWEST `detail`, newest first, plus `vocabulary` — what each
event name means — so a console renders a label it was not compiled with. `subject` is the
event's own `routine` field: a routine slug for a refused fire or a capped trigger, an
opaque LANE ID for the three lane events (resolve it against the lane store, never by
reading a prefix), empty for a scheduler tick. `total: 0` is the healthy reading.

Both folds are memoized on the stream's stat fingerprint with single-flight misses: this
rides a bus-event refresh path, and an un-memoized parse per request is what starved the
daemon behind `/api/items` and `/api/questions`.

## Who reads the flags

The routine page (the health banner and the one-click revert) and **routine-improver's
target ORDER**: its `orient` stage attaches the same two window medians and the same 1.5×
ratio to every candidate, and `select-targets` puts the flagged ones first, most-moved
first. That is what the flags are for — a sweep spends its hour where the numbers moved,
and this is the only place on the instance that compares a routine against its own past.

## Follow-ups

- Auto-revert by the routine-improver (act on a flag instead of ordering its sweep by it)
  is deliberately out of scope — flag-first until the heuristic has earned trust.
