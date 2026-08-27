"""The three NUDGES the loop gives a run about its own behaviour.

Split out of `loop.py` (F393): the turn state machine is one job; noticing that a run is drifting
and saying so is another, and all three of these are prompt surface rather than control flow.

`_build_util_reminder` re-states the util catalog when a run starts inventing tool names.
`_repeat_streak` counts identical consecutive actions, which is how a loop-in-a-loop shows up.
`_reserve_finish` is the important one: the FIRST budget violation spends a one-time reserved
turn with the schema narrowed to `finish`, so the run authors its own summary. The engine never
ends a run the model could have ended itself — a run overruns a budget by at most one turn, and
the summary is always the model's.
"""

from __future__ import annotations

import hashlib
import json

REPEAT_FAIL = 5



def build_util_reminder(loop) -> str:
    # One-shot nudge appended to the FIRST user message only (kickoff / resume note) —
    # the catalog already sits in CAPABILITIES and a failed util call carries its own
    # repair hint, so repeating this on every observation was rent without information
    # (~60 tokens × every turn, re-read for the rest of the run).
    if loop.allowed_tools is not None and "util" not in loop.allowed_tools:
        return ""
    if loop.grants.allows_kind("write_util"):
        create = ("write_util to create/revise one"
                  + (" (needs the user's approval first)"
                     if loop.grants.needs_confirm(creating=True) else ""))
    else:
        create = ("note the gap with a deferred ask_user — the write_util capability "
                  "is switched off for this routine")
    return ("\n[tools: the CAPABILITIES catalog lists the global utils; run `util "
            f"name=list args=[\"<name>\"]` for one util's exact usage; "
            f"if none fits, {create}.]")


def reserve_finish(loop, violation: str) -> None:
    """Spend the reserved finish turn: one more turn, schema narrowed to `finish`, so the
    run ends in ITS OWN words instead of an engine string. Before this, a budget violation
    returned an engine-authored `partial` and the model was never told — for a scheduled
    routine that costs the next run its handover; in a CONVERSATION the reply IS the
    product, so the user read "Run stopped by the engine: turn budget exhausted (10)".
    The reserve is spent at most once per run (the caller force-finishes on a second
    violation), so it can overrun a budget by exactly one turn.
    """
    from .kindsurface import schema_for_kinds

    loop._finish_reserved = True
    # ALWAYS_KINDS keeps `report` reachable here; using the reserve on one costs the run
    # its authored summary (the next violation force-finishes), which is the model's call.
    loop.action_schema = schema_for_kinds({"finish"})
    loop._schema_off = False   # the narrowed grammar is the point — re-arm it if shed
    loop.messages.append({"role": "user", "content":
        f"OBSERVATION (budget spent): {violation}. This is your LAST turn — the engine "
        "executes nothing else. Reply with `finish`, status `partial` if work is "
        "unfinished, and put everything that matters into the summary: what you "
        "established, what changed on disk, and precisely where to pick up. That summary "
        "is all that survives."})


def repeat_streak(loop, action: dict) -> int:
    key = {k: v for k, v in action.items() if k != "say"}
    digest = hashlib.sha1(json.dumps(key, sort_keys=True).encode("utf-8"),
                          usedforsecurity=False).hexdigest()
    loop.repeat_hashes.append(digest)
    streak = 0
    for h in reversed(loop.repeat_hashes):
        if h != digest:
            break
        streak += 1
    return streak
