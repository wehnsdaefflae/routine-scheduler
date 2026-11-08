"""Two once-per-RUN counters a resumed leg used to forget — rebuilt from the transcript (D245).

`engine/guardscope.py` rebuilds every once-only LEDGER a resumed leg would otherwise start
empty. These two are counters rather than ledgers, and they were missed:

* **`loop.failures`** — how often each call has failed this run. The fix-the-cause rule assist
  fires at the SECOND failure of one call (`assist_predicates._repeated_failure`): the first
  failure is information, the second is where a run either changes route or starts engineering
  around a wall. With the tally reset per leg, a call that failed once before a restart and
  once after it reads as two first failures, so the moment never arrives — in exactly the run
  that needed it, since a leg that died mid-work is the one most likely to resume into the
  same wall.
* **`loop._schema_off`** — the provider's response-format grammar switched off for the rest of
  the run, after repeat-streak shedding rescued the run twice (`loop._repeat_warning`). A
  restart turned it back on, re-imposing the grammar that was suppressing fields for that
  model.

**The transcript is the record** — the rule `guardscope` states, and neither counter needs a
payload extension to honour it:

* a failure is `exitgate.failure_tally_key(ctx, action, obs)`, a pure function of the action
  and its own observation, which the transcript records as a paired `assistant_action` +
  `observation` of the same turn. The pairing is by TURN, and an observation with no action
  before it is skipped rather than guessed at — a leg killed between the two events leaves
  exactly that.
* `_schema_off` already writes an `error` event whose message says the format was disabled for
  the rest of the run. The fact was on the record and simply never read back.

The same gate as the live loop (`donothing.grounds_a_finish`) decides what counts, so a held
action — one the engine never ran — is not tallied here either. That is D243's defect one level
down: two readers of one condition must not disagree, which is why both ask the one predicate
rather than restating it.
"""

from __future__ import annotations

#: The sentence `loop._repeat_warning` records when it turns the grammar off for good. Matched
#: on this phrase rather than on `attempt == 0`, because an ordinary schema error also carries
#: an attempt number — the fact being read back is the DECISION, not the error.
_SCHEMA_OFF_MARK = "disabled for the rest of the"


def failures_from(ctx, events: list[dict]) -> dict[str, int]:
    """`{failure key: how many times it failed}` over the events of one guard scope.

    `ctx` is the run context `exitgate.failure_tally_key` needs to read a callee's declared
    informational exits (F711) — None is accepted, and then a declared code counts, exactly as
    an unreadable source does in the live tally.
    """
    from .donothing import grounds_a_finish
    from .exitgate import failure_tally_key

    out: dict[str, int] = {}
    pending: dict[object, dict] = {}
    for ev in events:
        payload = ev.get("payload")
        if not isinstance(payload, dict):
            continue
        if ev.get("type") == "assistant_action":
            pending[ev.get("turn")] = payload
            continue
        if ev.get("type") != "observation":
            continue
        action = pending.pop(ev.get("turn"), None)
        if action is None or not grounds_a_finish(payload):
            continue
        if key := failure_tally_key(ctx, action, payload):
            out[key] = out.get(key, 0) + 1
    return out


def schema_off_from(events: list[dict]) -> bool:
    """Did this run already switch the provider's response format off for good?"""
    return any(ev.get("type") == "error"
               and isinstance(ev.get("payload"), dict)
               and str((ev["payload"] or {}).get("where")) == "schema"
               and _SCHEMA_OFF_MARK in str((ev["payload"] or {}).get("message") or "")
               for ev in events)


def rebuild(loop, events: list[dict]) -> None:
    """Re-seed both counters from the events inside this leg's scope — the counterpart of the
    `loopsetup` lines that built them empty.
    """
    loop.failures = failures_from(loop.ctx, events)
    if schema_off_from(events):
        loop._schema_off = True
