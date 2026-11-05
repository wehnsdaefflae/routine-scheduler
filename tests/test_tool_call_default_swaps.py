"""The two SWAP LAYERS, once the fleet has a default for the `tool_call` role (D230).

The operator's proof predicate has a second half: *"a run that calls role_swap cheap shows a
lower per-turn cost in its own usage row"*. Neither swap layer goes through
`EndpointRegistry.for_model_chain` — both read `routine.models["tool_call"]` and stand down when
it is empty — so binding the role for the `llm` action and the claim verifier alone would have
changed NOTHING for the mechanism D217/D230 exist to feed: 31 of 32 routines would still read
"could not be served".

`autoswap._cheap_model` recorded exactly why it stood down: an unset role resolved to
`system_model`, "which is not necessarily cheaper" — the model `main` was already running. A
default FOR THE ROLE is what replaces that premise, so both layers now resolve through the one
rule (`roleswap.cheap_model`), which still answers None when the resolved cheap model is the one
`main` runs on. With no default set, nothing here changes behaviour at all.

Split from `tests/test_tool_call_default.py` (the config + resolution + readout half) to keep
both files under the repo's 350-line bar.
"""

from __future__ import annotations

from types import SimpleNamespace

from helpers import server_config
from rsched.config import EndpointConfig, ModelConfig
from rsched.endpoints import EndpointRegistry

#: `Big` and `Cheap` sit on different endpoints with different credentials, as in the sibling
#: file: the point of the item is that the two roles stop sharing one provider model.
_ENDPOINTS = {"big_ep": ("http://big/v1", "BIG_KEY"), "cheap_ep": ("http://cheap/v1", "CHEAP_KEY")}
_MODELS = {"Big": "big_ep", "Cheap": "cheap_ep", "Other": "big_ep"}


def _server(**attrs):
    eps = {name: EndpointConfig(name=name, kind="openai", base_url=url, key_var=key)
           for name, (url, key) in _ENDPOINTS.items()}
    models = {name: ModelConfig(name=name, endpoint=ep, model=f"vendor/{name.lower()}")
              for name, ep in _MODELS.items()}
    return server_config(endpoints=eps, models=models, **attrs)


def _swap_loop(server, models: dict[str, str]):
    """A loop stand-in carrying what the appliers touch (`tests/test_role_swap.py::_loop`),
    plus the server the instance default is read from."""
    events: list[dict] = []
    ctx = SimpleNamespace(
        routine=SimpleNamespace(models=dict(models)), server=server,
        transcript=SimpleNamespace(event=lambda kind, payload: events.append(
            {"type": kind, **payload})))
    return SimpleNamespace(ctx=ctx, messages=[]), events


# ---- the run's own swap (D185) -----------------------------------------------------------


def test_role_swap_cheap_is_served_by_the_fleet_default_when_the_routine_names_none():
    from rsched.engine import roleswap

    loop, events = _swap_loop(_server(system_model="Big", system_tool_call_model="Cheap"),
                              {"main": "Big"})

    roleswap.apply(loop, {"say": "x", "kind": "wait", "role_swap": "cheap"})

    assert loop.ctx.routine.models["main"] == "Cheap"
    assert "could not be served" not in str(events)


def test_role_swap_cheap_still_stands_down_with_no_default_set():
    """Unset, nothing changes: `tool_call` falls through to `system_model`, which is the model
    `main` already runs — so there is no cheaper model and the stand-down is the honest answer.
    """
    from rsched.engine import roleswap

    loop, events = _swap_loop(_server(system_model="Big"), {"main": "Big"})

    roleswap.apply(loop, {"say": "x", "kind": "wait", "role_swap": "cheap"})

    assert loop.ctx.routine.models["main"] == "Big"
    assert "could not be served" in str(events)


def test_role_swap_never_serves_cheap_with_the_model_main_already_runs():
    """A default pointed at the model `main` runs is no saving, and must not be announced as
    one — the same nothing `autoswap` always refused to claim."""
    from rsched.engine import roleswap

    loop, events = _swap_loop(_server(system_model="Big", system_tool_call_model="Big"),
                              {"main": "Big"})

    roleswap.apply(loop, {"say": "x", "kind": "wait", "role_swap": "cheap"})

    assert loop.ctx.routine.models.get("main") == "Big"
    assert "now answered by" not in str(events)


def test_a_routine_that_configures_no_main_can_climb_back_out_of_cheap():
    """THE hazard the fleet default creates. `cheap` was unservable for a routine naming no
    `tool_call`, so the climb-back was never exercised for one naming no `main` either —
    `configured_main` is "" there, and a `strong` that can name no model would leave the run
    stranded on the cheap model for the rest of the run. Removing the override is the honest
    inverse: `main` resolves through the instance default again, exactly as before the swap.
    """
    from rsched.engine import roleswap

    loop, events = _swap_loop(_server(system_model="Big", system_tool_call_model="Cheap"), {})

    roleswap.apply(loop, {"say": "x", "kind": "wait", "role_swap": "cheap"})
    assert loop.ctx.routine.models["main"] == "Cheap"

    roleswap.apply(loop, {"say": "x", "kind": "wait", "role_swap": "strong"})
    assert "main" not in loop.ctx.routine.models
    # ...and that is a real climb-back, not a silent stranding
    chain = EndpointRegistry(loop.ctx.server).for_model_chain(
        "main", loop.ctx.routine.models, {})
    assert chain[0][1].model == "vendor/big"
    assert "could not be served" not in str(events)


# ---- the engine-served cheap poll (D217/F785) --------------------------------------------


def test_the_engine_served_cheap_poll_uses_the_fleet_default_and_restores_from_it():
    """`autoswap` is the half D217 shipped; the fleet default is what makes it reach a routine
    that configures no `tool_call` of its own — which is 31 of the 32."""
    from rsched.engine import autoswap

    loop, _events = _swap_loop(_server(system_model="Big", system_tool_call_model="Cheap"),
                               {"main": "Big"})
    # the SAME poll class, not a byte-identical action: an identical streak that deep is the
    # stuck-run escape turn, which this layer deliberately never serves cheap
    for n in range(1, autoswap.SERVE_FROM + 1):
        autoswap.at_landing(loop, {"say": "x", "kind": "wait", "n": n})

    assert loop.ctx.routine.models["main"] == "Cheap"

    autoswap.at_landing(loop, {"say": "x", "kind": "read_file", "path": "p"})
    assert loop.ctx.routine.models["main"] == "Big"


def test_the_engine_served_poll_still_does_nothing_without_a_default():
    from rsched.engine import autoswap

    loop, events = _swap_loop(_server(system_model="Big"), {"main": "Big"})
    for n in range(1, autoswap.SERVE_FROM + 1):
        autoswap.at_landing(loop, {"say": "x", "kind": "wait", "n": n})

    assert loop.ctx.routine.models["main"] == "Big"
    assert "serving your REPEAT POLLS" not in str(events)


def test_an_engine_served_poll_restores_a_routine_that_configures_no_main():
    """The autoswap mirror of the stranding hazard: its restore guard compared `main` with
    `models['tool_call']`, which the fleet default leaves EMPTY — a guard reading that key
    would refuse every restore and leave the run cheap for good.
    """
    from rsched.engine import autoswap

    loop, _events = _swap_loop(_server(system_model="Big", system_tool_call_model="Cheap"), {})
    for n in range(1, autoswap.SERVE_FROM + 1):
        autoswap.at_landing(loop, {"say": "x", "kind": "wait", "n": n})
    assert loop.ctx.routine.models["main"] == "Cheap"

    autoswap.at_landing(loop, {"say": "x", "kind": "read_file", "path": "p"})
    assert "main" not in loop.ctx.routine.models


def test_both_layers_answer_one_question_about_a_cheaper_model():
    """One rule (`roleswap.cheap_model`), because two would disagree on exactly the fleet D230
    was built for — and a run's own `strong` must climb back correctly out of a swap the ENGINE
    made, which it can only do if both layers resolved the same model."""
    from rsched.engine import autoswap, roleswap

    for server, expected in ((_server(system_model="Big", system_tool_call_model="Cheap"),
                              "Cheap"),
                             (_server(system_model="Big"), None),
                             (_server(system_model="Big", system_tool_call_model="Big"), None)):
        loop, _events = _swap_loop(server, {"main": "Big"})
        assert roleswap.cheap_model(loop.ctx) == expected
        assert autoswap._cheap_model(loop) == expected
