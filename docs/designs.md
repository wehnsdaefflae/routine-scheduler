# Designs not yet built

Specs for work that is **decided but unbuilt**. Each entry states the problem from real
evidence, the shape, and the FIRST increment — enough that whoever picks it up is not
re-deriving it. An entry is deleted the moment it ships (its narration moves to the
subsystem doc it belongs to); an entry that stops being wanted is deleted too. Nothing here
describes current behaviour, so nothing here is a reference for how the system works today.

**Who builds from this file.** The `self-audit` routine reads every `## ` heading here at
orient as one of the three sources of its decided-work queue, gives each an `in_progress`
decision row whose detail names this file as where it was decided so the Items page can see
it, and deletes the entry in the commit that ships it. Adding an entry here is therefore an
order, not a note: write one only for work that is actually decided, and delete one the
moment it stops being wanted.
From 2026-08-26 to 2026-09-22 this file had no reader at all: five entries accumulated and
none was built, the oldest waiting thirty-three days through roughly a hundred and eighty
releases, because the builder's queue could not see it.

Started 2026-08-26 against 0.230.x, on the operator's order to clear the queued design
backlog. An entry headed by an item id answers that Messages-page finding; an entry
decided in conversation before any finding exists says so and carries none.

---

## Structured outputs instead of forced tool use (decided 2026-09-11, BLOCKED on verification)

**Problem.** `endpoints/anthropic_api.py` enforces the action schema with a single forced
tool call — `tools=[{name: "action", input_schema: <schema>}]` plus
`tool_choice: {type: "tool", name: "action"}`. That shape is being retired upstream: forced
tool use (`tool_choice` `any`/`tool`) returns a **400** on Claude Fable 5.1 / Mythos 5.1, so
this transport simply cannot reach that model tier. The modern equivalent is structured
outputs — `output_config: {format: {type: "json_schema", schema: <schema>}}` — where the
reply comes back as a TEXT block containing schema-valid JSON rather than a `tool_use` block.

There is no second prize here any more, and the entry must not be read as if there were: the
claim that runs execute with extended thinking OFF is FALSE on the current fleet. The live
system model is `Opus high` = `claude-opus-5`, and on Opus 5 / Sonnet 5 / Fable 5 omitting
`thinking` runs ADAPTIVE thinking by default, while `output_config.effort`
(`endpoints/anthropic_api.py`) already steers its depth on every turn. Nothing about thinking
is owed; what remains owed is the format swap below.

**Verified 2026-09-11 against the live transports** (probe:
`~/.config/routine-scheduler/so-probe.py`, run inside the container — `cliproxy` resolves
only on the compose network):

| | forced tool use | structured outputs | strict tool + auto | adaptive thinking |
|---|---|---|---|---|
| claude-proxy / the Claude model | ok | **ok** (`blocks=['text']`, valid JSON) | ok | ok |
| codex-proxy / gpt-6-astra | *429 — quota* | *429 — quota* | *429 — quota* | *429 — quota* |

**Why it is blocked.** `codex-proxy` serves `gpt-6-astra`, the system model for 28 of the 33
live routines, and it was rate-limited (`usage_limit_reached`) at every probe attempt that
day. It fronts an OpenAI model over the anthropic wire, so there is no reason to assume it
maps `output_config.format` the way the Claude-backed proxy does — it has to be measured.
The house rule forbids shipping a tolerant dual path, so this is a HARD swap or nothing, and
a hard swap that 400s on the system model breaks the fleet at the next scheduled run.

**First increment.** Re-run the probe when `gpt-6-astra` quota has reset. If structured
outputs works there too: swap `tools`/`tool_choice` for `output_config.format` in
`AnthropicEndpoint.complete`, change `_parse` to read the JSON out of the text block instead
of the `tool_use` block, and make sure `actionschema` emits `additionalProperties: false`
(structured outputs wants it — it already is, throughout `actionschema.py`, so nothing is owed there).
The existing 400-degradation path DOES need changing: it
currently strips the whole of `output_config` on an effort-related 400, which would take the
FORMAT with it and leave a turn with no schema enforcement at all — make it
`output_config.pop("effort")` so the format survives. The one genuinely open question is the
probe: that same codex-proxy quota 429 has recurred since, so the probe has simply not been
re-run. If structured outputs
does NOT work on codex-proxy, the swap stays unbuilt until that transport changes — and the
Fable tier stays unreachable, which is the cost of keeping the fleet up.

---

## F363 — failover support: a per-stage distillate

**Problem.** A catalog model may declare `fallbacks:` (endpoints/failover.py). When the
primary fails hard the run continues on a weaker model — with the *same* prompt. The recipe
was written for the strong model: it says WHAT to do and leaves the how to judgment, which
is exactly what the weaker model does not have. So failover keeps the run alive and quietly
degrades the work.

**Decision (D95, operator 2026-08-20, free text; recommendation A).** Support the fallback
model with a per-STAGE *distillate* — a short, concrete crib for that one stage — injected
**only when failover has engaged**. Not a second recipe, and never in the normal prompt.

**Shape.**

- A distillate is one file per stage: `<routine>/stages/<name>.distillate.md`, ≤ ~25 lines.
  It holds what the strong model actually DID in that stage, reduced to instructions a
  weaker model can follow: the concrete tool calls in order, the shape of the output, the
  two or three traps previous runs hit. It names capabilities, never utils — the
  recipe rule (CLAUDE.md) applies to it unchanged.
- **Derivation is a meta-routine's job, not the engine's.** routine-improver already reads a
  routine's own finished runs; it writes the distillate from runs that (a) completed on the
  routine's `main` model and (b) reached that stage. A stage with fewer than N such runs gets
  no distillate — an invented crib is worse than none.
- **Injection.** `engine/completion.py` already logs the failover switch as a transcript
  `error` event carrying a `failover` payload. That is the trigger: from the moment a run
  switches down the chain, the composer appends the CURRENT stage's distillate to the turn's
  context, under a heading that says plainly what it is ("You are running on a fallback
  model. Here is how this stage was carried out on the primary."). It is dropped again if
  the run climbs back to the primary.
- **The caching contract holds** (CLAUDE.md: the message list is appended-to, never mutated).
  Failover already invalidates the provider cache by switching model, so the append costs
  nothing extra — but it must be an APPEND at the switch, not a per-turn re-render.

**Why not the alternatives.** A whole second recipe doubles what routine-improver maintains
and drifts silently. Injecting the distillate always makes every strong-model run read a crib
written for a weaker one — the failure mode that made `deliberation` a knob in the first
place.

**First increment.** The injection seam only, with distillates written by hand for one
routine: teach the composer to append `stages/<current>.distillate.md` when the run's
transcript carries a `failover` event. That is testable with `ScriptedEndpoint` and proves
the trigger before any generation work exists.

---

## Progressive ossification — the run history as the routine's own corpus

**Decision** (operator, 2026-08-27, in conversation). No item id yet: this entry precedes
its finding rather than answering one. F363 is the same operation at a different
compression level — take the corpus of prior runs of ONE stage, emit an artifact that
reduces what the next run must work out. F363 emits prose for a weaker model; this emits
code, or nothing at all, for no model.

**Problem.** A routine re-pays the same deliberation every run for steps whose answer never
changes — which files to read, which parameters to fetch with, which capability to reach
for. That is not one turn: it is the several turns of context-acquisition that precede the
action, all to arrive at a conclusion the last twenty runs already reached. The system
already wants this fixed — `scripts/` exists as the ossification target, and
routine-improver scouts recipes for deterministic prose responsibilities — but nothing
MEASURES whether a step has converged. Today an ossification is the improver's taste, and
a wrong one degrades the work silently, because a mature routine is the one nobody reads.

**The direction, which is the load-bearing decision.** Exploration is the STARTING state
and exploitation is earned; never the reverse. A scheme with reuse as the default and a
sampling rate for fresh deliberation makes exploration permanent rent — the cost curve
flattens instead of falling. Here the unit of progress is the STEP, not the run: every step
begins in prose, evidence accrues from ordinary runs at zero extra cost (that deliberation
was happening anyway), and a step is promoted only when its own history earns it. A routine's
cost is then monotonically decreasing over its lifetime.

**Shape.**

- **One corpus, many consumers.** Per routine, per stage: the cases `(preceding
  observation → action taken, with what it cost)` across all runs. Derived, never written.
  Everything wants it — F363's derivation half, ossification into `scripts/`, per-stage
  model right-sizing, budget and cadence calibration from measured distributions, an
  anomaly alarm, convergence detection. Build the corpus, not N features.
- **A promotion ladder, each rung demanding more evidence:** prose → the run reads what
  previous runs did here and re-issues it itself → a guarded reuse → a script. The first
  rung needs no engine change and no new action kind: the model reads the cases and
  chooses, so it is the guard on every use, and its disagreement is exploration for free.
- **The promotion test is that the corpus is a FUNCTION** — same input class, same action,
  across N cases. Variance in the decision IS the judgment, and an unformalizable step must
  stay in prose. This is what makes "formalizable" checkable rather than a vibe. A run that
  freely re-issues an identical action, having been able to choose otherwise, is the
  strongest evidence available that the step is constant — and it is generated during
  normal operation, so no shadow-execution machinery is needed.
- **Rank candidates by DELIBERATION cost, not execution cost.** There is no reasoning-token
  field (usage is `{in, out, cost}` per event), but reasoning bills as output: a turn with a
  two-line action and a large `out` is a heavy-deliberation turn. `out ÷ (say + action size)`
  ranks candidates with no new instrumentation. Multiply by frequency and by constancy.
- **Demotion, not sampling.** A reused decision still EXECUTES, so its observation returns
  to the run, which reads it fresh: a fetch that returns garbage, a parse that fails, a file
  that is gone. Drift is caught by failure, not by a schedule. `scripts.py` already returns
  `(exit, out, err)` into the observation, so a script that raises on unrecognized input
  hands the step back to prose by itself — the discipline is writing scripts that raise
  rather than guess.
- **A step whose failure would be SILENT is the only residual risk.** The fix there is to
  make the failure loud (an assertion over the output shape, which prior runs hand you for
  free), never to re-deliberate periodically.
- **What this dissolves:** cache-invalidation fingerprints. A changed stage is not a stale
  cache, it is a NEW step with no accumulated evidence, which starts in prose like anything
  else. Do not build a scheme that tracks every input to a decision (stage text, permissions,
  rules, model, util catalog): it is bookkeeping that still cannot see the outside world
  change, which is the common case.

**Why not the alternatives.** A global exploration rate charges rent forever and never lets
a converged step settle. A `replay`/`reuse` action kind FIRST inverts the value: the actions
safe to replay are cheap to redo anyway, and the ones worth replaying carry effects — while
the corpus-as-read version needs no 26th kind at all. A dedicated kind earns its place only
once the corpus shows a stage where the run always agrees AND the arguments are bulky enough
that emitting them is the cost.

**First increment.** The per-stage, cross-run CASE INDEX — nothing that consumes it. Half of
it exists: every `assistant_action` is stamped with the active phase, and `statemap.phase_stats`
already derives per-phase turns / tokens / cost from one transcript. What is missing is the
cross-run aggregation keyed by stage, holding cases rather than totals. Build it on the
`readmodels/util_stats.py` pattern — retained transcripts, both homes, root + sub, memoized
behind an (inode, mtime, size) fingerprint, no database.

**The one trap that shape carries:** transcripts are PRUNED. `util_stats` needed a durable
usage stream precisely because retention would otherwise erase its history, and a corpus that
forgets is worst exactly where it matters — a step converges over the long tail, which is the
part retention deletes first. So the case index needs its own durable record from day one, or
it will confidently report convergence over whatever window happens to survive.

---

---

## A third stopping SCOPE: work that outlives one run but does not retire the routine

**Decided in conversation 2026-09-17; no finding.** The survey that produced it compared this
engine against four spec-driven-development toolkits — [spec-kit](https://github.com/github/spec-kit)
(MIT), [OpenSpec](https://github.com/Fission-AI/OpenSpec) (MIT),
[spekk-cli](https://github.com/spekk-ai/spekk-cli) (Apache 2.0) and
[shipsmooth](https://github.com/bitkentech/shipsmooth) (Apache 2.0). Almost everything in them
already exists here under different names (a constitution is the rules library, phases are
`stages/` + `phase.json`, a change folder is `docs/designs.md` plus the Items ledger, an observer
is self-audit, a planning store is a domain's shared store), which is why none of the four is
being integrated: they deliver their value as slash commands inside an interactive assistant, and
a second agent loop in the path is banned. Two ideas survived the comparison. One shipped in
0.350.0 as the `unmet` residual. This is the other.

**Problem.** `state/stopping.json` has exactly two scopes and there is nothing between them.
`scope: "run"` bounds ONE run and never transitions — correctly, since a per-run bound cannot be
"already met", and making it sticky is the defect the split undid. `scope: "goal"` is the state
after which the ROUTINE is finished, and it is sticky *because* it retires the routine. A unit of
work that legitimately spans several runs — a migration with eleven steps, a backlog of twelve
sites to convert, a document with five sections each needing its own research pass — fits neither.
Today it is carried in recipe prose, in `state/notes.md`, or in the `unmet` residual, none of which
can say "this piece is finished and the next run should not re-derive it", and none of which two
runs can hold without colliding.

**Shape (spekk's assertion model, mapped onto what exists).** A `scope: "work"` condition: sticky
like a goal, so it transitions to `met` and stays there — but it retires the ITEM, never the
routine, so `evaluate()` keeps answering about goals alone and nothing about retirement changes.
Ordering is the existing `requires` connective, which already expresses a dependency chain and
already renders as `waiting on s1`; spekk's one-dependency limit is the same restraint this
document's two-level nesting applies for the same reason. What is genuinely new is the LEASE:
a condition claimed by a run (or by a `spawn`ed child) while it is worked, released on exit, and
ignored once stale — so a parent and its children can share one queue instead of the parent
re-deriving what a child already took. spekk keys its lease `builder-<host>-<pid>-<ts>` and
treats an old lock as absent, which is the right shape here too: the engine already reaps runs
whose process is gone, and a lease that outlived its holder must never block the queue.

**Why it is not built yet, and what would settle it.** The honest case against is that this is
precisely the feature that grows a second planning system inside a scheduler — and three
mechanisms already cover much of the ground (the `unmet` residual carries what a run left, the
digest tail carries notes forward, `.memory/` carries curated findings across runs). The question
it hangs on is empirical and answerable: **do live routines actually lose or re-derive work
between runs today?** A self-audit pass over the run transcripts of the multi-run routines can
answer it — a run re-doing a step a previous run completed, or a child and its parent doing the
same piece, is the evidence. Build it if that evidence exists; delete this entry if it does not.

**First increment, if it goes ahead.** The scope alone, with no lease: add `"work"` to
`stopping.SCOPES`, make `record_accounting` transition it on `met` the way it transitions a goal
(and nothing else — `evaluate` must keep ignoring it), render it in `stopping_digest` as its own
block beside the other two, and let `api_stopping` write it. That is testable on its own and
leaves the lease — the part with real concurrency in it — as a second decision.

---

## One unnamed thread pool serves four latency classes, and the console cannot say "alive but not answering"

**Decision** (operator, 2026-09-12, five proposals accepted mid-run: `q-20260912-121159-20`,
`-26`, `-32`, `-35`, `-40`, plus `q-20260912-115143-34` asking for this very entry). They are ONE
body of work, not five, and the routine that filed them said so. Ten days and roughly twenty
releases later none of it was built, because an acceptance on the Decisions page reached no
builder — which is the defect the decided-work queue now closes, and this entry is its first item.

**Problem.** The daemon's stalls are invisible from both ends at once.

*Server.* FastAPI runs every `def` handler on anyio's default limiter and `asyncio.to_thread`
draws from the same one. No `total_tokens` / `CapacityLimiter` assignment exists repo-wide, so it
is the stock **40 tokens, unnamed**, shared by four latency classes: fast local reads
(`/api/status`, `/api/lanes`, items, questions); daemon background work (`oauth_refresh.tick`,
the scheduler's own to-threads, `library_watch.tick`, the llm tailer's poll loop,
`detached_delivery`'s `copytree`); **unbounded outbound network** (`endpoint_probe` — provider
quota reads and a real LLM completion probe; `machines.scan-host` over SSH); and the pdoc build,
which "cannot be cancelled, only awaited". The project isolates by construction everywhere else
and says so — a watcher "must never take the scheduler down", docs "must not take the daemon
down", SSE is exempt from timing because it is slow by design, runs are subprocesses. It isolates
by FAILURE and not by LATENCY, and a starved pool is a latency failure. No module owns the pool,
which is why nothing documents the 40 and `api_debug._limiter()` has to discover it at runtime.

*Client.* `static/api.js`'s `api()`, `apiUpload()` and `apiBlobUrl()` have no `AbortController`
and no deadline (all three now share `authedFetch`'s 401/403 gate loop, so only the deadline half
of this stands). A stalled request never settles, so `!resp.ok` — the
only failure path — is never reached: no toast, no log, no state change, an eternal skeleton.
`refreshStatus()` polls `/api/status` every 30 s through that same helper, so when the poll
stalls control reaches neither the branch that turns the daemon lamp on nor the `catch` that
turns it off: the lamp freezes in its last state, i.e. GREEN. Three situations, two states —
healthy, dead, and **alive-but-not-answering, which renders as healthy** — and the third is the
one the lamp exists for. It is the second confident falsehood from that indicator; the amber
`restart-pending` state already proves the design accepts more than binary. A caller has worked
around the missing deadline in place: `static/components/searchbox.js` keeps a sequence counter
commented "stale-response guard (no AbortController in api())".

**First increment** — the ordering is the filing routine's own, and it is cheapest-first because
each step makes the next one's evidence better:

1. **Stamp `borrowed_tokens` / `total_tokens` on every slow-request record.** One field pair on a
   record that already exists. It turns the next stall into a self-naming incident instead of one
   inferred from status codes, and it is the evidence every later step wants.
2. **Persist the slow-request ring as a health event**, so that evidence survives a restart.
3. **Give `api()` a deadline and a pending-request counter** — one `AbortController` in the one
   shared helper, a counter beside the existing `openStreams` gauge. No backend change. This is
   the prerequisite for step 4's third item, and it lets `searchbox.js` drop its workaround.
4. **A third daemon-lamp state.** One CSS class plus a `lastGoodPoll` timestamp: degraded when
   the last good poll is older than the poll interval, with its age shown. Items 1-2 of it stand
   alone; the third needs step 3.
5. **Name the pool, then bulkhead it** — a second `CapacityLimiter` for network-bound work and
   deadlines at the five call sites named above. This is the only medium-sized step, and by the
   time it is reached steps 1-2 have measured whether it is the right one.

A convention rides along with all of it, accepted in the same sweep: **a measurement carries its
load conditions** — how many runs were active, in-process or fresh, idle or loaded. Its first
application is the numbers step 1 produces.

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

## The seams the 2026-09-22 review named but did not finish (decided 2026-09-22)

**Decided in the review that shipped 0.365.0; no finding.** That review worked every open item
and every subsystem in file-disjoint lanes, and a lane that needed a change in another lane's
file left a precise request rather than reaching across. Most were landed in the same release.
These are the ones no lane could take, each already specified by the lane that asked for it —
the full text is in that release's working notes and the summary below is enough to start.

**Why one entry and not six.** They share a cause: each is the LAST caller of a seam this
release unified, and a seam with one straggler is a seam that will grow a second convention.
Building them together is what makes the unifications true rather than mostly true.

- **`engine/inbox.py` is 529 lines against the ~350 standard**, and the split is already named:
  messages on one side, questions on the other. The release put one writer and one glob behind
  the `msg-*` shape, which is what makes the seam clean enough to cut.
- **Two hand-rolled `msg-*` writers remain** (`engine/*` and `daemon/detached_delivery.py`)
  after the rest moved to the one writer. Until they move, the single-writer scan the finding
  asks for in `tests/test_policy.py` would arrive red, and a gate that arrives red is deleted
  rather than obeyed.
- **A fourth renderer of the collected-children line** lives in `engine/obs_children.py`, beside
  the three the hand-back unification merged.
- **Two of the five "is something waiting in the inbox" predicates** are in daemon files the
  unification lane did not own. One of them is fail-open by contract and one fail-closed, so
  the shared predicate already carries the flag they need.
- **The domain-notes drain is a side effect inside `composer.state_digest`**, whose only
  production caller is boot. Moving the drain to boot leaves the digest builder pure.
- **The proposal-and-question merge is half done**: one standing proposal per ask now holds for
  every kind, but the badge and the browser push still count no proposals, so two records have
  been invisible on the Decisions page since 2026-09-21.
- **The assist label increment is priced and deferred**: a pre-action rule assist holds a run and
  cannot be labelled, measured at 72 unlabelable turns across three routines. The four coupled
  parts are listed in `docs/rule-assists.md`.

**First increment.** The two remaining `msg-*` writers plus the policy scan, in one change: it is
the smallest of these, it closes a finding rather than half-closing it, and it is what lets the
`engine/inbox.py` split land against a settled contract rather than a moving one.
