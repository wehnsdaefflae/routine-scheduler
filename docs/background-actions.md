# Background actions — every action runnable in the background (DESIGN, D118)

> Status: **DESIGN, not built.** Settled decision **D118** (operator, 2026-09-04): *"okay if it's
> too big for now then you plan the full feature and its implementation."* This document is that
> plan. Nothing here has shipped; build it in the phases below, each test-gated.

## The problem

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

## Current mechanics this builds on (all real today)

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

## The proposed model

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

## The hard part: which actions may be backgrounded

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

## Open decisions to settle before/while building (surface as their own D-items)

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

## Implementation plan (phased, each test-gated)

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

## Why not just use `detach`?

`detach` is the right tool for a *big, self-contained* job that deserves its own run, budget and
context (a bulk scrape, a slow build). D118 is the opposite end: keep the SAME context and just not
block on one slow step. Both share the delivery-into-a-live-conversation plumbing; D118 adds a
lightweight, same-context unit of work on top of it. Keep both — they answer different needs.
