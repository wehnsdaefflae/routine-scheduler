"""F868 — an `llm` call reaching an endpoint the CATALOG does not carry, and the engine telling
a util which endpoint its own routine runs on.

Two halves of one requirement ("it should reach any endpoint", operator, 2026-11-10). The
action's half: `model: "<endpoint>/<model id>"` resolves the ENDPOINT from the operator's
config and the model ID freely, so a run can send work to a local model — which is what a
privacy constraint needs and what the cloud-only catalog made impossible. The engine's half:
`RSCHED_LLM` / `RSCHED_LLM_KEYS`, so an LLM-calling util stops hardcoding a provider.

What is deliberately NOT here: a bare URL. The `llm` action runs in the engine, not in the util
jail, so an endpoint named by a model would be an unreviewed network destination; the endpoint
set stays the operator's and only the model id opens.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from helpers import server_config, util_library, write_util
from rsched import secrets, utils_run
from rsched.config import EndpointConfig, ModelConfig
from rsched.endpoints import EndpointRegistry, routine_env
from rsched.endpoints.base import Completion, EndpointError
from rsched.engine.exec_env import _extra_secrets, _llm_env
from rsched.engine.llmaction import do_list_models, do_llm
from rsched.engine.subruns import SubrunManager

DECLARING = '''"""llmish — declares the endpoint vars.

usage: gu llmish
tags: test
secrets: RSCHED_LLM, RSCHED_LLM_KEYS
net: outbound
"""
print("hi")
'''

PLAIN = '''"""plainish — declares no secrets.

usage: gu plainish
tags: test
"""
print("hi")
'''


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
    attrs = {"endpoints": {"omen": _ep("omen"), "otari": _ep("otari", key_var="OTARI_API_KEY")},
             "models": {"opus": _model("opus", "otari", "claude-opus-5")},
             "system_model": "opus"}
    attrs.update(over)
    return server_config(**attrs)


# ------------------------------------------------------------------ the endpoint resolver ----
def test_for_endpoint_resolves_an_off_catalog_model_on_a_configured_endpoint():
    """The whole point: `qwen3-vl-8b-instruct` is in no catalog entry on this instance, and the
    local box serving it is in no catalog at all — naming the ENDPOINT plus the id reaches it.
    """
    reg = EndpointRegistry(_server())
    endpoint, ref = reg.for_endpoint("omen/qwen3-vl-8b-instruct")
    assert ref.endpoint == "omen" and ref.model == "qwen3-vl-8b-instruct"
    assert endpoint.name == "omen"
    # EMPTY: there is no catalog entry, and inventing a name would make an unregistered model
    # indistinguishable from a registered one everywhere a ref is reported.
    assert ref.name == ""


def test_for_endpoint_inherits_the_endpoint_defaults_a_catalog_entry_would_carry():
    reg = EndpointRegistry(_server(endpoints={"omen": _ep("omen", context_tokens=32768,
                                                          temperature=0.2, max_tokens=4096)}))
    _endpoint, ref = reg.for_endpoint("omen/qwen3-vl-8b-instruct")
    assert ref.context_tokens == 32768
    assert ref.temperature == 0.2 and ref.max_tokens == 4096


def test_for_endpoint_rejects_an_unconfigured_endpoint_naming_the_configured_ones():
    reg = EndpointRegistry(_server())
    with pytest.raises(EndpointError) as exc:
        reg.for_endpoint("http://192.168.0.201:8790/qwen3")
    # a URL is not an endpoint name: it fails as the unconfigured name it is, and the error
    # teaches the names that DO work rather than only saying no
    assert "omen" in str(exc.value) and "otari" in str(exc.value)


def test_for_endpoint_sends_a_decision_endpoint_to_the_decide_action():
    """`predator` exists on this instance ONLY as a decision endpoint — a different wire (one
    typed answer, no free text). Naming it here is a wrong-action mistake, so it is answered as
    one instead of as a missing endpoint.
    """
    reg = EndpointRegistry(_server(decision_endpoints={"otari-decisions": object()}))
    with pytest.raises(EndpointError) as exc:
        reg.for_endpoint("otari-decisions/predator-vl8b")
    assert "decide" in str(exc.value) and "DECISION" in str(exc.value)


def test_for_endpoint_rejects_a_spec_with_no_model_id():
    reg = EndpointRegistry(_server())
    with pytest.raises(EndpointError) as exc:
        reg.for_endpoint("omen/")
    assert "omen/<model id" in str(exc.value)


# ------------------------------------------------------------------------- the llm action ----
class _Ep:
    def __init__(self, text="reply"):
        self.calls: list[dict] = []
        self._text = text

    def complete(self, messages, **kw):
        self.calls.append(kw)
        return Completion(text=self._text, parsed=None, usage={"in": 1, "out": 1})


class _Registry:
    def __init__(self):
        self.specs: list[str] = []
        self.roles: list[str] = []
        self.ep = _Ep()

    def for_model(self, kind, models, efforts):
        self.roles.append(kind)
        return self.ep, SimpleNamespace(endpoint=f"{kind}-ep", model=f"{kind}-model",
                                        name=kind, effort=None, temperature=None,
                                        max_tokens=1, multimodal=False)

    def for_endpoint(self, spec, effort=None):
        self.specs.append(spec)
        ep_name, _, model_id = spec.partition("/")
        return self.ep, SimpleNamespace(endpoint=ep_name, model=model_id, name="", effort=None,
                                        temperature=None, max_tokens=1, multimodal=False)

    def for_uncensored(self, models, efforts):
        return None                      # no uncensored role configured, the normal case

    def resolve(self, name, effort=None):
        return self.ep, SimpleNamespace(endpoint="otari", model=f"{name}-model", name=name,
                                        effort=None, temperature=None, max_tokens=1,
                                        multimodal=False, context_tokens=1)


def _ctx(registry, server=None):
    return SimpleNamespace(registry=registry, routine=SimpleNamespace(models={}, efforts={}),
                           server=server or _server(), add_usage=lambda u: None)


def test_llm_routes_a_slashed_model_through_the_endpoint_resolver():
    reg = _Registry()
    obs = do_llm({"prompt": "read this scan", "model": "omen/qwen3-vl-8b-instruct", "say": "s"},
                 _ctx(reg))
    assert obs["reply"] == "reply"
    assert reg.specs == ["omen/qwen3-vl-8b-instruct"]
    # the observation reports WHERE the work went, which is what a privacy claim rests on
    assert obs["endpoint"] == "omen" and obs["model"] == "qwen3-vl-8b-instruct"
    assert reg.roles == ["tool_call"]              # the classify subcall only


def test_llm_unknown_model_teaches_the_endpoint_route_too():
    reg = _Registry()
    obs = do_llm({"prompt": "p", "model": "nope-9", "say": "s"}, _ctx(reg))
    assert "nope-9" in obs["error"] and "opus" in obs["error"]
    assert "<endpoint>/<model id>" in obs["error"]
    assert "omen" in obs["error"]                  # the configured endpoints are named
    assert reg.specs == [] and reg.roles == []     # nothing was called


def test_a_resolution_failure_is_an_observation_not_a_raised_run():
    class _Failing(_Registry):
        def for_endpoint(self, spec, effort=None):
            raise EndpointError("endpoint 'ghost' is not configured. Configured endpoints: omen.")

    obs = do_llm({"prompt": "p", "model": "ghost/x", "say": "s"}, _ctx(_Failing()))
    assert "ghost" in obs["error"] and "reply" not in obs


def test_list_models_names_the_configured_endpoints():
    """Discovery has to carry them: an endpoint serving no catalog model is invisible in
    `models`, and on this instance that is exactly the local-inference case the route exists
    for — so the route would be unusable without this.
    """
    obs = do_list_models(_ctx(_Registry()))
    assert [e["name"] for e in obs["endpoints"]] == ["omen", "otari"]
    assert obs["endpoints"][0]["base_url"] == "http://192.168.0.201:8790/v1"
    assert "<endpoint>/<model id>" in obs["note"]
    assert not any("api_key" in e for e in obs["endpoints"])     # never a credential


def test_a_child_run_cannot_take_an_endpoint_spec():
    """A child is configured with `models["main"] = <catalog name>` (engine/childrun.py), so an
    endpoint spec would be a name it cannot find at its first turn. Refused where the reason can
    name the action that does take one.
    """
    ctx = SimpleNamespace(registry=_Registry(), routine=SimpleNamespace(models={}, efforts={}),
                          server=_server())
    mgr = SubrunManager(SimpleNamespace(ctx=ctx))
    reason = mgr._model_reason({"model": "omen/qwen3-vl-8b-instruct"})
    assert reason and "`llm`" in reason and "catalog name" in reason


# ---------------------------------------------------------------------- the env injection ----
def test_llm_env_carries_the_endpoints_and_the_role_map(monkeypatch):
    monkeypatch.setattr(secrets, "load_secrets", lambda: {"OTARI_API_KEY": "sk-live"})
    env = routine_env.llm_env_for_routine("folder-reorg", {"main": "opus"}, _server())
    meta = json.loads(env[routine_env.LLM_VAR])
    assert meta["routine"] == "folder-reorg"
    assert meta["roles"]["main"] == {"catalog": "opus", "endpoint": "otari",
                                     "model": "claude-opus-5"}
    assert meta["roles"]["uncensored"] is None          # unset reads as absent, not invented
    assert sorted(meta["endpoints"]) == ["omen", "otari"]
    assert meta["endpoints"]["omen"]["base_url"] == "http://192.168.0.201:8790/v1"
    assert meta["endpoints"]["otari"]["has_key"] is True
    assert "sk-live" not in env[routine_env.LLM_VAR]    # metadata is never the key


def test_keys_cover_the_role_endpoints_only(monkeypatch):
    """The narrowing that keeps the operator's decision intact: a key for an endpoint this
    routine's roles do not use stays behind its own `secret:<NAME>` grant row.
    """
    monkeypatch.setattr(secrets, "load_secrets",
                        lambda: {"OTARI_API_KEY": "sk-live", "OTHER_KEY": "sk-other"})
    server = _server(endpoints={"otari": _ep("otari", key_var="OTARI_API_KEY"),
                                "other": _ep("other", key_var="OTHER_KEY")})
    env = routine_env.llm_env_for_routine("r", {"main": "opus"}, server)
    assert json.loads(env[routine_env.LLM_KEYS_VAR]) == {"otari": "sk-live"}


def test_a_keyless_local_endpoint_reports_no_key_rather_than_the_placeholder(monkeypatch):
    monkeypatch.setattr(secrets, "load_secrets", dict)
    env = routine_env.llm_env_for_routine("r", {"main": "opus"}, _server(models={}))
    meta = json.loads(env[routine_env.LLM_VAR])
    assert meta["endpoints"]["omen"]["has_key"] is False
    # "none" is the placeholder bearer keyless backends ignore — it is not a key
    assert json.loads(env[routine_env.LLM_KEYS_VAR]) == {}


def test_a_role_naming_a_vanished_model_reads_as_none(monkeypatch):
    monkeypatch.setattr(secrets, "load_secrets", dict)
    env = routine_env.llm_env_for_routine("r", {"main": "deleted-model"}, _server())
    assert json.loads(env[routine_env.LLM_VAR])["roles"]["main"] is None


def test_an_unset_role_resolves_the_instance_default(monkeypatch):
    monkeypatch.setattr(secrets, "load_secrets", dict)
    env = routine_env.llm_env_for_routine("r", {}, _server())
    roles = json.loads(env[routine_env.LLM_VAR])["roles"]
    assert roles["main"]["catalog"] == "opus" and roles["tool_call"]["catalog"] == "opus"


def test_extra_secrets_carries_the_endpoint_vars(monkeypatch):
    monkeypatch.setattr(secrets, "load_secrets", dict)
    ctx = SimpleNamespace(routine=SimpleNamespace(slug="r", connections={}, machines=[],
                                                  models={"main": "opus"}),
                          granted_now=set(), grant_args={},
                          server=_server(routine_token="rt"))
    env = _extra_secrets(ctx)
    assert routine_env.LLM_VAR in env and routine_env.LLM_KEYS_VAR in env
    assert env["RSCHED_ROUTINE"] == "r"            # the sibling injections still stand


def test_llm_env_is_resolved_from_the_routine_not_from_an_argument(monkeypatch):
    monkeypatch.setattr(secrets, "load_secrets", dict)
    ctx = SimpleNamespace(routine=SimpleNamespace(slug="r", models={"main": "opus"}),
                          server=_server())
    assert json.loads(_llm_env(ctx)[routine_env.LLM_VAR])["routine"] == "r"


# ------------------------------------------------------------------ the declared-only gate ---
def test_a_declaring_util_receives_the_endpoint_vars(tmp_path):
    home = util_library(tmp_path, "llmish", DECLARING)
    env = utils_run._child_env(home, "llmish",
                               {"RSCHED_LLM": "{}", "RSCHED_LLM_KEYS": '{"otari":"sk"}'})
    assert env["RSCHED_LLM"] == "{}" and env["RSCHED_LLM_KEYS"] == '{"otari":"sk"}'


def test_an_undeclaring_util_receives_neither(tmp_path, monkeypatch):
    # the gate that makes shipping a key through this channel safe at all — and an INHERITED
    # value is scrubbed too, so the key cannot reach the wrong util by any route
    monkeypatch.setenv("RSCHED_LLM_KEYS", "leaked")
    home = util_library(tmp_path, "plainish", PLAIN)
    env = utils_run._child_env(home, "plainish",
                               {"RSCHED_LLM": "{}", "RSCHED_LLM_KEYS": '{"otari":"sk"}'})
    assert "RSCHED_LLM" not in env and "RSCHED_LLM_KEYS" not in env


def test_the_keys_var_is_credential_shaped_so_the_authoring_gate_forces_its_declaration():
    """`RSCHED_LLM_KEYS` ends in KEYS, which `utils_header._SECRET_HINT` reads as a credential:
    a util that reads it without declaring it is rejected. That is not a convention, it is what
    makes the name safe to carry a key.
    """
    from rsched.utils_header import undeclared_secrets

    body = 'import os\nos.environ["RSCHED_LLM_KEYS"]\n'
    assert "RSCHED_LLM_KEYS" in undeclared_secrets('"""u — t.\n\nsecrets: (none)\n"""\n' + body)


# --------------------------------------------------------------------------- the surface -----
def test_llm_env_vars_are_the_two_names():
    assert routine_env.llm_env_vars() == {"RSCHED_LLM", "RSCHED_LLM_KEYS"}


@pytest.mark.usefixtures("empty_store")
def test_the_setup_surface_does_not_ask_the_operator_to_add_them(tmp_path):
    """They are assembled from `endpoints:` and each endpoint's own key ladder — nothing the
    operator can put in the secrets store, so a row telling him to is a false blocker.
    """
    from rsched.readmodels.surface import routine_surface

    lib = tmp_path / "lib"
    for sub in ("permissions", "rules", "utils"):
        (lib / sub).mkdir(parents=True)
    (tmp_path / "routines").mkdir(exist_ok=True)
    server = SimpleNamespace(libraries_home=lib, permissions_home=lib / "permissions",
                            rules_home=lib / "rules", machines={},
                            routines_home=tmp_path / "routines")
    write_util(server, "llmish", secrets="RSCHED_LLM, RSCHED_LLM_KEYS")
    # the field set test_surface._cfg uses — scheduled and enabled, so the schedule join
    # reports nothing and the only rows left are the secret ones under test
    cfg = SimpleNamespace(slug="r", dir=tmp_path / "r", permissions=[], rules=[],
                          capabilities={"actions": [], "utils": ["llmish"]}, grants={},
                          fs_read_roots=[], fs_write_roots=[], machines=[], connections={},
                          enabled=True, cron="0 7 * * 1", triggers=[])
    surface = routine_surface(server, cfg)
    assert [n for n in surface["nodes"] if n["id"].startswith("secret:RSCHED_LLM")] == []
