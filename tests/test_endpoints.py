"""Adapter request construction, response parsing, error mapping — all mocked, no network."""

import json
import subprocess

import pytest

import rsched.endpoints.anthropic_api as anth_mod
import rsched.endpoints.openai_compat as oai_mod
from rsched.config import EndpointConfig
from rsched.endpoints import EndpointRegistry, make_endpoint
from rsched.endpoints.anthropic_api import AnthropicEndpoint, merge_consecutive
from rsched.endpoints.base import EndpointError, split_system, with_retries
from rsched.endpoints.claude_cli import (STRIP_VARS, TOKEN_VAR, ClaudeCliEndpoint, build_cmd,
                                         parse_result, render_prompt, resolve_token, scrub_env)
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


def test_ollama_native_structured_output(monkeypatch):
    ep = OpenAICompatEndpoint(EndpointConfig(
        name="ollama", kind="openai", base_url="http://x/v1", api_key="ollama",
        schema_mode="ollama_native", context_chars=36000, temperature=0.2))
    seen = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        seen.update(url=url, body=json)
        return FakeResponse(payload={"message": {"content": '{"say":"s","kind":"finish"}'},
                                     "prompt_eval_count": 12, "eval_count": 5})

    monkeypatch.setattr(oai_mod.httpx, "post", fake_post)
    c = ep.complete(MESSAGES, model="gemma4:latest", schema={"type": "object"}, max_tokens=999)
    assert seen["url"] == "http://x/api/chat"          # native endpoint, not /v1/chat/completions
    assert seen["body"]["format"] == {"type": "object"}  # the schema drives constrained decoding
    assert seen["body"]["options"]["num_ctx"] == 9000    # context_chars // 4, prevents truncation
    assert seen["body"]["options"]["num_predict"] == 999
    assert c.text == '{"say":"s","kind":"finish"}' and c.usage == {"in": 12, "out": 5}
    # without a schema the native path is skipped (plain /v1 generation)
    monkeypatch.setattr(oai_mod.httpx, "post",
                        lambda *a, **k: FakeResponse(payload={"choices": [{"message": {"content": "x"}}]}))
    assert ep.complete(MESSAGES, model="m").text == "x"


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


# --- claude-cli ------------------------------------------------------------------

def test_scrub_env_and_token(tmp_path, monkeypatch):
    env = scrub_env({"ANTHROPIC_API_KEY": "x", "ANTHROPIC_BASE_URL": "y", "PATH": "/bin"},
                    token="tok", max_tokens=99)
    assert all(k not in env for k in STRIP_VARS)
    assert env["PATH"] == "/bin" and env[TOKEN_VAR] == "tok"
    assert env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "99"
    credfile = tmp_path / "cred.env"
    credfile.write_text(f"# comment\n{TOKEN_VAR}='sk-file'\n")
    monkeypatch.delenv(TOKEN_VAR, raising=False)
    assert resolve_token(str(credfile)) == "sk-file"
    monkeypatch.setenv(TOKEN_VAR, "sk-env")
    assert resolve_token(str(credfile)) == "sk-env"


def test_build_cmd_isolation_flags():
    cmd = build_cmd("/bin/claude", "opus", system="sys", schema_str="{}", effort="low")
    for flag in ("-p", "--tools", "--disable-slash-commands", "--no-session-persistence",
                 "--strict-mcp-config", "--setting-sources", "--output-format",
                 "--system-prompt", "--effort", "--json-schema"):
        assert flag in cmd
    assert cmd[cmd.index("--tools") + 1] == ""
    assert cmd[cmd.index("--setting-sources") + 1] == ""


def test_render_prompt():
    single = render_prompt([{"role": "user", "content": "just this"}])
    assert single == "just this"
    multi = render_prompt([m for m in MESSAGES if m["role"] != "system"])
    assert "<<USER>>" in multi and "<<ASSISTANT>>" in multi
    assert multi.strip().endswith("no role tags.")


def test_parse_result_envelopes():
    text, parsed, usage = parse_result(json.dumps(
        {"is_error": False, "result": "hi", "usage": {"input_tokens": 1, "output_tokens": 2}}), False)
    assert text == "hi" and parsed is None and usage == {"in": 1, "out": 2}
    _, parsed, _ = parse_result(json.dumps(
        {"is_error": False, "result": "x", "structured_output": {"b": 2}}), True)
    assert parsed == {"b": 2}
    _, parsed, _ = parse_result(json.dumps({"is_error": False, "result": '{"a": 1}'}), True)
    assert parsed == {"a": 1}
    with pytest.raises(EndpointError) as exc:
        parse_result(json.dumps({"is_error": True, "result": "401 unauthorized"}), False)
    assert exc.value.auth


def test_claude_cli_complete(monkeypatch, tmp_path):
    credfile = tmp_path / "cred.env"
    credfile.write_text(f"{TOKEN_VAR}=tok\n")
    monkeypatch.delenv(TOKEN_VAR, raising=False)
    ep = ClaudeCliEndpoint(EndpointConfig(
        name="claude-cli", kind="claude-cli", credentials_env=str(credfile), context_chars=400000))
    monkeypatch.setattr("rsched.endpoints.claude_cli.find_cli", lambda: "/bin/claude")
    seen = {}

    def fake_run(cmd, input=None, capture_output=None, text=None, timeout=None, env=None, cwd=None):
        seen.update(cmd=cmd, input=input, env=env)
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(
            {"is_error": False, "result": "ok", "usage": {"input_tokens": 3, "output_tokens": 4}}), stderr="")

    monkeypatch.setattr("rsched.endpoints.claude_cli.subprocess.run", fake_run)
    c = ep.complete(MESSAGES, model="opus", effort="medium")
    assert c.text == "ok" and c.usage == {"in": 3, "out": 4}
    assert seen["env"][TOKEN_VAR] == "tok" and "<<USER>>" in seen["input"]
    assert "--system-prompt" in seen["cmd"]


# --- registry ----------------------------------------------------------------------

def test_merge_consecutive_same_role():
    merged = merge_consecutive([
        {"role": "user", "content": "a"}, {"role": "user", "content": "b"},
        {"role": "assistant", "content": "c"}, {"role": "user", "content": "d"},
    ])
    assert [m["role"] for m in merged] == ["user", "assistant", "user"]
    assert merged[0]["content"] == "a\n\nb"


def test_registry_model_resolution():
    from rsched.config import ModelRef, ServerConfig
    server = ServerConfig()
    server.endpoints = {"e1": EndpointConfig(name="e1", kind="openai", base_url="http://x")}
    server.system_model = ModelRef("e1", "sys")
    reg = EndpointRegistry(server)
    # a kind the routine didn't set falls back to system_model
    ep, ref = reg.for_model("main", {})
    assert ref.model == "sys"
    # a routine's own model wins
    ep, ref = reg.for_model("main", {"main": ModelRef("e1", "override")})
    assert ref.model == "override"
    # for_system returns the system_model
    _, sref = reg.for_system()
    assert sref.model == "sys"
    # no system_model and no routine model → error
    with pytest.raises(EndpointError):
        EndpointRegistry(ServerConfig()).for_model("main", {})
    with pytest.raises(EndpointError):
        EndpointRegistry(ServerConfig()).get("nope")


def test_make_endpoint_kinds():
    assert isinstance(make_endpoint(EndpointConfig(name="a", kind="openai", base_url="x")),
                      OpenAICompatEndpoint)
    assert isinstance(make_endpoint(EndpointConfig(name="b", kind="anthropic")), AnthropicEndpoint)
    assert isinstance(make_endpoint(EndpointConfig(name="c", kind="claude-cli")),
                      ClaudeCliEndpoint)
