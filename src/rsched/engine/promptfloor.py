"""The PRECONDITION nothing checked: is this run's prompt floor larger than its model's window?

The second half of F539, decided by the operator as D223 (2026-10-30). The first half shipped in
0.474.0 — a run served by a smaller model is TOLD its window, durably (`engine/substitution.py`).
What stayed open is the question that module's own docstring set aside as not its to answer:
whether a run whose incompressible floor exceeds the resolved window should be stopped BEFORE it
starts. It is a behaviour change — a run that previously produced something would produce
nothing — so it went to him, and his answer was option (a):

    *"A HEALTH EVENT only: record the precondition failure and start the run anyway. Nothing
    changes for any run, and you get a surface that says how often it happens."*

So this module RECORDS and never refuses. That is not timidity, it is what the available number
can support: the window read here is the CATALOG window, the same figure the CAPABILITIES line
states, and an F278 run-local correction can already make the real clamp smaller than the
catalog says (a disclosed approximation, carried in F539's row). A refusal resting on a figure
known to be approximate would turn a degraded run into no run on the strength of an estimate.
Once the stream says how often the floor really exceeds the window, refusing becomes a decision
that can be made on evidence rather than on arithmetic.

WHY THE FLOOR IS KNOWABLE HERE. `engine/window.py` and `engine/compaction.py` both state the
same property: archiving elides the MIDDLE, and the retained head+tail is an incompressible
floor. `engine/overflow.py` raises exactly when shrinking cannot get under the window — which is
where the specimen died, at turn 106, $1.03 spent, on a run that could never have completed a
turn (`llmsectest-weekday:20260922-060001`). At boot the prompt has just been composed, so its
estimate IS the floor plus whatever the first turn adds: measured before any model call, with no
network and no cost.

BEST-EFFORT BY CONSTRUCTION. It runs inside `boot`, before turn one, so every failure path here
returns None: an unresolvable model is not measured, a malformed loop is not measured, and
nothing raises. A diagnostic that can stop a boot is worse than no diagnostic.
"""

from __future__ import annotations

from typing import Any

#: The event name. Registered in `health_events`' header enum in the same change — an event at a
#: call site and absent from that enum is invisible to every documented filter, which is the
#: `prompt_oversize_shrunk` failure `tests/test_health_events.py` exists to catch.
EVENT = "prompt_floor_over_window"


def check(loop: Any) -> None:
    """File the precondition event if this run's prompt floor exceeds its model's window.

    Returns None always — the caller gets no verdict, because there is no decision to make
    here (D223 option (a)). Never raises.
    """
    try:
        ctx = loop.ctx
        messages = loop.messages
        if ctx is None or not messages:
            return
        from .compaction import estimate_input_tokens

        floor = int(estimate_input_tokens(messages))
        _endpoint, ref = ctx.registry.for_model("main", ctx.routine.models, ctx.routine.efforts)
        window = int(getattr(ref, "context_tokens", 0) or 0)
        if window <= 0 or floor <= window:
            return
        from ..health_events import log_health_event

        model = getattr(ref, "name", None) or getattr(ref, "model", "?")
        log_health_event(
            ctx.server.routines_home, EVENT, routine=ctx.routine.slug, run_id=ctx.run_id,
            detail=(f"the composed prompt estimates {floor:,} tokens against {model}'s "
                    f"{window:,}-token window, and the head+tail floor cannot shrink below "
                    f"it — this run can complete no turn unless the prompt gets smaller. "
                    f"The run was started anyway (D223)."),
            # FIELDS, not only prose: "how often, and by how much" is the question this surface
            # exists to answer, so a sweep can filter and compare without parsing a sentence.
            floor_tokens=floor, window_tokens=window, model=model)
    except Exception:
        # A precondition DIAGNOSTIC may never be the reason a run fails to start: an
        # unresolvable model, a stand-in loop, a malformed message list are all simply not
        # measured. The run proceeds exactly as it did before this module existed.
        return
