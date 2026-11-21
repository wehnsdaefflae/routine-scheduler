"""The run's model SWAP (`role_swap`), once the fleet has a default for the `tool_call` role
(D230)."""

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
    one."""
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


def test_cheap_model_resolves_the_fleet_default_and_only_a_distinct_one():
    """One rule (`roleswap.cheap_model`): the routine's own `tool_call`, else the fleet default,
    and only when it differs from what `main` resolves to."""
    from rsched.engine import roleswap

    for server, expected in ((_server(system_model="Big", system_tool_call_model="Cheap"),
                              "Cheap"),
                             (_server(system_model="Big"), None),
                             (_server(system_model="Big", system_tool_call_model="Big"), None)):
        loop, _events = _swap_loop(server, {"main": "Big"})
        assert roleswap.cheap_model(loop.ctx) == expected
