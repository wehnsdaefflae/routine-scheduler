"""A util can learn which DECISION models its routine reaches (D288, goal b6 increment 1).

The operator, 2026-11-14: **"Convert fully"** — `llm` and `decide` stop being action kinds and
become ONE util with both capabilities. The asymmetry this closes is why `decide` could only ever
be an action: `endpoints/routine_env` has injected the routine's INFERENCE endpoints into a
declaring util's environment since it shipped (`RSCHED_LLM` / `RSCHED_LLM_KEYS`), and the DECISION
catalog had no such injection at all. The engine knew which decision model a routine reaches and
a util did not, so a util could only hardcode one — which for a routine configured to a LOCAL
model is not a cost problem but a privacy breach, the same argument the inference half makes.

Both injections now live side by side in one module, deliberately: a reader who has understood
one has understood the other, and they cannot drift into two designs.

What is pinned here is the contract a util depends on:
  - the role map resolves a routine's own binding first, then the INSTANCE default — the same
    order `endpoints/decisions.pick_decision_model` uses, so a util reaches the model the engine
    would reach for that routine rather than a different one;
  - a role naming a model the catalog no longer carries reads as None, never substituted;
  - keys reach the ROLE endpoints only, so an engine-injected blob cannot route around the
    operator's own `secret:<NAME>` decision;
  - **the PROTOCOL rides the metadata**, which is F888's lesson rather than decoration: that
    defect cost nine days because the fix landed in the `openai` adapter while all three of this
    instance's models speak `systemone`, and a util that cannot see its protocol can only
    rediscover it by failing.
"""

from __future__ import annotations

import json

from rsched.config.decisionconf import DecisionEndpointConfig, DecisionModelConfig
from rsched.endpoints.routine_env import (
    DECISIONS_KEYS_VAR,
    DECISIONS_VAR,
    decision_env_for_routine,
    decision_roles,
    llm_env_vars,
)


def _server(**over):
    """A ServerConfig carrying a two-endpoint decision catalog, built the way the loader does."""
    from rsched.config.server import ServerConfig

    endpoints = {
        "otari-decisions": DecisionEndpointConfig(
            name="otari-decisions", protocol="systemone", base_url="https://one.example",
            api_key="sk-systemone", timeout_s=60),
        "cloud": DecisionEndpointConfig(
            name="cloud", protocol="openai", base_url="https://cloud.example",
            api_key="sk-openai", timeout_s=60),
    }
    models = {
        "jev": DecisionModelConfig(name="jev", endpoint="otari-decisions", model="typesafe/jev"),
        "vl8b": DecisionModelConfig(name="vl8b", endpoint="otari-decisions",
                                    model="qwen-vl8b", multimodal=True),
        "gpt": DecisionModelConfig(name="gpt", endpoint="cloud", model="gpt-6-luna"),
    }
    return ServerConfig(decision_endpoints=endpoints, decision_models=models,
                        decision_model="jev", decision_media_model="vl8b", **over)


def _meta(env: dict) -> dict:
    return json.loads(env[DECISIONS_VAR])


def test_the_injection_is_declared_so_settings_does_not_ask_for_it_as_a_secret():
    """These vars are assembled from `decision_endpoints:`, never set by the user — the same
    reason the inference pair is declared here."""
    assert {DECISIONS_VAR, DECISIONS_KEYS_VAR} <= llm_env_vars()


def test_a_util_is_told_the_catalog_its_routine_actually_reaches():
    env = decision_env_for_routine("probe", {}, _server())
    meta = _meta(env)
    assert meta["routine"] == "probe"
    assert set(meta["models"]) == {"jev", "vl8b", "gpt"}
    assert set(meta["endpoints"]) == {"cloud", "otari-decisions"}


def test_the_protocol_rides_the_metadata_because_f888_cost_nine_days_without_it():
    """A util that cannot see which protocol it is talking to can only rediscover it by failing
    — and the shipped fix for that class of defect landed in an adapter this instance never
    executes."""
    meta = _meta(decision_env_for_routine("probe", {}, _server()))
    assert meta["endpoints"]["otari-decisions"]["protocol"] == "systemone"
    assert meta["endpoints"]["cloud"]["protocol"] == "openai"
    assert meta["roles"]["decision"]["protocol"] == "systemone"


def test_a_routines_own_binding_outranks_the_instance_default():
    """The order `pick_decision_model` uses. A util told only about instance defaults would
    reach a different model than the engine does for the same routine."""
    roles = decision_roles({"decision": "gpt"}, _server())
    assert roles["decision"]["catalog"] == "gpt"
    assert roles["decision"]["endpoint"] == "cloud"
    # …and the unbound media role still falls back to the instance's own
    assert roles["decision_media"]["catalog"] == "vl8b"


def test_an_unbound_role_falls_back_to_the_instance_default():
    roles = decision_roles({}, _server())
    assert roles["decision"]["catalog"] == "jev"
    assert roles["decision_media"]["catalog"] == "vl8b"


def test_a_role_naming_a_model_the_catalog_lost_reads_as_absent():
    """Never substituted: a default going missing must be VISIBLE to the util, because the
    alternative is a util quietly sending work to a model nobody configured."""
    roles = decision_roles({"decision": "a-model-that-was-deleted"}, _server())
    assert roles["decision"] is None


def test_an_instance_with_no_decision_catalog_says_so_rather_than_inventing_one():
    """"This instance has no media-capable decision model" must be distinguishable from "I was
    told nothing" — the property the inference half's docstring names."""
    from rsched.config.server import ServerConfig

    meta = _meta(decision_env_for_routine("probe", {}, ServerConfig()))
    assert meta["roles"] == {"decision": None, "decision_media": None}
    assert meta["endpoints"] == {} and meta["models"] == {}


def test_keys_reach_the_role_endpoints_only():
    """An engine-injected blob of every endpoint key would route around the operator's own
    `secret:<NAME>` decision, which is not a widening a run gets to make for him. Here both
    roles sit on `otari-decisions`, so `cloud`'s key must NOT be injected.
    """
    env = decision_env_for_routine("probe", {}, _server())
    assert list(json.loads(env[DECISIONS_KEYS_VAR])) == ["otari-decisions"]
    assert json.loads(env[DECISIONS_KEYS_VAR])["otari-decisions"] == "sk-systemone"


def test_a_key_never_rides_the_non_secret_metadata():
    """`has_key` reports whether one resolved; the value lives only in the KEYS var, whose name
    ends in KEYS so the util-authoring gate forces its declaration."""
    env = decision_env_for_routine("probe", {}, _server())
    blob = env[DECISIONS_VAR]
    assert "sk-systemone" not in blob and "sk-openai" not in blob
    assert _meta(env)["endpoints"]["cloud"]["has_key"] is True


def test_a_keyless_local_endpoint_reads_as_keyless_rather_than_as_configured():
    """`none` is the placeholder bearer keyless local backends ignore, and the inference half
    already refuses to call it a key."""
    from rsched.config.server import ServerConfig

    server = ServerConfig(
        decision_endpoints={"local": DecisionEndpointConfig(
            name="local", protocol="systemone", base_url="http://127.0.0.1:9", api_key="none")},
        decision_models={"tiny": DecisionModelConfig(name="tiny", endpoint="local",
                                                     model="tiny-1")},
        decision_model="tiny")
    env = decision_env_for_routine("probe", {}, server)
    assert _meta(env)["endpoints"]["local"]["has_key"] is False
    assert json.loads(env[DECISIONS_KEYS_VAR]) == {}
