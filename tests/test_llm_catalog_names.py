"""The INFERENCE injection carries the catalog's model NAMES, so a util can serve the `llm`
action's `model` field (D288, goal b6).

The asymmetry this closes was measured, not guessed. `decision_env_for_routine` has carried a
`models` catalog since 0.544.0 — every configured decision model by NAME, with its endpoint and
provider id — precisely so a util can honour `decide`'s `model` override. The inference half
carried `roles` and `endpoints` only. So a util could reach what a ROLE resolves to but could not
resolve `claude-opus-5` from the name `opus`, which is what the `llm` action's `model` field
accepts: 7 of the 18 live `llm` action calls on this instance (2026-11-15) pass one.

While that gap stood, "one util covers both capabilities" was nominal: a caller migrating off the
action would silently lose the model override, and the kind could not be removed without taking
something away. The keys discipline does NOT widen — keys still ride for ROLE endpoints only
(`test_the_catalog_does_not_widen_which_keys_ride`), so naming a catalog model on an endpoint the
routine has no role on tells the util the model EXISTS and lets the call fail visibly against a
missing key, rather than routing around the operator's own `secret:` decision.
"""

from __future__ import annotations

import json

from helpers import server_config
from rsched.config import EndpointConfig, ModelConfig
from rsched.endpoints import routine_env


def _ep(name: str, **kw) -> EndpointConfig:
    cfg = EndpointConfig(kind=kw.pop("kind", "openai"),
                         base_url=kw.pop("base_url", "http://192.168.0.201:8790/v1"), **kw)
    cfg.name = name
    return cfg


def _model(name: str, endpoint: str, model: str) -> ModelConfig:
    mc = ModelConfig(endpoint=endpoint, model=model)
    mc.name = name
    return mc


def _server(**over):
    attrs = {"endpoints": {"omen": _ep("omen"),
                           "otari": _ep("otari", kind="anthropic",
                                        key_var="OTARI_API_KEY")},
             "models": {"opus": _model("opus", "otari", "claude-opus-5"),
                        "sonnet": _model("sonnet", "otari", "claude-sonnet-5"),
                        "qwen": _model("qwen", "omen", "qwen3-vl-8b-instruct")},
             "system_model": "opus"}
    attrs.update(over)
    return server_config(**attrs)


def _meta(**over):
    env = routine_env.llm_env_for_routine("r", over.pop("models_map", {"main": "opus"}),
                                          _server(**over))
    return json.loads(env[routine_env.LLM_VAR])


def test_the_injection_carries_every_catalog_model_by_name():
    """The `llm` action takes a catalog NAME; a util told only about roles cannot resolve one."""
    catalog = _meta()["models"]
    assert set(catalog) == {"opus", "sonnet", "qwen"}
    assert catalog["opus"] == {"endpoint": "otari", "model": "claude-opus-5"}
    assert catalog["qwen"] == {"endpoint": "omen", "model": "qwen3-vl-8b-instruct"}


def test_the_catalog_is_shaped_exactly_like_the_decision_halfs():
    """One shape for both injections: a util reading one has learned to read the other.

    The decision half shipped first (0.544.0) and its shape is the precedent, so this is
    asserted against the decision builder's real output rather than against a copied literal.
    """
    from rsched.config.decisionconf import DecisionEndpointConfig, DecisionModelConfig

    server = _server(
        decision_endpoints={"d": DecisionEndpointConfig(
            name="d", protocol="systemone", base_url="https://one.example",
            api_key="sk-one", timeout_s=60)},
        decision_models={"jev": DecisionModelConfig(name="jev", endpoint="d",
                                                    model="typesafe/jev-1.13")},
        decision_model="jev")
    dec = json.loads(routine_env.decision_env_for_routine("r", {}, server)
                     [routine_env.DECISIONS_VAR])["models"]
    inf = json.loads(routine_env.llm_env_for_routine("r", {"main": "opus"}, server)
                     [routine_env.LLM_VAR])["models"]
    assert sorted(next(iter(dec.values()))) == sorted(next(iter(inf.values())))


def test_a_catalog_model_names_an_endpoint_the_injection_also_describes():
    """A name is only useful if the endpoint beside it is in the same payload — otherwise the
    util learns a model exists and still cannot find its wire or its kind."""
    meta = _meta()
    for entry in meta["models"].values():
        assert entry["endpoint"] in meta["endpoints"]
    assert meta["endpoints"]["otari"]["kind"] == "anthropic"
    assert meta["endpoints"]["omen"]["kind"] == "openai"


def test_a_model_on_no_configured_endpoint_is_left_out_rather_than_offered():
    """A stale catalog entry must not reach a util as a callable model: it would fail at the
    wire with nothing naming the cause. `role_endpoints` drops such a role for the same reason.
    """
    catalog = _meta(models={"ghost": _model("ghost", "gone", "m"),
                            "opus": _model("opus", "otari", "claude-opus-5")},
                    system_model="opus")["models"]
    assert "ghost" not in catalog and "opus" in catalog


def test_the_catalog_does_not_widen_which_keys_ride():
    """The keys discipline is unchanged: ROLE endpoints only.

    `qwen` sits on `omen`, which no role of this routine resolves to. Its NAME reaches the util
    (so a call can be formed and fail visibly) while its KEY does not — a blob of every
    endpoint's key would route around the operator's own `secret:<NAME>` decision, which is not
    a widening a run gets to make for him.
    """
    env = routine_env.llm_env_for_routine(
        "r", {"main": "opus"}, _server(endpoints={"omen": _ep("omen", key_var="OMEN_AGENT_KEY"),
                                                  "otari": _ep("otari", kind="anthropic",
                                                               key_var="OTARI_API_KEY")}))
    meta = json.loads(env[routine_env.LLM_VAR])
    keys = json.loads(env[routine_env.LLM_KEYS_VAR])
    assert "qwen" in meta["models"]
    assert "omen" not in keys


def test_the_role_map_and_the_endpoint_map_are_untouched():
    """A pure addition: nothing that existed before this change reads differently."""
    meta = _meta()
    assert meta["roles"]["main"] == {"catalog": "opus", "endpoint": "otari",
                                     "model": "claude-opus-5"}
    assert meta["routine"] == "r" and meta["default"] == "tool_call"
    assert sorted(meta["endpoints"]) == ["omen", "otari"]
