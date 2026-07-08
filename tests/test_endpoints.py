"""Adapter request construction, response parsing, error mapping — all mocked, no network."""

import json
import subprocess

import pytest

import rsched.endpoints.anthropic_api as anth_mod
import rsched.endpoints.openai_compat as oai_mod
from rsched.config import EndpointConfig
from rsched.endpoints import EndpointRegistry, make_endpoint
from rsched.endpoints.anthropic_api import AnthropicEndpoint
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
    assert isinstance(make_endpoint(EndpointConfig(name="c", kind="claude-cli")), ClaudeCliEndpoint)
