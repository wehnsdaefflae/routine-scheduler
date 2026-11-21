"""The ENGINE-SERVED CHEAP POLL (F785, the engine half of D217)."""

from types import SimpleNamespace

from rsched.engine import autoswap, sidefields
from rsched.engine.wrongturn import WARN_STREAK


def _loop(models: dict[str, str]):
    """The same narrow stand-in `test_role_swap.py` uses: the models mapping and the message
    list `enginenote` appends to. Deliberately not a RunContext — this layer must stay narrow
    enough to work on exactly those.
    """
    events: list[dict] = []
    ctx = SimpleNamespace(
        routine=SimpleNamespace(models=dict(models)),
        transcript=SimpleNamespace(event=lambda kind, payload: events.append(
            {"type": kind, **payload})))
    return SimpleNamespace(ctx=ctx, messages=[]), events


POLL = {"say": "waiting", "kind": "children"}
GATE = {"say": "status?", "kind": "script", "name": "gate", "args": ["status"]}


def _land(loop, action: dict, times: int = 1) -> None:
    for _ in range(times):
        sidefields.land(loop, dict(action))


def _poll_stretch(loop, times: int = 3) -> None:
    """The servable band: a poll CLASS repeating while the action itself varies."""
    for n in range(1, times + 1):
        sidefields.land(loop, {"say": "still waiting", "kind": "wait", "n": n})


# ---- 1. the no-op must not pretend otherwise


def test_a_routine_with_no_cheap_model_is_not_swapped_and_is_told_nothing():
    """The common case today: 31 of 32 routines configure no `tool_call` model at all. An
    unset role resolves to the server's fallback model, which is not necessarily cheaper —
    so the mechanism must do nothing at all rather than swap there, and must claim no saving.
    """
    loop, events = _loop({"main": "opus"})
    _poll_stretch(loop, 6)
    assert loop.ctx.routine.models["main"] == "opus"
    assert loop.messages == [], "a routine with no cheap model was told about a saving"
    assert events == []


def test_a_tool_call_equal_to_main_is_also_no_distinct_pair():
    loop, _events = _loop({"main": "opus", "tool_call": "opus"})
    _land(loop, POLL, times=6)
    assert loop.ctx.routine.models["main"] == "opus"
    assert loop.messages == []


# ---- 2. detection is conservative


def test_a_single_recheck_does_not_change_models_under_the_run():
    """One repeat is an ordinary re-check. Swapping there would change the model answering a
    run that is not polling at all — the failure mode that makes an automatic mechanism
    worse than none.
    """
    loop, _events = _loop({"main": "opus", "tool_call": "haiku"})
    _land(loop, POLL, times=2)
    assert loop.ctx.routine.models["main"] == "opus"
    assert loop.messages == []


def test_a_poll_of_a_different_class_restarts_the_count():
    """Two different questions are not a repeat of either, even though both are polls."""
    loop, _events = _loop({"main": "opus", "tool_call": "haiku"})
    _land(loop, POLL, times=2)
    _land(loop, GATE, times=2)
    assert loop.ctx.routine.models["main"] == "opus"


def test_work_that_merely_looks_like_a_poll_is_not_one():
    """`script`/`util` are poll-class only when the identical call repeats: two different
    gate calls are work, not a poll.
    """
    loop, _events = _loop({"main": "opus", "tool_call": "haiku"})
    for args in (["status"], ["status", "-v"], ["run"]):
        sidefields.land(loop, {"say": "x", "kind": "script", "name": "gate", "args": args})
    assert loop.ctx.routine.models["main"] == "opus"


# ---- 3. the second consecutive repeat is served cheap, and the run is told


def test_the_second_consecutive_repeat_of_a_poll_class_is_served_by_the_cheap_model():
    loop, _events = _loop({"main": "opus", "tool_call": "haiku"})
    _poll_stretch(loop, autoswap.SERVE_FROM)
    assert loop.ctx.routine.models["main"] == "haiku"


def test_polls_chained_inside_one_reply_count_like_turns():
    """DELIBERATE, pinned so it is not read as an accident. `sidefields.land` is called for each
    CHAINED element of a multi-action reply too (`chain.take_turn` → `loop._land(…,
    chained=True)`), so three same-class polls written in ONE reply reach the serve depth inside
    that turn. The engine's own repeat counter behaves identically (`chain.take_turn` calls
    `loopnudge.repeat_streak` per element), and one reading of "the run is polling" is better
    than two that drift apart.
    """
    loop, _events = _loop({"main": "opus", "tool_call": "haiku"})
    _poll_stretch(loop, autoswap.SERVE_FROM)      # as one chained reply, at this seam
    assert loop.ctx.routine.models["main"] == "haiku"


def test_a_byte_identical_poll_can_never_be_served():
    """THE LIMIT OF THE AUTHORIZED SHAPE, pinned so no reader has to discover it twice."""
    assert autoswap.SERVE_FROM == WARN_STREAK, (
        "SERVE_FROM and WARN_STREAK no longer coincide — re-measure which band is servable "
        "before changing this test")
    for action in (POLL, GATE):
        loop, _events = _loop({"main": "opus", "tool_call": "haiku"})
        _land(loop, action, times=6)
        assert loop.ctx.routine.models["main"] == "opus"
        assert loop.messages == []


def test_the_run_is_told_once_and_told_it_asked_for_nothing():
    """Requirement 4 of the authorization: the run must be able to see that the engine served
    its turn cheap. The carrier is the existing per-turn model stamp (D188); the note names it
    rather than inventing a second surface, and says the run need do nothing.
    """
    loop, events = _loop({"main": "opus", "tool_call": "haiku"})
    _poll_stretch(loop, autoswap.SERVE_FROM + 3)
    notes = [m for m in loop.messages if m["content"].startswith("ENGINE NOTE: ")]
    assert len(notes) == 1, "the cheap stretch re-announced itself and paid for the feature"
    assert "haiku" in notes[0]["content"]
    assert "per-turn model stamp" in notes[0]["content"]
    recorded = [e for e in events if e["type"] == "user_injection"]
    assert recorded and recorded[-1]["source"] == "engine"


# ---- 4. the restore is automatic


def test_an_action_of_a_different_class_restores_the_main_model_unasked():
    loop, _events = _loop({"main": "opus", "tool_call": "haiku"})
    _poll_stretch(loop, autoswap.SERVE_FROM)
    assert loop.ctx.routine.models["main"] == "haiku"
    sidefields.land(loop, {"say": "now I read", "kind": "read_file", "path": "p"})
    assert loop.ctx.routine.models["main"] == "opus", "the run stayed cheap after it stopped polling"


def test_a_second_poll_stretch_is_served_again_without_a_second_note():
    loop, _events = _loop({"main": "opus", "tool_call": "haiku"})
    _poll_stretch(loop, autoswap.SERVE_FROM)
    sidefields.land(loop, {"say": "x", "kind": "read_file", "path": "p"})
    _poll_stretch(loop, autoswap.SERVE_FROM)
    assert loop.ctx.routine.models["main"] == "haiku"
    notes = [m for m in loop.messages if m["content"].startswith("ENGINE NOTE: ")]
    assert len(notes) == 1


# ---- 5. where this layer stands down


def test_the_stuck_escape_turn_is_never_served_cheap():
    """At `wrongturn.WARN_STREAK` byte-identical actions the loop tells the run it is stuck AND
    lifts the provider schema for its next reply (`loop._repeat_warning`). That reply has to
    re-plan an escape without a grammar to lean on — the worst turn in a run to hand to the
    cheap model — so a streak that deep restores the main model instead.
    """
    loop, _events = _loop({"main": "opus", "tool_call": "haiku"})
    _land(loop, POLL, times=WARN_STREAK)
    assert loop.ctx.routine.models["main"] == "opus"


def test_a_run_steering_its_own_model_outranks_the_engine():
    """Two layers write `models['main']`. A run that has asked for a position itself is making
    a live decision about its own turns, so the engine stops guessing for the rest of the run.
    """
    loop, _events = _loop({"main": "opus", "tool_call": "haiku"})
    sidefields.land(loop, {"say": "x", "kind": "read_file", "path": "p", "role_swap": "strong"})
    _poll_stretch(loop, autoswap.SERVE_FROM + 2)
    assert loop.ctx.routine.models["main"] == "opus"


# ---- 6. it never clobbers someone else's choice


def test_an_operator_console_switch_mid_poll_is_not_undone():
    """`switches.apply_model_switch` writes the same slot at the turn boundary and knows
    nothing of this layer. His switch is the live decision; the restore must yield to it.
    """
    loop, _events = _loop({"main": "opus", "tool_call": "haiku"})
    _poll_stretch(loop, autoswap.SERVE_FROM)
    assert loop.ctx.routine.models["main"] == "haiku"
    loop.ctx.routine.models["main"] = "sonnet"      # the operator, at the turn boundary
    sidefields.land(loop, {"say": "x", "kind": "read_file", "path": "p"})
    assert loop.ctx.routine.models["main"] == "sonnet"


def test_the_runs_own_strong_climbs_back_after_an_engine_served_swap():
    """Both layers share `ctx.configured_main`, so a run that notices it is cheap and asks for
    `strong` reaches the model its routine configured, not the cheap one it was served on.
    """
    loop, _events = _loop({"main": "opus", "tool_call": "haiku"})
    _poll_stretch(loop, autoswap.SERVE_FROM)
    assert loop.ctx.routine.models["main"] == "haiku"
    sidefields.land(loop, {"say": "x", "kind": "wait", "n": 9, "role_swap": "strong"})
    assert loop.ctx.routine.models["main"] == "opus"
