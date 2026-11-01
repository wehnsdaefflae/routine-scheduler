"""The ENGINE-SERVED CHEAP POLL (F785, the engine half of D217).

F785 measured 2,651 fleet turns that "do nothing but re-ask whether something finished (a
release gate's status, a child's subruns table)", each re-sending the whole prompt to the main
model to read a one-line answer, and noted that `role_swap` (D185) already existed and that
nothing used it. The operator's answer to D217 was "Both — the automatic cheap poll AND a real
tool_call model to swap to"; this is the first half, so the ENGINE serves the repeat poll on the
routine's `tool_call` model without the run asking, and restores the main model when the run
stops polling.

What these tests pin, in the order the engine meets them:
1. the no-op is a real no-op — a routine with no distinct cheap model must not be swapped
   anywhere and must not be told anything (31 of 32 routines today);
2. detection is CONSERVATIVE — one re-check never changes models under a run;
3. the second consecutive repeat is served cheap, and the run is told once;
4. the restore is automatic and happens on the first action of a different class;
5. the two ways this layer must stand down: the engine's own stuck-escape turn, and a run
   steering its own model with `role_swap`;
6. it never clobbers a `main` someone else re-pointed (the operator's console switch).
"""

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


POLL = {"say": "waiting", "kind": "subruns"}
GATE = {"say": "status?", "kind": "script", "name": "gate", "args": ["status"]}


def _land(loop, action: dict, times: int = 1) -> None:
    for _ in range(times):
        sidefields.land(loop, dict(action))


def _poll_stretch(loop, times: int = 3) -> None:
    """The servable band: a poll CLASS repeating while the action itself varies.

    This is the only shape that can be served cheap, and the reason is a collision with the
    loop's own stuck counter rather than a choice made here — see
    `test_a_byte_identical_poll_can_never_be_served` below. `wait` on successive children is
    the honest example: each turn asks "has it finished?" of the same class, and no two turns
    are the identical action.
    """
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
    """THE LIMIT OF THE AUTHORIZED SHAPE, pinned so no reader has to discover it twice.

    For a poll whose action never varies — `subruns` takes no fields at all, and an
    identity-class `script`/`util` poll IS its canonical string — the class depth and the
    byte-identical streak are the SAME number. `SERVE_FROM` (the second consecutive repeat,
    which is what D217 authorized as conservative) therefore always coincides with
    `wrongturn.WARN_STREAK`, where the loop warns the run it is stuck and sheds the provider
    schema for its next reply. The escape turn must stay on the main model, so the servable
    band for a byte-identical poll is EMPTY: the mechanism correctly does nothing.

    That is a real shortfall, not a tuning preference, and closing it needs a WINDOWED rule
    (a poll class seen twice within N turns) — an operator decision this layer does not take.
    """
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
