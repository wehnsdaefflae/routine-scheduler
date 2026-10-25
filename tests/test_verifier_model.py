"""Is a routine's claim verifier a second model at all? (rsched/verifier_model.py) — the
`tool_call` role it runs on falls back to the system model exactly as `main` does, so two unset
roles make the "second opinion" the first one asked again."""

from helpers import server_config
from rsched.verifier_model import shared_verifier_problem


def _server(**ids: str):
    from rsched.config.modelconf import EndpointConfig, ModelConfig

    ep = {"p": EndpointConfig(name="p", kind="openai", base_url="https://p", key_var="K")}
    models = {name: ModelConfig(name=name, endpoint="p", model=mid) for name, mid in ids.items()}
    return server_config(models=models, endpoints=ep, system_model="Opus")


def test_two_unset_roles_judge_with_the_system_model_they_were_made_by():
    line = shared_verifier_problem(_server(Opus="vendor/opus"), {})
    assert line is not None
    assert "vendor/opus" in line and "both unset" in line


def test_two_catalog_names_for_one_provider_model_are_one_model():
    server = _server(Opus="vendor/opus", **{"Opus max": "vendor/opus"})
    line = shared_verifier_problem(server, {"main": "Opus max", "tool_call": "Opus"})
    assert line is not None and "main 'Opus max', tool_call 'Opus'" in line


def test_a_different_verifier_model_or_an_unknown_name_says_nothing():
    server = _server(Opus="vendor/opus", Sonnet="vendor/sonnet")
    assert shared_verifier_problem(server, {"tool_call": "Sonnet"}) is None
    assert shared_verifier_problem(server, {"main": "Gone"}) is None   # roles_problem's line
