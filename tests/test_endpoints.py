"""Adapter request construction, response parsing, error mapping — all mocked, no network."""

import json

import pytest

import rsched.endpoints.anthropic_api as anth_mod
import rsched.endpoints.openai_compat as oai_mod
from rsched.config import EndpointConfig
from rsched.endpoints import EndpointRegistry, make_endpoint
from rsched.endpoints.anthropic_api import AnthropicEndpoint, merge_consecutive
from rsched.endpoints.base import EndpointError, split_system, with_retries
from rsched.endpoints.openai_compat import OpenAICompatEndpoint

MESSAGES = [
    {"role": "system", "content": "be brief"},
    {"role": "user", "content": "hi"},
    {"role": "assistant", "content": "hello"},
    {"role": "user", "content": "continue"},
]


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or (json.dumps(payload) if payload else "")

    def json(self):
        return self._payload


def test_split_system():
    system, rest = split_system(MESSAGES)
    assert system == "be brief"
    assert [m["role"] for m in rest] == ["user", "assistant", "user"]


def test_with_retries_backoff(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise EndpointError("boom", retryable=True)
        return "ok"

    assert with_retries(flaky) == "ok" and len(calls) == 3
    with pytest.raises(EndpointError):
        with_retries(lambda: (_ for _ in ()).throw(EndpointError("fatal")))


# --- openai-compat ---------------------------------------------------------------

def _oai(schema_mode="json_schema"):
    return OpenAICompatEndpoint(EndpointConfig(
        name="ollama-local", kind="openai", base_url="http://x/v1",
        api_key="k", schema_mode=schema_mode, context_chars=36000, temperature=0.2))


def test_openai_request_and_usage(monkeypatch):
    seen = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        seen.update(url=url, body=json, headers=headers)
        return FakeResponse(payload={
            "choices": [{"message": {"content": '{"say":"s","kind":"finish","status":"ok","summary":"d"}'}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}})

    monkeypatch.setattr(oai_mod.httpx, "post", fake_post)
    c = _oai().complete(MESSAGES, model="m", schema={"type": "object"})
    assert seen["url"] == "http://x/v1/chat/completions"
    assert seen["body"]["response_format"]["type"] == "json_schema"
    assert seen["body"]["response_format"]["json_schema"]["strict"] is True
    assert seen["body"]["temperature"] == 0.2
    assert seen["headers"]["Authorization"] == "Bearer k"
    assert c.usage == {"in": 10, "out": 5} and "finish" in c.text


def test_openai_schema_mode_degradation(monkeypatch):
    seen = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        seen["body"] = json
        return FakeResponse(payload={"choices": [{"message": {"content": "x"}}]})

    monkeypatch.setattr(oai_mod.httpx, "post", fake_post)
    _oai("json_object").complete(MESSAGES, model="m", schema={"type": "object"})
    assert seen["body"]["response_format"] == {"type": "json_object"}
    _oai("none").complete(MESSAGES, model="m", schema={"type": "object"})
    assert "response_format" not in seen["body"]


def test_openai_key_from_env_file(monkeypatch, tmp_path):
    keyfile = tmp_path / "openrouter.env"
    keyfile.write_text("# key\nOPENROUTER_API_KEY='sk-or-test'\n")
    ep = OpenAICompatEndpoint(EndpointConfig(
        name="openrouter", kind="openai", base_url="http://x/v1",
        key_env_file=str(keyfile), key_var="OPENROUTER_API_KEY"))
    seen = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        seen["headers"] = headers
        return FakeResponse(payload={"choices": [{"message": {"content": "x"}}]})

    monkeypatch.setattr(oai_mod.httpx, "post", fake_post)
    ep.complete(MESSAGES, model="m")
    assert seen["headers"]["Authorization"] == "Bearer sk-or-test"
    missing = OpenAICompatEndpoint(EndpointConfig(
        name="openrouter", kind="openai", base_url="http://x/v1",
        key_env_file=str(tmp_path / "absent.env"), key_var="OPENROUTER_API_KEY"))
    with pytest.raises(EndpointError) as exc:
        missing.complete(MESSAGES, model="m")
    assert exc.value.auth and "absent.env" in str(exc.value)


def test_openai_response_format_degradation_on_400(monkeypatch):
    bodies = []

    def fake_post(url, json=None, headers=None, timeout=None):
        bodies.append(json)
        if "response_format" in json:
            return FakeResponse(status_code=400, text='{"error": "response_format is not supported"}')
        return FakeResponse(payload={"choices": [{"message": {"content": '{"ok":1}'}}],
                                     "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

    monkeypatch.setattr(oai_mod.httpx, "post", fake_post)
    c = _oai().complete(MESSAGES, model="m", schema={"type": "object"})
    assert len(bodies) == 2 and "response_format" not in bodies[1]
    assert c.text == '{"ok":1}'


def test_openai_reasoning_fallback_on_empty_content(monkeypatch):
    monkeypatch.setattr(oai_mod.httpx, "post", lambda *a, **k: FakeResponse(payload={
        "choices": [{"message": {"content": "",
                                 "reasoning": 'thinking… the action is {"say":"s","kind":"finish"}'}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 9}}))
    c = _oai().complete(MESSAGES, model="m")
    assert '"kind":"finish"' in c.text  # reasoning text surfaced instead of empty content


def test_openai_error_mapping(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    codes = iter([500, 500, 500])
    monkeypatch.setattr(oai_mod.httpx, "post",
                        lambda *a, **k: FakeResponse(status_code=next(codes), text="err"))
    with pytest.raises(EndpointError) as exc:
        _oai().complete(MESSAGES, model="m")
    assert exc.value.retryable
    monkeypatch.setattr(oai_mod.httpx, "post", lambda *a, **k: FakeResponse(status_code=401, text="no"))
    with pytest.raises(EndpointError) as exc:
        _oai().complete(MESSAGES, model="m")
    assert exc.value.auth


# --- anthropic -------------------------------------------------------------------

def test_anthropic_forced_tool_and_parse(monkeypatch, tmp_path):
    keyfile = tmp_path / "anthropic.env"
    keyfile.write_text('ANTHROPIC_API_KEY="sk-test"\n')
    ep = AnthropicEndpoint(EndpointConfig(
        name="anthropic", kind="anthropic", key_env_file=str(keyfile), context_chars=100))
    seen = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        seen.update(url=url, body=json, headers=headers)
        return FakeResponse(payload={
            "content": [{"type": "tool_use", "name": "action", "input": {"say": "s", "kind": "finish"}}],
            "usage": {"input_tokens": 7, "output_tokens": 3}})

    monkeypatch.setattr(anth_mod.httpx, "post", fake_post)
    c = ep.complete(MESSAGES, model="m", schema={"type": "object"})
    assert seen["headers"]["x-api-key"] == "sk-test"
    assert seen["body"]["system"] == "be brief"
    assert seen["body"]["tool_choice"] == {"type": "tool", "name": "action"}
    assert all(m["role"] != "system" for m in seen["body"]["messages"])
    assert c.parsed == {"say": "s", "kind": "finish"} and c.usage == {"in": 7, "out": 3}


def test_anthropic_missing_key(tmp_path):
    ep = AnthropicEndpoint(EndpointConfig(
        name="anthropic", kind="anthropic", key_env_file=str(tmp_path / "absent.env")))
    with pytest.raises(EndpointError) as exc:
        ep.complete(MESSAGES, model="m")
    assert exc.value.auth


# --- registry ----------------------------------------------------------------------

def test_merge_consecutive_same_role():
    merged = merge_consecutive([
        {"role": "user", "content": "a"}, {"role": "user", "content": "b"},
        {"role": "assistant", "content": "c"}, {"role": "user", "content": "d"},
    ])
    assert [m["role"] for m in merged] == ["user", "assistant", "user"]
    assert merged[0]["content"] == "a\n\nb"


def test_registry_role_resolution():
    from rsched.config import RoleRef, ServerConfig
    server = ServerConfig()
    server.endpoints = {"e1": EndpointConfig(name="e1", kind="openai", base_url="http://x")}
    server.default_roles = {"orchestrator": RoleRef("e1", "m1"), "subcall": RoleRef("e1", "m2")}
    reg = EndpointRegistry(server)
    ep, ref = reg.for_role("cheap", {})           # falls back to subcall
    assert ref.model == "m2"
    ep, ref = reg.for_role("subcall", {"subcall": RoleRef("e1", "override")})
    assert ref.model == "override"
    with pytest.raises(EndpointError):
        EndpointRegistry(ServerConfig()).get("nope")


def test_make_endpoint_kinds():
    assert isinstance(make_endpoint(EndpointConfig(name="a", kind="openai", base_url="x")),
                      OpenAICompatEndpoint)
    assert isinstance(make_endpoint(EndpointConfig(name="b", kind="anthropic")), AnthropicEndpoint)
    with pytest.raises(EndpointError):
        make_endpoint(EndpointConfig(name="c", kind="claude-cli"))  # harness kinds are gone
