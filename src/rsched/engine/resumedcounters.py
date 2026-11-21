"""Two once-per-RUN counters a resumed leg must not forget — rebuilt from the transcript (D245).

The counter twins of `engine/guardscope.py`'s ledgers:

* **`loop.failures`** — how often each call has failed this run. The fix-the-cause rule assist
  fires at the SECOND failure of one call, so a tally reset per leg would never reach it across
  a restart.
* **`loop._schema_off`** — the response-format grammar switched off for the rest of the run
  (`loop._repeat_warning`); a restart must not re-impose it.

**The transcript is the record**, with no payload extension:

* a failure is `exitgate.failure_tally_key(ctx, action, obs)` over a paired `assistant_action` +
  `observation` of the same TURN; an observation with no action before it is skipped.
* `_schema_off` is read back from the `error` event that recorded the decision.

The live loop's gate (`donothing.grounds_a_finish`) decides what counts, so a held action is not
tallied here either: two readers of one condition ask the one predicate.
"""

from __future__ import annotations

#: The sentence `loop._repeat_warning` records when it turns the grammar off for good — the
#: DECISION, which `attempt` alone cannot tell from an ordinary schema error.
_SCHEMA_OFF_MARK = "disabled for the rest of the"


def failures_from(ctx, events: list[dict]) -> dict[str, int]:
    """`{failure key: how many times it failed}` over the events of one guard scope.

    `ctx` lets `exitgate.failure_tally_key` read a callee's declared informational exits; with
    None a declared code counts, as an unreadable source does in the live tally.
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
