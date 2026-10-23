"""The RUN-CHOSEN ROLE SWAP (D185): a run drops itself to the cheap model and climbs back.

The operator chose the run-chosen trigger on 2026-10-16 out of four — not a stage-based rule and
not a turn-count rule, because either is wrong for a run whose hard thinking comes late. So the
run itself asks, and it asks on a SIDE FIELD (`role_swap`) riding any action: a dedicated action
kind would spend one full turn of the expensive model asking to become cheap, which is the very
cost the feature exists to save.

What these tests pin, in the order the engine meets them:
1. the field reaches the engine at all — declared in ACTION_SCHEMA, kept by `normalize_action`,
   kept by the capability projection (an undeclared sub-field is stripped SILENTLY);
2. the semantic check refuses a non-position inside the schema-retry cycle, so a typo costs no
   turn;
3. the applier re-points the `main` role, which is what makes the next completion cheap, and
   climbs back to the model the routine configured;
4. the run is TOLD, in the one voice the engine speaks with, so the swap is on the transcript.
"""

from types import SimpleNamespace

from rsched.engine import actions, kindsurface, roleswap, sidefields
from rsched.engine.actionschema import ACTION_SCHEMA


def _loop(models: dict[str, str]):
    """A loop stand-in with the two things the applier touches: the models mapping and the
    message list `enginenote` appends to. Deliberately NOT a RunContext — the applier must
    stay narrow enough to work on exactly these two.
    """
    events: list[dict] = []
    ctx = SimpleNamespace(
        routine=SimpleNamespace(models=dict(models)),
        transcript=SimpleNamespace(event=lambda kind, payload: events.append(
            {"type": kind, **payload})))
    return SimpleNamespace(ctx=ctx, messages=[]), events


# ---- 1. the field reaches the engine


def test_the_field_is_declared_and_survives_normalisation_and_projection():
    """ACTION_SCHEMA sets `additionalProperties: false`, so a field declared nowhere is
    stripped before any validator or applier runs: no error, no log line, a feature that
    silently does nothing. Three gates must each keep it.
    """
    assert ACTION_SCHEMA["properties"]["role_swap"]["enum"] == list(roleswap.POSITIONS)
    assert "role_swap" in actions.SIDE_FIELDS

    kept = actions.normalize_action(
        {"say": "x", "kind": "read_file", "path": "p", "role_swap": "cheap"})
    assert kept["role_swap"] == "cheap", "the normaliser dropped the side field"

    # …and the capability projection, which trims the schema to a workflow's kinds: a field
    # that is not universal is trimmed with the kinds that named it.
    projected = kindsurface.schema_for_kinds({"read_file", "finish"}, reminders=False)
    assert "role_swap" in projected["properties"]


def test_it_rides_a_kind_whose_own_fields_it_is_not():
    """A side field is defined by riding EVERY kind. `validate_action` builds its allowed-field
    set per kind, so a field missing from SIDE_FIELDS is rejected as a stray on every kind.
    """
    for kind, extra in (("read_file", {"path": "p"}),
                        ("finish", {"status": "ok", "summary": "done"}),
                        ("wait", {})):
        action = {"say": "x", "kind": kind, "role_swap": "cheap", **extra}
        assert actions.validate_action(action) == [], f"{kind} rejected the side field"


# ---- 2. the check that costs no turn


def test_a_value_that_is_not_a_position_is_corrected_not_executed():
    bad = {"say": "x", "kind": "wait", "role_swap": "cheeep"}
    problems = actions.validate_action(bad)
    assert problems, "a non-position was accepted"
    assert "cheap" in problems[0] and "strong" in problems[0], (
        "the correction must name the positions, or the retry spends another turn guessing")
    assert roleswap.field_problems({"say": "x", "kind": "wait"}) == []   # absent is fine


# ---- 3. the swap itself


def test_cheap_serves_main_from_the_routines_tool_call_model():
    """The mechanism is the one `switches.apply_model_switch` uses for the operator's console
    switch: assign `routine.models['main']`. `endpoints.for_model` re-resolves the role every
    turn, so the model, its window and its effort all self-correct at the next completion.
    """
    loop, _events = _loop({"main": "opus", "tool_call": "haiku"})
    sidefields.land(loop, {"say": "x", "kind": "wait", "role_swap": "cheap"})
    assert loop.ctx.routine.models["main"] == "haiku"


def test_strong_climbs_back_to_the_model_the_routine_configured():
    """The climb-back is the half a naive implementation loses: `models['main']` is the thing
    being mutated, so the run must remember where it came from before the first swap.
    """
    loop, _events = _loop({"main": "opus", "tool_call": "haiku"})
    sidefields.land(loop, {"say": "x", "kind": "wait", "role_swap": "cheap"})
    sidefields.land(loop, {"say": "x", "kind": "wait", "role_swap": "strong"})
    assert loop.ctx.routine.models["main"] == "opus"


def test_swapping_to_where_you_already_are_says_nothing():
    """A repeated swap is not an error — a run re-asserting `cheap` across several mechanical
    turns is exactly the intended use — but it must not append a note every time, or a long
    cheap stretch pays for the feature in prompt tokens.
    """
    loop, _events = _loop({"main": "opus", "tool_call": "haiku"})
    for _ in range(4):
        sidefields.land(loop, {"say": "x", "kind": "wait", "role_swap": "cheap"})
    assert loop.ctx.routine.models["main"] == "haiku"
    assert len(loop.messages) == 1, "a repeated swap re-announced itself"


def test_an_action_without_the_field_changes_nothing():
    loop, _events = _loop({"main": "opus", "tool_call": "haiku"})
    sidefields.land(loop, {"say": "x", "kind": "wait"})
    assert loop.ctx.routine.models["main"] == "opus"
    assert loop.messages == []


def test_a_routine_with_no_cheap_model_is_told_instead_of_silently_served():
    """`for_model` falls back to the server's system model for an unset role — which is not
    necessarily cheaper and is not what the run asked for. Serving that silently would teach
    the run that swapping works when it did not, so the run is told and the model is unchanged.
    """
    loop, _events = _loop({"main": "opus"})
    sidefields.land(loop, {"say": "x", "kind": "wait", "role_swap": "cheap"})
    assert loop.ctx.routine.models["main"] == "opus"
    assert "could not be served" in loop.messages[-1]["content"]
    assert "Do not retry" in loop.messages[-1]["content"]


# ---- 4. the run is told, and so is the transcript


def test_the_swap_is_announced_as_an_engine_note_and_recorded():
    """Auditability was a condition of the operator's choice. The swap reaches the model as an
    appended ENGINE NOTE (the prompt is immutable under the caching contract) and the same
    string is recorded, so a resumed leg replays what the model actually read.
    """
    loop, events = _loop({"main": "opus", "tool_call": "haiku"})
    sidefields.land(loop, {"say": "x", "kind": "wait", "role_swap": "cheap"})

    assert loop.messages[-1]["role"] == "user"
    assert loop.messages[-1]["content"].startswith("ENGINE NOTE: ")
    assert "haiku" in loop.messages[-1]["content"]
    recorded = [e for e in events if e["type"] == "user_injection"]
    assert recorded and recorded[-1]["source"] == "engine"
    assert recorded[-1]["text"] in loop.messages[-1]["content"]


def test_the_note_tells_the_run_how_to_get_back():
    """A run that cannot find its way back stays cheap for the rest of the run — the failure
    mode that makes the whole feature a net loss.
    """
    loop, _events = _loop({"main": "opus", "tool_call": "haiku"})
    sidefields.land(loop, {"say": "x", "kind": "wait", "role_swap": "cheap"})
    assert "role_swap=strong" in loop.messages[-1]["content"]


def test_the_note_is_filed_by_the_same_landing_call():
    """`sidefields.land` replaced `notes.capture` at loop._land, so the note channel must still
    work — a side field's landing is one seam now, and a regression here is silent.
    """
    import rsched.engine.notes as notes_mod

    seen: list[tuple] = []
    original = notes_mod.capture
    try:
        notes_mod.capture = lambda ctx, action: seen.append((ctx, action))
        loop, _events = _loop({"main": "opus", "tool_call": "haiku"})
        sidefields.land(loop, {"say": "x", "kind": "wait", "note": "keep me"})
    finally:
        notes_mod.capture = original
    assert seen and seen[-1][1]["note"] == "keep me"
