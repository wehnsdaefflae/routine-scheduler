"""The instance-wide default for the `tool_call` role (D230).

The measured defect: of 32 routines exactly one had a distinct `main`/`tool_call` pair, and that
one only because the operator hand-applied a config_patch for it. Every other routine's
`role_swap: cheap` turn resolved `tool_call` through `EndpointRegistry.for_model_chain` to the
SAME value an unset `main` lands on — the server's `system_model` — so the swap saved nothing
while the run believed it had. The mechanism worked; there was no instance default for the role
to resolve through.

So `ServerConfig` carries one: `system_tool_call_model` (+ `system_tool_call_effort`, for
symmetry with the four per-purpose pairs beside it). It SHIPS EMPTY, and the two halves of that
promise are pinned here because they are the whole scope:

* empty = today's behaviour EXACTLY — an unset `tool_call` keeps falling through to
  `system_model` at `system_effort`, so nothing changes for any routine at the release;
* set = it reaches every routine that has no `tool_call` of its own, and nothing else: the
  routine's own override still wins, and `main` / `uncensored` / `supervisor` are untouched.

WHICH catalog model it should name is the operator's decision and is deliberately absent from
this file and from every default in the tree.
"""

from __future__ import annotations

import yaml

from conftest import usage_run
from helpers import server_config, tmp_server
from rsched.config import EndpointConfig, ModelConfig, load_server_config
from rsched.config.base import supervisor_role
from rsched.endpoints import EndpointRegistry

# ---- fixtures ----------------------------------------------------------------------------

#: `Big` and `Cheap` sit on DIFFERENT endpoints with different credentials on purpose: the whole
#: point of the item is that the two roles stop sharing one provider model, and a pair that
#: differs only by catalog name would still be one model with one set of biases.
_ENDPOINTS = {
    "big_ep": ("http://big/v1", "BIG_KEY"),
    "cheap_ep": ("http://cheap/v1", "CHEAP_KEY"),
}
_MODELS = {"Big": "big_ep", "Cheap": "cheap_ep", "Other": "big_ep"}


def _server(**attrs):
    eps = {name: EndpointConfig(name=name, kind="openai", base_url=url, key_var=key)
           for name, (url, key) in _ENDPOINTS.items()}
    models = {name: ModelConfig(name=name, endpoint=ep, model=f"vendor/{name.lower()}")
              for name, ep in _MODELS.items()}
    return server_config(endpoints=eps, models=models, **attrs)


def _ref(server, role, models=None, efforts=None):
    """The ModelRef the engine's own resolution hands a turn for `role`."""
    chain = EndpointRegistry(server).for_model_chain(role, models or {}, efforts or {})
    return chain[0][1]


def _load(tmp_path, **raw):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_server_config(path)


# ---- half one: EMPTY is today's behaviour, exactly ---------------------------------------


def test_an_empty_default_leaves_an_unset_tool_call_on_the_system_model():
    """The release must change NOTHING for any routine until the operator sets a value."""
    server = _server(system_model="Big", system_effort="high")

    ref = _ref(server, "tool_call")

    assert ref.model == "vendor/big"
    assert ref.effort == "high"                      # the system model's own effort, as today
    assert _ref(server, "main").model == "vendor/big"


def test_an_empty_default_is_not_a_config_problem(tmp_path):
    _server_cfg, problems = _load(tmp_path, system_model="Big")
    assert not [p for p in problems if "tool_call" in p]


def test_role_default_model_states_the_precedence_as_a_pure_function():
    """One resolver, so the engine, the readouts and the console cannot drift apart."""
    from rsched.config.base import role_default_model

    empty = _server(system_model="Big", system_effort="high")
    assert role_default_model(empty, "tool_call") == ("Big", "high")
    assert role_default_model(empty, "main") == ("Big", "high")

    set_up = _server(system_model="Big", system_effort="high",
                     system_tool_call_model="Cheap", system_tool_call_effort="low")
    assert role_default_model(set_up, "tool_call") == ("Cheap", "low")
    assert role_default_model(set_up, "main") == ("Big", "high")


def test_role_default_model_survives_a_minimal_server_stand_in():
    """Same tolerance `conformance.system_model_problems` documents: `rsched validate`'s own
    tests hand these readers a stand-in carrying only the fields they need."""
    from types import SimpleNamespace

    from rsched.config.base import role_default_model

    assert role_default_model(SimpleNamespace(), "tool_call") == ("", "")


# ---- half two: a SET value reaches every routine that has no override of its own ---------


def test_a_set_default_gives_an_unset_tool_call_role_a_cheaper_model():
    server = _server(system_model="Big", system_tool_call_model="Cheap")

    assert _ref(server, "tool_call").model == "vendor/cheap"
    assert _ref(server, "main").model == "vendor/big"


def test_the_pair_a_routine_with_no_models_block_resolves_to_is_distinct():
    """THE proof predicate: a routine naming nothing of its own must still report two
    different provider models for main and tool_call."""
    server = _server(system_model="Big", system_tool_call_model="Cheap")

    main, tool = _ref(server, "main"), _ref(server, "tool_call")

    assert main.model != tool.model
    assert (main.endpoint, tool.endpoint) == ("big_ep", "cheap_ep")


def test_a_routines_own_tool_call_override_still_wins():
    server = _server(system_model="Big", system_tool_call_model="Cheap")

    assert _ref(server, "tool_call", models={"tool_call": "Other"}).model == "vendor/other"


def test_the_default_touches_no_other_role():
    server = _server(system_model="Big", system_tool_call_model="Cheap")

    assert _ref(server, "main").model == "vendor/big"
    # uncensored has no server fallback at all — an unset role means referral off
    assert EndpointRegistry(server).for_uncensored({}, {}) is None
    # supervisor resolves through MAIN (D164), never through the tool-call default
    assert supervisor_role({}) == "main"


def test_the_role_effort_a_routine_sets_still_outranks_the_default_effort():
    server = _server(system_model="Big", system_tool_call_model="Cheap",
                     system_tool_call_effort="low")

    assert _ref(server, "tool_call").effort == "low"
    assert _ref(server, "tool_call", efforts={"tool_call": "max"}).effort == "max"


def test_a_default_model_with_no_effort_of_its_own_runs_at_the_providers_default():
    """`system_effort` belongs to the SYSTEM model; it must not leak onto a different model
    the operator pointed the tool-call role at."""
    server = _server(system_model="Big", system_effort="high", system_tool_call_model="Cheap")

    assert _ref(server, "tool_call").effort is None


# ---- the loader reports, never crashes ---------------------------------------------------
# (the two SWAP LAYERS that SPEND the saving — the second half of the operator's proof — are
# pinned in tests/test_tool_call_default_swaps.py)


def test_a_default_off_the_catalog_is_a_problem_line_not_a_crash(tmp_path):
    cfg, problems = _load(tmp_path, system_model="Big", system_tool_call_model="Ghost",
                          endpoints={"big_ep": {"kind": "openai", "base_url": "http://b/v1"}},
                          models={"Big": {"endpoint": "big_ep", "model": "vendor/big"}})

    assert cfg.system_tool_call_model == "Ghost"      # kept, so the operator sees what they typed
    assert [p for p in problems if p.startswith("system_tool_call_model:")]


def test_a_bad_default_effort_is_reported_and_dropped(tmp_path):
    cfg, problems = _load(tmp_path, system_tool_call_effort="screaming")

    assert cfg.system_tool_call_effort == ""
    assert [p for p in problems if p.startswith("system_tool_call_effort:")]


def test_the_two_new_fields_are_real_server_config_fields():
    from rsched.config import ServerConfig

    for field in ("system_tool_call_model", "system_tool_call_effort"):
        assert field in ServerConfig.model_fields
        assert ServerConfig().model_dump()[field] == ""      # ships EMPTY


# ---- the operator's two doors onto it: the patch surface and the settings route ----------


def test_the_server_patch_vocabulary_carries_both_pointers():
    """Without this the operator cannot land the value at all: `config_patch` is judged
    against `SERVER_PATCH_FIELDS` at filing and against `ServerPatch` at the apply."""
    from rsched import configflow
    from rsched.web.settings.serverpatch import ServerPatch

    assert {"system_tool_call_model", "system_tool_call_effort"} <= configflow.SERVER_PATCH_FIELDS
    assert set(ServerPatch.model_fields) == configflow.SERVER_PATCH_FIELDS


def test_the_patch_route_refuses_a_default_the_catalog_does_not_carry():
    from rsched.web.settings.serverpatch import _catalog_problem

    server = _server(system_model="Big")

    assert "not a catalog model" in _catalog_problem(server, {"system_tool_call_model": "Ghost"})
    assert _catalog_problem(server, {"system_tool_call_model": "Cheap"}) == ""
    # blanking it is a real intent and must always pass
    assert _catalog_problem(server, {"system_tool_call_model": ""}) == ""
    assert "must be one of" in _catalog_problem(server, {"system_tool_call_effort": "screaming"})


def test_the_settings_route_for_the_tool_call_default_is_mounted():
    """Regression shape of F395: a handler nobody wired into the settings aggregate 404s
    while the console calls it."""
    from rsched.web.settings import router as settings_router

    mounted: set[str] = {rt.path for rt in settings_router.routes if hasattr(rt, "path")}
    for inc in settings_router.routes:
        sub = getattr(inc, "original_router", None)
        if sub is not None:
            mounted |= {rt.path for rt in sub.routes if hasattr(rt, "path")}
    assert "/settings/tool-call-model" in mounted
    assert "/settings/system-model" in mounted      # the traversal itself is sound


# ---- the readouts: the pair must READ distinct, or the proof predicate cannot be met -----


def test_the_instance_check_names_an_unresolvable_tool_call_default():
    from rsched.conformance import system_model_problems

    server = _server(system_model="Big", system_tool_call_model="Ghost")

    # (the fixture's `Big` has no fallback, so the pre-existing single-chain note comes too —
    # this pins the line the NEW pointer is responsible for)
    mine = [p for p in system_model_problems(server) if p.startswith("system_tool_call_model ")]

    assert len(mine) == 1
    assert "not in the model catalog" in mine[0]
    # ...and an UNSET pointer raises nothing: that is how it ships
    assert not [p for p in system_model_problems(_server(system_model="Big"))
                if "tool_call" in p]


def test_the_per_routine_readout_resolves_the_pair_through_the_default(tmp_path):
    """`readmodels/stats.py` resolved ONLY `main`, so a per-routine readout showed one model
    and the pair read identical however the instance was configured."""
    from rsched.readmodels.stats import aggregate

    home = tmp_path / "routines"
    routine = home / "alpha"
    (routine / "state").mkdir(parents=True)
    (routine / "routine.yaml").write_text(yaml.safe_dump({
        "name": "Alpha", "slug": "alpha", "enabled": True, "description": "A test routine.",
        "schedule": {"cron": "0 7 * * 1", "tz": "Etc/UTC", "catchup": "skip"},
    }), encoding="utf-8")     # NO models: block — the routine overrides neither role
    usage_run(routine, "20260712-070000", "finished", tin=10, tout=5, elapsed_s=30,
              model="big_ep/vendor/big")

    server = tmp_server(tmp_path, create=False, conversations_home=tmp_path / "conversations",
                        endpoints={name: EndpointConfig(name=name, kind="openai", base_url=url,
                                                        key_var=key)
                                   for name, (url, key) in _ENDPOINTS.items()},
                        models={name: ModelConfig(name=name, endpoint=ep,
                                                  model=f"vendor/{name.lower()}")
                                for name, ep in _MODELS.items()},
                        system_model="Big", system_tool_call_model="Cheap")

    row = aggregate(server)["by_routine"]["alpha"]

    assert (row["model"], row["endpoint"]) == ("vendor/big", "big_ep")
    assert (row["tool_call_model"], row["tool_call_endpoint"]) == ("vendor/cheap", "cheap_ep")
    assert row["model"] != row["tool_call_model"]


def test_the_shared_verifier_warning_falls_silent_once_the_fleet_default_splits_the_roles():
    """`verifier_model.py` had its own copy of the precedence, so it would have gone on
    reporting "both unset, so both run the system model" about an instance where the default
    had already made the claim verifier a different model."""
    from rsched.verifier_model import shared_verifier_problem

    both_on_system = _server(system_model="Big")
    line = shared_verifier_problem(both_on_system, {})
    assert line and "both unset, so both run the system model" in line

    split = _server(system_model="Big", system_tool_call_model="Cheap")
    assert shared_verifier_problem(split, {}) is None


def test_the_routine_page_payload_names_the_tool_call_default():
    """The blank `tool_call` option on a routine's page says what the engine resolves it to, so
    the page needs the pointer the engine now reads (`routine-config-models.js` blankLabel)."""
    from types import SimpleNamespace

    from rsched.web.config_fields import effort_picker

    server = _server(system_model="Big", system_effort="high",
                     system_tool_call_model="Cheap", system_tool_call_effort="low")
    payload = effort_picker(server, SimpleNamespace(efforts={}))

    assert payload["system_effort"] == "high"
    assert payload["system_tool_call_model"] == "Cheap"
    assert payload["system_tool_call_effort"] == "low"


def test_the_routine_page_payload_leaves_an_unset_default_null():
    from types import SimpleNamespace

    from rsched.web.config_fields import effort_picker

    payload = effort_picker(_server(system_model="Big"), SimpleNamespace(efforts={}))

    assert payload["system_tool_call_model"] is None
    assert payload["system_tool_call_effort"] is None
