# Detached background tasks — long fire-and-forget in conversations

A within-reply `subtask`/`spawn` is a **thread** in the reply's process: it keeps a conversation
live *while a child runs within a reply*, but it dies when the reply's process exits (a conversation
is finish-per-reply / process-per-reply). That can't cover the one case a chat most wants: **kick off
a long job (a 20-minute scrape), keep having a normal back-and-forth about other things, and get told
when it lands.** For that the job must survive across *multiple visible replies* — so it runs as its
**own daemon-managed process**, detached from any single reply.

That is the `detach` action. Naming: the *concept* is a "detached background task"; the *word*
"background" already means the within-reply non-blocking subtask, so the action is the verb `detach`.

## The `detach` action

```json
{"say": "Kicking off the scrape in the background.",
 "kind": "detach",
 "prompt": "<self-contained brief — the task can't ask you blocking questions>",
 "workflow": "general-task",   // optional: a library pattern for the job (default general-task)
 "label": "scrape"}            // optional: the name shown in the conversation rail
```

`detach` is only valid from a **root conversation** (depth 0, under `conversations_home`): a scheduled
routine has no waiting user to relay a result to, and a within-reply child (or a detached task itself)
must not spawn further detaches — so the engine rejects it elsewhere. No permission grants it:
it is STRUCTURAL, added to a root conversation's policy at setup (`engine/loopsetup`) and to
nothing else, so a routine's schema never shows it.

The handler does almost nothing in-process: it writes an intent file to `background_home/.requests/`
and returns. The assistant then `finish`es the reply ("started it — I'll report back") and the
conversation continues normally.

## Lifecycle (the daemon owns it)

A **detached task** is a routine-shaped dir with a unique slug under a NEW `background_home` (a config
peer to `routines_home` / `conversations_home`), whose `routine.yaml` records `owner: {slug, dir}` (the
spawning conversation). The daemon's **`DetachedManager`** (`daemon/detached.py`, the SINGLE writer of
`background_home`, ticked from the scheduler after the cron-fire loop) runs the whole lifecycle, all on
disk so it is restart-safe:

1. **intake** — drains `.requests/*.json`, materializes the task dir (`childrun.materialize_to_disk`),
   writes its `routine.yaml` (owner + permissions/capabilities/models/fs-roots copied from the owner, a
   background-sized budget — NOT the owner's 10-turn reply window — and `write_util`/`memory_*`/`detach`
   stripped), then `runner.fire`s it on a dedicated `BACKGROUND_SLOTS` pool. Idempotency is keyed on
   **run existence**, so a crash between materialize and fire is recoverable.
2. **deliver** (guarded by a `delivered.json` marker + a deterministic message filename, so delivery is
   exactly-once across restarts) — on the task's terminal status it copies its `artifacts/` into
   `<owner>/artifacts/from-bg-<taskid>/` (namespaced, never clobbering the conversation's own artifacts)
   and writes a durable `[background task finished] …` message into the owner's `inbox/`.
3. **wake** — if the owner conversation is idle (its last run is terminal), `runner.resume`s it so the
   result reaches an away user; if a reply is live, the message rides its next turn boundary. This wake
   is state-driven (terminal owner + a pending message), so it also catches the race where the owner
   finishes a reply just after the message was written. It counts only the messages a RESUMED leg
   drains (`inbox.LIVE_MESSAGE_VIAS`: the user's own, a background result, a branch hand-back,
   a one-shot's fire text) —
   anything else waiting is a fresh run's freight, and waking for it consumed nothing, so every tick
   woke the owner again. Nothing pings anywhere else: the durable message in the conversation IS the
   delivery, and the console surfaces it.
4. **digest** — rebuilds `<owner>/state/background.json`, which the composer inlines into each reply's
   state digest ("Background tasks you launched: …") so the assistant can answer "how's the scrape
   going?" and knows to relay a newly-finished result.
5. **gc** — removes a delivered task dir a grace window after the owner has drained its message.

A detached task **holds the restart drain gate like any other run**: the daemon restarts once nothing
has been active for ten seconds, and a running task counts as active. It has to. The child spawns with
its own session, which was once read as surviving the daemon's SIGTERM — it does not, on either
deployment. Under Docker the daemon's exit ends tini (PID 1) and the kernel SIGKILLs the whole PID
namespace; under the systemd unit the default `KillMode=control-group` kills everything left in the
cgroup. A new session is neither. An excluded task was therefore killed at rc=-9 by the very drain that
exists to protect a run, closed `aborted` at the next boot and delivered to its owner as
"[background task was cancelled]".

The wait that costs a deploy is bounded twice: a task's own budget caps it at 60 minutes, and detached
tasks use **deferred asks only** (coerced in `handle_ask`), so one can never park in `waiting_user` and
hold a restart with no deadline. What survives a restart regardless is the WORK ALREADY DONE — delivery
is idempotent and driven from disk, so a task that finished just before the exit is delivered after it.

## What the global pause holds

The operator's global pause (D34, `daemon/pause.py`) promises that **the scheduler starts nothing
on its own**. This lifecycle starts runs at exactly two points, and the pause is enforced
per-phase inside `DetachedManager.tick` — not by its caller:

| phase | paused | why |
| --- | --- | --- |
| **intake** (`runner.fire`) | **HELD** | it starts a run. The intent file stays in `.requests/` unconsumed, so the task fires on the first tick after the resume — the promise one-shot intake already makes ("never consumed unfired"). |
| **deliver** (artifacts + inbox message) | **proceeds** | it starts nothing: a copy and a durable message in the owner's `inbox/`, which the owner reads whenever it next runs. |
| **wake** (`runner.resume_terminal`) | **HELD** | a resumed leg IS a run. `wake` is state-driven (a `delivered.json` marker plus a pending message), so the first tick after the resume wakes the owner — nothing is lost by waiting. |
| **digest** (`state/background.json`) | **proceeds** | a pure derivation of what is on disk. |
| **gc** | **proceeds** | it only removes task dirs the owner has already drained. |

**Why delivery is not held, and the wake is.** The two look like one step in prose — "hand the
result back" — and they differ on the only axis the pause cares about. A delivery's work is
already paid for: the task ran, it finished, and copying its artifacts next to a message changes
nothing about what is RUNNING. Holding it would also lose something real, because `gc` removes a
task dir a grace window after delivery: a pause lifted days later would hand back a message
whose artifacts had meanwhile been collected. The wake is the opposite — `resume_terminal` starts
a run leg, and such a leg is **not** caught by the engine's own pause gate
(`engine/control.pause_gate`), which holds only runs MARKED at the pause click
(`web/settings/pause.py` stamps `scheduling_pause` into each then-active run's `control.json`).
A freshly resumed leg carries no mark and would run to completion under a pause the operator
believes stopped everything. So the pause holds it, and the delivery waits in the inbox — which
is exactly where an undelivered result waits anyway while its owner is busy.

**Why the gate is in the manager and not in the scheduler's `if not is_paused:` block.** `_boot`
calls `detached.reconcile()` (`daemon/scheduler.py`) BEFORE the pause-checking `boot_catchup`, so
a guard at the tick's call site would still have let a restart under a pause fire every
undelivered task — which is what F721 found. `reconcile()` is a `tick()`, so one gate covers both.

**Neither hold files a health event**, deliberately. `health_stream.BLOCKED_EVENTS` means WORK
THAT WAS DUE DID NOT HAPPEN; these are deferrals with their own durable record on disk (the
request file, the delivered marker + the pending message), exactly like trigger and one-shot
intake, which file nothing either. They log at DEBUG: the tick runs every 5 s, so an INFO line
per held tick would be hours of noise for one standing fact the dashboard's `paused` banner
already shows. A dropped fire is different and does file one — `fire_paused` / `lane_fire_paused`
(`daemon/pause_skip.py`), because a dropped fire leaves nothing else behind.

Pinned by `tests/test_global_pause.py` (intake, boot `reconcile`, and the delivery-vs-wake split).

## Monitor + cancel

- The conversation rail (`static/views/conversations.js`) shows a **background** card: each task's label,
  state, and a cancel button while it runs. It refreshes when the conversation wakes (a
  completion) and — while there is a task to watch or a run that could start one — on a 15 s poll
  that DISARMS itself when neither holds: `#/conversations` is the default route, so an idle tab
  otherwise polled that endpoint forever about an empty list.
- API (`web/api_background.py`): `GET /conversations/{slug}/background` lists them,
  `POST …/background` drops an intent (the human/test analog of the engine action),
  `POST …/background/{id}/cancel` aborts one (`runner.abort` + a pid fallback for a task that outlived a
  restart). Deleting a conversation tears down its detached jobs.
- A detached run's transcript / sub-run tree resolve on the generic `/api/runs/{run_id}` endpoints
  (`_run_dir` searches `background_home` too).

## A single action in the background — `background: true`

The other half of backgrounding, and the opposite end of the scale from `detach`: not a whole job
deserving its own run, but ONE slow step the run should not sit through. A read or a fetch marked
`background: true` starts in a thread, returns a *started* observation naming a handle on the same
turn, and its REAL observation is appended at a later turn boundary, tagged with that handle. The
context is unchanged — same run, same budget, same conversation — which is precisely what `detach`
cannot offer.

- **Which kinds.** `engine/actions.BACKGROUNDABLE_KINDS`: `util`, `script`, `shell`, `llm`,
  `decide`, `read_file`, `view_image`, `memory_read`, `read_rule`. Every one is a read or a fetch
  whose only effect is its observation. A mutation (`write_file`, `edit_file`, `write_util`, `memory_write`, …)
  is REFUSED with its reason inside the schema-retry cycle, because a later synchronous action
  would read what the deferred one has not written yet; widening that needs a dependency/barrier
  model. A control kind (`finish`, `ask_user`, `report`, `spawn`, `subtask`, `wait`, `kill`) is
  refused too — it either steers the run or is already asynchronous in its own right.
- **The mechanics** live in `engine/background.py`, shaped on `engine/archival.py` rather than on
  the detached machinery above: `configure` / `start` / `collect` / `settle`. `actionroute.dispatch_action`
  intercepts the flagged action; `loop._turn_boundary` calls `collect`, which appends the real
  observation where every other boundary feed appends; `loopend.finish_run` calls `settle`.
- **The thread runs the ORDINARY dispatch path for the kind**, read off the same routing table the
  foreground consults (`background._runner`), so a backgrounded call is the same call it would have
  been synchronously. Two kinds are NOT `executor.dispatch`: `script` is dispatched by
  `executor.do_script` (it has no `DISPATCH` entry, on purpose — see the next bullet), and the
  asymmetry is asserted by `tests/test_background_actions.py::test_the_background_runner_matches_the_foreground_routing_table`.
- **Every CALL-TIME GATE runs on the starting turn, synchronously — the flag defers the work, never
  the decision** (F633). `util` and `script` are the two backgroundable kinds `actionroute._route`
  owns rather than `executor.DISPATCH`, because D39 decided secret exposure at call time and the
  user answers it while the run waits; a thread has no turn to block on, so the gate cannot live
  there. `actionroute._gate_for_background` runs it before anything starts, and a refusal or a
  pending request becomes that turn's observation with nothing backgrounded. Before this, a
  backgrounded `script` raised `KeyError: 'script'` every time and a backgrounded `util` ran with
  its exposure gate never asked — an ergonomics flag had relocated a security decision.
- **Nothing vanishes.** A background call that RAISES delivers an error observation like any other.
  One that lands with no turn left to read it is recorded with `unread: true`. One still running at
  run end is recorded `abandoned: true`, its in-flight model calls closed through
  `instrument.abandon_open_calls`.
- **And no spend vanishes either — including a CANCELLED call's** (F722).
  `engine/background_booking.py` is the one place that books what a background call parked, and
  it also keeps the ledger a cancel needs: a model call parks its reading only once the provider
  ANSWERS, so a call cancelled while still running had parked nothing at `cancel` time, and the
  reading that arrived afterwards sat in a parking slot whose `Pending` had already left the live
  list — which `collect` and `settle` both iterate. Nobody popped it: silent under-accounting,
  repeatable every turn, against this very promise. Every boundary now sweeps that ledger and
  records each late booking as an `observation` with `late_booking: true` beside `cancelled`,
  so the spend and a note of what it was for arrive together. A handle leaves the ledger as soon
  as it pays (`take_deferred_usage` pops), so a run with five later boundaries still books once;
  one still owing at run end is given up on, because a thread killed with the interpreter parked
  nothing. Booking at request time was the alternative and was rejected: it would charge an
  estimate the provider never confirmed.
- **And nothing the run never read is silent in `result.md`** (F715, authorized by D195). Both
  losses reach the summary, in two DELIBERATELY different sentences rendered by
  `engine/background_summary.settle_note` and appended by `loopend.finish_run`: a still-running
  call's result *is lost — nothing in this run read them, and nothing recorded them*, while one
  that *LANDED after this run's last turn and was never read* has a result that is **not** lost —
  it is in the transcript as an `unread` observation, and only the run's own reading of it is
  missing. Each line names the count and the handles. A run that read everything gets no line at
  all, which is what keeps the bracket worth reading. Until F715 only the abandoned case spoke, on
  the argument that a late landing is the ordinary end of a short run — so a run that backgrounded
  a call, finished before reading it and drew its conclusions anyway said nothing about that on
  the one surface the operator and the next run read. D195 is why the fix is mechanical: an
  honesty instruction lifts flaw-flagging where the model CAN see the flaw, and the paper's single
  non-improving scenario is the hidden pending tool call — a run cannot flag what it was never
  shown, so the engine says it. Pinned in `tests/test_background_summary.py` (both wordings, and
  that neither appears when there is nothing to report).
- **No new transcript event types**: the started record and the real observation are both
  `observation` events carrying `background` and `handle` (plus `started_turn` on the delivery).
- **The console renders those payloads in ONE place** — `static/components/obs-background.js`,
  which owns every ending a backgrounded call can have (`started`, `cancelled`, `unread`,
  `abandoned`) and the `kill` confirmation that ends one. Its own module because a background
  call has four endings where every other kind has one, and because `components/transcript.js`
  sits at its ratchet ceiling. Each ending has its own row state, and the states are the ones the
  payload distinguishes rather than a tidier shorter list: `running` (dashed — no verdict exists
  yet), `lost` (a result nobody read) and `cancelled` (solid — the one ending whose reason is
  known; `static/obs.css`). F723 is what that costs when it is missing: a cancelled call matched
  no branch, fell through to the ordinary util branch, and rendered `undefined → exit undefined`
  with no state at all, while `kill handle=bg1` read **"sub-workflow #undefined terminated"** —
  the renderer took a sub-workflow NUMBER from an action carrying a HANDLE, so the one
  confirmation a cancelling run gets named nothing, in the vocabulary of the other namespace.
  Both are pinned by `tests/ui/test_cancelled_background_row.py`, which asserts the rendered
  TEXT: the class was never what was wrong. The terminal renderer had the same bug in its action
  brief (`cli_render.py`, `tests/test_cli.py`).

### Phase 4 — the cap, the budget, and who may use it

Three decisions, answered 2026-10-08 (D166, D167, D168), settle how the feature behaves under
load, where its spend lands, and who gets it.

- **No cap on how many are in flight** (D166, answered **(c) unbounded**: "simplest, and the run's
  own turn budget is already the practical limit"). Nothing in `actionroute.dispatch_action` counts
  the live calls; a flagged action meets only the vision steer and the call-time secret gate, both
  of which are about that call and not about how many others are running. The argument for a cap is
  real and was heard — every pending call is a live thread whose spend books against the same run's
  budgets (D167) — and the decision went the other way: a run that fans out more than it has turns
  left to read pays for that in its own turn budget. `background.py` carries that reasoning as a
  comment so the cap is not reintroduced as an obvious improvement; reopening it means reopening
  D166.

  **0.392.0 shipped the opposite and 0.408.1 removed it — the specimen is worth keeping** (F650).
  That release built option (a), `MAX_CONCURRENT = 3` with a `refuse_at_capacity` refusal naming the
  live handles, because the decision row had recorded (a) from an EARLIER delivery of the same
  question; the operator had since answered it differently, and the code, its test, the CHANGELOG
  entry and the prompt text every run reads were all built to the option he did not choose. What
  caught it was comparing each re-delivered answer against its recorded resolution instead of
  checking only that an answer had arrived — four of the five answers in that batch matched the
  ledger exactly, so a blanket "stale re-delivery" reading would have buried the one contradiction.
- **A background call's tokens book ON COLLECTION, not from its thread** (D167). A handler knows
  nothing about having been backgrounded — `do_llm` books through `ctx.add_usage` at five sites
  (two of its own, three in `engine/refusal.py`) and `do_decide` at one — so the deferral lives at
  the single booking point: `RunContext.add_usage` reads the name of the thread it is running on
  (`background_parking_key`, matching `BACKGROUND_THREAD_PREFIX`) and PARKS the reading instead of
  folding it. `background.collect` books it at the turn boundary and records `usage_booked` beside
  the call, so the spend and the observation that caused it arrive together. Booking from the
  thread was wrong twice over: `fold_usage` is a read-modify-write over a shared dict, so two
  folds at once lose a reading; and the spend arrived at an arbitrary moment inside a turn, making
  the number a turn's budget check reads depend on thread timing. **An abandoned call's spend books
  too** — the tokens were spent at the provider whether or not the run ever read the answer, and
  charging only the calls that landed would make "background it and finish" the cheapest way to
  spend money no budget sees.
- **Wall-clock needs no accounting machinery** and the reason is worth stating: the run's clock is
  real elapsed time (`meter()["wall_clock"]` = `elapsed_s()/60`), so twenty parallel fetches cannot
  outrun a 240-minute ceiling — the run is still running while they do. A test pins that semantics
  rather than leaving it to be re-derived.
- **Every run, not only conversations** (D168). Backgrounding was motivated by a human waiting, but
  a scheduled routine with independent fetches gains exactly the same, so the flag is gated by KIND
  only (`BACKGROUNDABLE_KINDS`) and never by run kind. There is nothing to configure.
- **Cancellation: `kill handle=bg1`** (D176 option (a) — `n` stays the sub-workflow namespace, a
  new optional `handle` addresses a background call; the flat schema gained one field rather than
  `n` becoming an `integer | string` union, because it is flat on purpose for weak models and
  Ollama grammars). `background.cancel` drops the Pending from the live list AT ONCE, which is what
  makes the promise true — `collect` reads that list at every boundary — and frees the cap slot
  immediately.
  **What it stops depends on the KIND, and for the slow kinds it really is a kill.** 0.404.0 shipped
  this as "it stops the DELIVERY, never the work", justified by "a Python thread cannot be
  interrupted". The operator read that field and said it described a detach rather than a kill, and
  he was right: the claim holds for the kinds that run inside the interpreter and is FALSE for the
  three that are actually slow. `util`, `script` and `shell` all reach the host through
  `utils_run.run_jailed`, which has polled a `cancelled` callable inside the wait it performs anyway
  and ended the command's whole process group through `procgroup.terminate` since F586 (decided
  D160-C; 0.370.2 added the SIGKILL backstop for a group that ignores SIGTERM) — and a backgrounded
  call goes through the ordinary dispatch, so it already received that callable. What stranded the
  capability was never Python but the intent channel's KEY: `control.cancelled_for_turn` keys the
  channel by TURN, deliberately, so a cancel clicked as one call ends cannot kill the next one — and
  a background call outlives its starting turn, so the check could never fire again.
  - `background.STOPPABLE_KINDS` = `util`, `script`, `shell` — the kinds whose thread is blocked on
    a subprocess. `Pending.stop` is a `threading.Event` set by `cancel` BEFORE it records, so the
    process group starts dying while the record is written, and `Pending.stoppable` says which
    promise a handle carries.
  - The thread dispatches against `_cancellable_ctx`, a shallow `__getattr__` PROXY of the run
    context whose only difference is a `background_stop` handle that `cancelled_for_turn` prefers
    when present. A proxy and not a mutated context, because the real one is shared with the live
    turn and flipping its cancel check would stop the FOREGROUND call too; a delegate and not a
    copy, because the transcript, meter and usage lock must stay the SAME objects for the
    parked-spend booking (D167).
  - **`stopped_work` rides both observations** — the cancel's own and the collector's — so a real
    stop and a dropped delivery are never conflated by whoever reads them back.

  For `llm`, `decide` and the reads the original ceiling stands unchanged: the work runs to its own
  end unread. Both promises are stated in the `handle` field's own schema description, because that
  field is the only documentation a run meets at the moment it decides to cancel. Either way the
  call is recorded with `cancelled: true` (and `had_landed`, when a finished result was thrown away)
  rather than `abandoned`: a deliberate drop and a run that ran out of turns are different losses,
  and the spend books in both. An unknown handle is a correctable error naming what IS in flight.

## Contrast with subtasks/subruns

| | lives | blocks the reply | reports via | survives reply-finish |
|---|---|---|---|---|
| `background: true` (one action) | a thread in the reply | no (the turn returns at once) | the turn-boundary collector | no |
| `subtask` (sequential) | a thread in the reply | no (you `wait`) | the finished-hook / `wait` | no |
| `spawn` (parallel) | a thread in the reply | no | the finished-hook | no |
| `detach` (background) | its OWN daemon process | no (you `finish`) | an inbox message + wake | **yes** |

Reach for `detach` only when a job is genuinely long and independent; anything you can finish within the
reply should stay a direct step or a `subtask`. Reach for `background: true` when the step belongs in
THIS context and only its waiting is the problem.
