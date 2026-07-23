"""Adapter request construction, response parsing, error mapping — all mocked, no network."""

import json
import subprocess
from pathlib import Path

import pytest

import rsched.endpoints.anthropic_api as anth_mod
import rsched.endpoints.openai_compat as oai_mod
from rsched.config import EndpointConfig
from rsched.endpoints import EndpointRegistry, make_endpoint
from rsched.endpoints.anthropic_api import AnthropicEndpoint, merge_consecutive
from rsched.endpoints.base import EndpointError, split_system, with_retries
from rsched.endpoints.claude_cli import (
    STRIP_VARS,
    TOKEN_VAR,
    ClaudeCliEndpoint,
    build_cmd,
    parse_result,
    render_prompt,
    resolve_token,
    scrub_env,
)
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


class BrokenJSONResponse(FakeResponse):
    """A 200 whose body is not JSON (truncated stream, proxy garbage)."""

    def json(self):
        raise json.JSONDecodeError("Expecting value", self.text, 0)


def test_split_system():
    system, rest = split_system(MESSAGES)
    assert system == "be brief"
    assert [m["role"] for m in rest] == ["user", "assistant", "user"]


def test_with_retries_backoff(monkeypatch):
    monkeypatch.delenv("RSCHED_RETRY_BASE_DELAY", raising=False)  # pin the PRODUCTION delays
    sleeps = []
    monkeypatch.setattr("time.sleep", sleeps.append)
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise EndpointError("boom", retryable=True)
        return "ok"

    assert with_retries(flaky) == "ok" and len(calls) == 3
    assert sleeps == [1, 2]                               # exponential backoff preserved
    with pytest.raises(EndpointError):
        with_retries(lambda: (_ for _ in ()).throw(EndpointError("fatal")))
    assert sleeps == [1, 2]                               # non-retryable: no extra attempts


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


def test_openai_cached_tokens_kept_out_of_in(monkeypatch):
    """Implicit prompt caching (OpenAI/OpenRouter style): cached_tokens arrives as a subset
    of prompt_tokens — the adapter subtracts it so "in" is fresh input only. This pins the
    CROSS-ADAPTER invariant (cached_in kept OUT of "in", token budgets keep their meaning);
    the anthropic/claude-cli cache tests pin the same shape for the other two adapters."""
    monkeypatch.setattr(oai_mod.httpx, "post", lambda *a, **k: FakeResponse(payload={
        "choices": [{"message": {"content": "x"}}],
        "usage": {"prompt_tokens": 1000, "completion_tokens": 5,
                  "prompt_tokens_details": {"cached_tokens": 896}}}))
    c = _oai().complete(MESSAGES, model="m")
    assert c.usage == {"in": 104, "out": 5, "cached_in": 896}
    assert c.usage["in"] + c.usage["cached_in"] == 1000   # fresh + cached = the full prompt


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
    monkeypatch.setattr("rsched.secrets.load_secrets", dict)   # hermetic: ignore the machine's real secrets store
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


def test_openai_response_format_degradation_on_503(monkeypatch):
    """A backend that can't do schema-constrained decoding may reject `response_format`
    with a 503 whose body never names the field (NanoGPT community models). The adapter
    speculatively retries once without it and adopts the result when that clears."""
    bodies = []

    def fake_post(url, json=None, headers=None, timeout=None):
        bodies.append(json)
        if "response_format" in json:
            return FakeResponse(status_code=503,
                                text='{"error":{"message":"Service temporarily unavailable.",'
                                     '"type":"service_unavailable"}}')
        return FakeResponse(payload={"choices": [{"message": {"content": '{"ok":1}'}}],
                                     "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

    monkeypatch.setattr(oai_mod.httpx, "post", fake_post)
    c = _oai().complete(MESSAGES, model="m", schema={"type": "object"})
    assert len(bodies) == 2 and "response_format" not in bodies[1]
    assert c.text == '{"ok":1}'


def test_openai_persistent_503_still_retryable(monkeypatch):
    """A genuine outage (503 even without response_format) must not be masked by the
    speculative degrade: it still surfaces retryable, after probing once without the schema
    on each of the three attempts."""
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(json)
        return FakeResponse(status_code=503, text="service_unavailable")

    monkeypatch.setattr(oai_mod.httpx, "post", fake_post)
    with pytest.raises(EndpointError) as exc:
        _oai().complete(MESSAGES, model="m", schema={"type": "object"})
    assert exc.value.retryable
    assert len(calls) == 6           # 3 attempts × (with response_format, then without)


def test_openai_reasoning_fallback_on_empty_content(monkeypatch):
    monkeypatch.setattr(oai_mod.httpx, "post", lambda *a, **k: FakeResponse(payload={
        "choices": [{"message": {"content": "",
                                 "reasoning": 'thinking… the action is {"say":"s","kind":"finish"}'}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 9}}))
    c = _oai().complete(MESSAGES, model="m")
    assert '"kind":"finish"' in c.text  # reasoning text surfaced instead of empty content


def test_openai_reasoning_content_fallback_on_empty_content(monkeypatch):
    """DeepSeek/vLLM/SGLang-style APIs put the scratchpad in `reasoning_content` (not
    `reasoning`) and can leave `content` empty — the answer JSON must still surface."""
    monkeypatch.setattr(oai_mod.httpx, "post", lambda *a, **k: FakeResponse(payload={
        "choices": [{"message": {"content": "",
                                 "reasoning_content": 'hm {"say":"s","kind":"finish"}'}}],
        "usage": {}}))
    c = _oai().complete(MESSAGES, model="m")
    assert '"kind":"finish"' in c.text


def test_openai_think_preamble_stripped(monkeypatch):
    """Hybrid-thinking models (qwen3/GLM via NanoGPT) inline `<think>…</think>` before the
    answer in `content`; the adapter drops closed think blocks so the action JSON parses.
    An UNCLOSED think block (cap hit mid-thought) stays untouched — visible, not vanished."""
    monkeypatch.setattr(oai_mod.httpx, "post", lambda *a, **k: FakeResponse(payload={
        "choices": [{"message": {
            "content": '<think>let me plan…\n{maybe}</think>\n{"say":"s","kind":"finish"}'}}],
        "usage": {}}))
    c = _oai().complete(MESSAGES, model="m")
    assert c.text == '{"say":"s","kind":"finish"}'
    monkeypatch.setattr(oai_mod.httpx, "post", lambda *a, **k: FakeResponse(payload={
        "choices": [{"message": {"content": "<think>ran out of budget mid-thought"}}],
        "usage": {}}))
    c2 = _oai().complete(MESSAGES, model="m")
    assert "<think>" in c2.text  # unclosed block: kept as-is for the empty/retry path


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


def test_openai_malformed_json_on_200_is_retryable(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(1)
        return BrokenJSONResponse(text="<html>502 from a proxy</html>")

    monkeypatch.setattr(oai_mod.httpx, "post", fake_post)
    with pytest.raises(EndpointError) as exc:
        _oai().complete(MESSAGES, model="m")
    assert exc.value.retryable and len(calls) == 3        # went through the retry wrapper
    assert "unparseable JSON" in str(exc.value) and "502 from a proxy" in str(exc.value)


def test_ollama_native_malformed_json_on_200_is_retryable(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    ep = OpenAICompatEndpoint(EndpointConfig(
        name="ollama", kind="openai", base_url="http://x/v1", api_key="ollama",
        schema_mode="ollama_native", context_chars=36000))
    monkeypatch.setattr(oai_mod.httpx, "post", lambda *a, **k: BrokenJSONResponse(text="not json"))
    with pytest.raises(EndpointError) as exc:
        ep.complete(MESSAGES, model="m", schema={"type": "object"})
    assert exc.value.retryable


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
    # prompt caching: system + tools carry static breakpoints, the last message a moving one
    assert seen["body"]["system"] == [{"type": "text", "text": "be brief",
                                       "cache_control": {"type": "ephemeral"}}]
    assert seen["body"]["tools"][0]["cache_control"] == {"type": "ephemeral"}
    assert seen["body"]["tool_choice"] == {"type": "tool", "name": "action"}
    assert all(m["role"] != "system" for m in seen["body"]["messages"])
    last = seen["body"]["messages"][-1]
    assert last["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert last["content"][0]["text"] == "continue"
    assert c.parsed == {"say": "s", "kind": "finish"} and c.usage == {"in": 7, "out": 3}


def test_anthropic_missing_key(tmp_path):
    ep = AnthropicEndpoint(EndpointConfig(
        name="anthropic", kind="anthropic", key_env_file=str(tmp_path / "absent.env")))
    with pytest.raises(EndpointError) as exc:
        ep.complete(MESSAGES, model="m")
    assert exc.value.auth


def _anth():
    return AnthropicEndpoint(EndpointConfig(
        name="anthropic", kind="anthropic", api_key="sk-test", context_chars=800000))


_ANTH_OK = {"content": [{"type": "tool_use", "name": "action", "input": {"say": "s", "kind": "finish"}}],
            "usage": {"input_tokens": 1, "output_tokens": 1}}


@pytest.mark.parametrize(("status", "attr", "attempts"), [
    (401, "auth", 1),        # auth errors fail fast
    (429, "retryable", 3),   # rate limit → retried
    (529, "retryable", 3),   # overloaded → retried
    (500, "retryable", 3),   # server error → retried
])
def test_anthropic_http_error_mapping(monkeypatch, status, attr, attempts):
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(1)
        return FakeResponse(status_code=status, text="err body")

    monkeypatch.setattr(anth_mod.httpx, "post", fake_post)
    with pytest.raises(EndpointError) as exc:
        _anth().complete(MESSAGES, model="m")
    assert getattr(exc.value, attr) and len(calls) == attempts
    assert f"HTTP {status}" in str(exc.value) and "err body" in str(exc.value)


def test_anthropic_malformed_json_on_200_is_retryable(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(1)
        return BrokenJSONResponse(text="upstream hiccup")

    monkeypatch.setattr(anth_mod.httpx, "post", fake_post)
    with pytest.raises(EndpointError) as exc:
        _anth().complete(MESSAGES, model="m")
    assert exc.value.retryable and len(calls) == 3        # went through the retry wrapper
    assert "unparseable JSON" in str(exc.value) and "upstream hiccup" in str(exc.value)


def test_anthropic_effort_wiring(monkeypatch):
    seen = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        seen["body"] = json
        return FakeResponse(payload=_ANTH_OK)

    monkeypatch.setattr(anth_mod.httpx, "post", fake_post)
    _anth().complete(MESSAGES, model="m", effort="xhigh")
    assert seen["body"]["output_config"] == {"effort": "xhigh"}
    _anth().complete(MESSAGES, model="m")                 # no effort → no output_config
    assert "output_config" not in seen["body"]


def test_anthropic_cache_usage_captured(monkeypatch):
    """input_tokens excludes cache traffic on this API — the adapter surfaces it as
    cached_in/cache_write, never folded into "in" (the cross-adapter invariant)."""
    monkeypatch.setattr(anth_mod.httpx, "post", lambda *a, **k: FakeResponse(payload={
        "content": [{"type": "text", "text": "hi"}],
        "usage": {"input_tokens": 12, "output_tokens": 3,
                  "cache_read_input_tokens": 9000, "cache_creation_input_tokens": 400}}))
    c = _anth().complete(MESSAGES, model="m")
    assert c.usage == {"in": 12, "out": 3, "cached_in": 9000, "cache_write": 400}


def test_anthropic_cache_control_degradation_on_400(monkeypatch):
    """A gateway that rejects cache_control gets one degraded retry without the markers
    (string-form system restored) — caching is an optimization, never a hard dependency."""
    bodies = []

    def fake_post(url, json=None, headers=None, timeout=None):
        bodies.append(json)
        if "cache_control" in __import__("json").dumps(json):
            return FakeResponse(status_code=400,
                                text='{"error": {"message": "cache_control: unknown field"}}')
        return FakeResponse(payload=_ANTH_OK)

    monkeypatch.setattr(anth_mod.httpx, "post", fake_post)
    c = _anth().complete(MESSAGES, model="m", schema={"type": "object"})
    assert len(bodies) == 2
    degraded = __import__("json").dumps(bodies[1])
    assert "cache_control" not in degraded
    assert bodies[1]["system"] == "be brief"          # block-form collapsed back to a string
    assert c.parsed == {"say": "s", "kind": "finish"}


def test_anthropic_effort_degradation_on_400(monkeypatch):
    bodies = []

    def fake_post(url, json=None, headers=None, timeout=None):
        bodies.append(json)
        if "output_config" in json:
            return FakeResponse(status_code=400,
                                text='{"error": {"message": "output_config.effort: not supported"}}')
        return FakeResponse(payload=_ANTH_OK)

    monkeypatch.setattr(anth_mod.httpx, "post", fake_post)
    c = _anth().complete(MESSAGES, model="m", effort="high")
    assert len(bodies) == 2 and "output_config" not in bodies[1]
    assert c.parsed == {"say": "s", "kind": "finish"}
    # a 400 unrelated to effort is NOT degraded — it surfaces as-is
    bodies.clear()
    monkeypatch.setattr(anth_mod.httpx, "post",
                        lambda *a, **k: FakeResponse(status_code=400, text="max_tokens too large"))
    with pytest.raises(EndpointError) as exc:
        _anth().complete(MESSAGES, model="m", effort="high")
    assert not exc.value.retryable and "max_tokens too large" in str(exc.value)


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
    text, parsed, usage, _stop = parse_result(json.dumps(
        {"is_error": False, "result": "hi", "usage": {"input_tokens": 1, "output_tokens": 2}}), False)
    assert text == "hi" and parsed is None and usage == {"in": 1, "out": 2}
    _, parsed, _, _ = parse_result(json.dumps(
        {"is_error": False, "result": "x", "structured_output": {"b": 2}}), True)
    assert parsed == {"b": 2}
    _, parsed, _, _ = parse_result(json.dumps({"is_error": False, "result": '{"a": 1}'}), True)
    assert parsed == {"a": 1}
    with pytest.raises(EndpointError) as exc:
        parse_result(json.dumps({"is_error": True, "result": "401 unauthorized"}), False)
    assert exc.value.auth


def _cli_endpoint(monkeypatch, tmp_path):
    """A ClaudeCliEndpoint wired to a token file, a fake CLI path, and no real secrets."""
    credfile = tmp_path / "cred.env"
    credfile.write_text(f"{TOKEN_VAR}=tok\n")
    monkeypatch.delenv(TOKEN_VAR, raising=False)
    monkeypatch.setattr("rsched.endpoints.claude_cli.find_cli", lambda: "/bin/claude")
    monkeypatch.setattr("rsched.secrets.load_secrets", dict)
    return ClaudeCliEndpoint(EndpointConfig(
        name="claude-cli", kind="claude-cli", credentials_env=str(credfile), context_chars=400000))


def test_claude_cli_complete(monkeypatch, tmp_path):
    credfile = tmp_path / "cred.env"
    credfile.write_text(f"{TOKEN_VAR}=tok\n")
    monkeypatch.delenv(TOKEN_VAR, raising=False)
    ep = ClaudeCliEndpoint(EndpointConfig(
        name="claude-cli", kind="claude-cli", credentials_env=str(credfile), context_chars=400000))
    monkeypatch.setattr("rsched.endpoints.claude_cli.find_cli", lambda: "/bin/claude")
    seen = {}

    def fake_run(cmd, input=None, capture_output=None, text=None, timeout=None,
                 env=None, cwd=None, check=False):
        seen.update(cmd=cmd, input=input, env=env)
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(
            {"is_error": False, "result": "ok", "usage": {"input_tokens": 3, "output_tokens": 4}}), stderr="")

    monkeypatch.setattr("rsched.endpoints.claude_cli.subprocess.run", fake_run)
    monkeypatch.setattr("rsched.secrets.load_secrets", dict)   # hermetic: ignore the machine's real secrets store
    c = ep.complete(MESSAGES, model="opus", effort="medium")
    assert c.text == "ok" and c.usage == {"in": 3, "out": 4}
    assert seen["env"][TOKEN_VAR] == "tok" and "<<USER>>" in seen["input"]
    assert "--system-prompt" in seen["cmd"]


def test_claude_cli_nonzero_exit_empty_stdout_is_retryable(monkeypatch, tmp_path):
    ep = _cli_endpoint(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "rsched.endpoints.claude_cli.subprocess.run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, stdout="", stderr="ECONNRESET talking home"))
    with pytest.raises(EndpointError) as exc:
        ep.complete(MESSAGES, model="opus")
    assert exc.value.retryable
    assert "exited 1" in str(exc.value) and "ECONNRESET" in str(exc.value)


def test_claude_cli_unparseable_stdout_retried_then_raised(monkeypatch, tmp_path):
    """Garbled CLI stdout is a transport fault (a truncated envelope), mirroring
    json_or_raise for HTTP bodies: with_retries re-invokes the CLI, then the last
    error propagates retryable."""
    ep = _cli_endpoint(monkeypatch, tmp_path)
    calls = []
    monkeypatch.setattr(
        "rsched.endpoints.claude_cli.subprocess.run",
        lambda cmd, **kw: (calls.append(1), subprocess.CompletedProcess(
            cmd, 0, stdout="plain text, no envelope", stderr=""))[1])
    with pytest.raises(EndpointError) as exc:
        ep.complete(MESSAGES, model="opus")
    assert exc.value.retryable
    assert len(calls) == 3   # the with_retries contract: 3 tries total
    assert "unparseable CLI output" in str(exc.value) and "plain text" in str(exc.value)


def test_claude_cli_transient_failure_recovered_by_retry(monkeypatch, tmp_path):
    """One bad invocation (nonzero exit, empty stdout) must not fail the turn — the
    retry wrapper runs the CLI again and the second attempt's reply is returned."""
    ep = _cli_endpoint(monkeypatch, tmp_path)
    calls = []

    def fake_run(cmd, **kw):
        calls.append(1)
        if len(calls) == 1:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="blip")
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(
            {"is_error": False, "result": "recovered",
             "usage": {"input_tokens": 1, "output_tokens": 1}}), stderr="")

    monkeypatch.setattr("rsched.endpoints.claude_cli.subprocess.run", fake_run)
    c = ep.complete(MESSAGES, model="opus")
    assert c.text == "recovered" and len(calls) == 2


def test_claude_cli_timeout_is_retryable(monkeypatch, tmp_path):
    ep = _cli_endpoint(monkeypatch, tmp_path)

    def fake_run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout", 0))

    monkeypatch.setattr("rsched.endpoints.claude_cli.subprocess.run", fake_run)
    with pytest.raises(EndpointError) as exc:
        ep.complete(MESSAGES, model="opus", timeout=7)
    assert exc.value.retryable and "timed out after 7s" in str(exc.value)


def test_claude_cli_cache_usage_captured():
    """Same cross-adapter invariant as the API adapters: cache traffic rides
    cached_in/cache_write, kept out of "in"."""
    _, _, usage, _ = parse_result(json.dumps(
        {"is_error": False, "result": "hi",
         "usage": {"input_tokens": 4, "output_tokens": 2,
                   "cache_read_input_tokens": 30000, "cache_creation_input_tokens": 1200}}), False)
    assert usage == {"in": 4, "out": 2, "cached_in": 30000, "cache_write": 1200}


def _ok_cli_result(cmd):
    return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(
        {"is_error": False, "result": "ok",
         "usage": {"input_tokens": 3, "output_tokens": 4}}), stderr="")


def test_claude_cli_session_opens_then_resumes_with_delta(monkeypatch, tmp_path):
    """With a session key: first call opens a CLI session (--session-id, full conversation
    rendered), the next call resumes it (--resume) sending ONLY the new user content —
    the caching-shaped path that stops re-processing the whole transcript every turn."""
    ep = _cli_endpoint(monkeypatch, tmp_path)
    monkeypatch.setattr("rsched.endpoints.claude_cli.expand",
                        lambda p: tmp_path / "cache" if str(p).startswith("~") else Path(p))
    calls = []

    def fake_run(cmd, input=None, capture_output=None, text=None, timeout=None,
                 env=None, cwd=None, check=False):
        calls.append({"cmd": list(cmd), "input": input, "cwd": cwd})
        return _ok_cli_result(cmd)

    monkeypatch.setattr("rsched.endpoints.claude_cli.subprocess.run", fake_run)
    ep.complete(MESSAGES, model="opus", session="run-A")
    first = calls[0]
    assert "--session-id" in first["cmd"] and "--no-session-persistence" not in first["cmd"]
    assert "<<USER>>" in first["input"]                    # full conversation seeds the session
    sid = first["cmd"][first["cmd"].index("--session-id") + 1]

    grown = [*MESSAGES, {"role": "assistant", "content": '{"kind":"util"}'},
             {"role": "user", "content": "OBSERVATION (util x, exit 0): fine"}]
    ep.complete(grown, model="opus", session="run-A")
    second = calls[1]
    assert second["cmd"][second["cmd"].index("--resume") + 1] == sid
    assert second["input"] == "OBSERVATION (util x, exit 0): fine"   # the delta, nothing else
    assert second["cwd"] == first["cwd"]                   # same cwd — the CLI's session key

    # a rewritten prefix (compaction) cannot resume — a FRESH session is seeded instead
    compacted = [MESSAGES[0], {"role": "user", "content": "CONTEXT COMPACTED — pointer"},
                 {"role": "user", "content": "next observation"}]
    ep.complete(compacted, model="opus", session="run-A")
    third = calls[2]
    assert "--resume" not in third["cmd"] and "--session-id" in third["cmd"]
    assert third["cmd"][third["cmd"].index("--session-id") + 1] != sid


def test_claude_cli_resume_failure_reseeds_fresh_session(monkeypatch, tmp_path):
    """A broken/expired CLI session must never break the run: the resume attempt's failure
    drops the state and the call is retried as a fresh session with the full conversation."""
    ep = _cli_endpoint(monkeypatch, tmp_path)
    monkeypatch.setattr("rsched.endpoints.claude_cli.expand",
                        lambda p: tmp_path / "cache" if str(p).startswith("~") else Path(p))
    calls = []

    def fake_run(cmd, input=None, capture_output=None, text=None, timeout=None,
                 env=None, cwd=None, check=False):
        calls.append(list(cmd))
        if "--resume" in cmd:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="No conversation found")
        return _ok_cli_result(cmd)

    monkeypatch.setattr("rsched.endpoints.claude_cli.subprocess.run", fake_run)
    ep.complete(MESSAGES, model="opus", session="run-B")
    grown = [*MESSAGES, {"role": "assistant", "content": "a"}, {"role": "user", "content": "o"}]
    c = ep.complete(grown, model="opus", session="run-B")
    assert c.text == "ok"
    assert ["--resume" in c_ for c_ in calls].count(True) == 1     # one failed resume …
    assert ["--session-id" in c_ for c_ in calls].count(True) == 2  # … then a fresh seed


def test_claude_cli_no_session_stays_stateless(monkeypatch, tmp_path):
    ep = _cli_endpoint(monkeypatch, tmp_path)
    seen = {}

    def fake_run(cmd, input=None, capture_output=None, text=None, timeout=None,
                 env=None, cwd=None, check=False):
        seen.update(cmd=list(cmd))
        return _ok_cli_result(cmd)

    monkeypatch.setattr("rsched.endpoints.claude_cli.subprocess.run", fake_run)
    ep.complete(MESSAGES, model="opus")
    assert "--no-session-persistence" in seen["cmd"]
    assert "--session-id" not in seen["cmd"] and "--resume" not in seen["cmd"]


# --- registry ----------------------------------------------------------------------

def test_merge_consecutive_same_role():
    merged = merge_consecutive([
        {"role": "user", "content": "a"}, {"role": "user", "content": "b"},
        {"role": "assistant", "content": "c"}, {"role": "user", "content": "d"},
    ])
    assert [m["role"] for m in merged] == ["user", "assistant", "user"]
    assert merged[0]["content"] == "a\n\nb"


def test_registry_model_resolution():
    from rsched.config import ModelConfig, ServerConfig
    server = ServerConfig()
    server.endpoints = {"e1": EndpointConfig(name="e1", kind="openai", base_url="http://x",
                                             context_chars=250_000)}
    server.models = {
        "sys": ModelConfig(name="sys", endpoint="e1", model="sys-id"),
        "override": ModelConfig(name="override", endpoint="e1", model="override-id",
                                multimodal=True, context_chars=500_000),
    }
    server.system_model = "sys"
    reg = EndpointRegistry(server)
    # a role the routine didn't set falls back to system_model (by name)
    _, ref = reg.for_model("main", {})
    assert ref.model == "sys-id" and ref.name == "sys"
    # resolved attrs inherit the endpoint defaults (openai → text-only; endpoint's window)
    assert ref.multimodal is False and ref.context_chars == 250_000
    # a routine's own model (by catalog name) wins, carrying its per-model attrs
    _, ref = reg.for_model("main", {"main": "override"})
    assert ref.model == "override-id" and ref.multimodal is True and ref.context_chars == 500_000
    # for_system returns the system_model
    _, sref = reg.for_system()
    assert sref.model == "sys-id"
    # no system_model + no routine model → error; unknown catalog name → error; unknown endpoint → error
    with pytest.raises(EndpointError):
        EndpointRegistry(ServerConfig()).for_model("main", {})
    with pytest.raises(EndpointError):
        reg.resolve("ghost")
    with pytest.raises(EndpointError):
        reg.get("nope")


def test_registry_for_uncensored():
    from rsched.config import ModelConfig, ServerConfig
    server = ServerConfig()
    server.endpoints = {"e1": EndpointConfig(name="e1", kind="openai", base_url="http://x")}
    server.models = {
        "sys": ModelConfig(name="sys", endpoint="e1", model="sys-id"),
        "abliterated": ModelConfig(name="abliterated", endpoint="e1", model="ablit-id"),
    }
    server.system_model = "sys"
    reg = EndpointRegistry(server)
    # unset uncensored role → None (NO system_model fallback: referral off)
    assert reg.for_uncensored({}) is None
    assert reg.for_uncensored({"main": "sys"}) is None
    # explicitly named → resolves the endpoint + ref
    _, ref = reg.for_uncensored({"uncensored": "abliterated"})
    assert ref.model == "ablit-id" and ref.endpoint == "e1"


def test_make_endpoint_kinds():
    assert isinstance(make_endpoint(EndpointConfig(name="a", kind="openai", base_url="x")),
                      OpenAICompatEndpoint)
    assert isinstance(make_endpoint(EndpointConfig(name="b", kind="anthropic")), AnthropicEndpoint)
    assert isinstance(make_endpoint(EndpointConfig(name="c", kind="claude-cli")),
                      ClaudeCliEndpoint)


def test_extra_body_merged_and_provider_captured(monkeypatch):
    """extra_body (aggregator routing) lands in every request body; the serving provider
    reported by the aggregator is captured on the Completion."""
    from rsched.config import EndpointConfig
    from rsched.endpoints.openai_compat import OpenAICompatEndpoint
    cfg = EndpointConfig(name="orx", kind="openai", base_url="http://x/v1", api_key="k",
                         extra_body={"provider": {"ignore": ["StreamLake"]}})
    ep = OpenAICompatEndpoint(cfg)
    seen = {}

    class R:
        status_code = 200
        text = "{}"
        def json(self):
            return {"choices": [{"message": {"content": '{"ok": true}'}}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 2},
                    "provider": "Fireworks"}

    def fake_post(body, headers, timeout):
        seen.update(body)
        return R()
    monkeypatch.setattr(ep, "_post", fake_post)
    c = ep.complete([{"role": "user", "content": "hi"}], model="m")
    assert seen["provider"] == {"ignore": ["StreamLake"]}
    assert c.provider == "Fireworks"


def test_api_key_source_ladder(tmp_path, monkeypatch):
    """The Settings credential-source labels mirror resolve_api_key's precedence exactly —
    inline wins (flagged when it shadows a set secret) → secret → env file → none."""
    from rsched.endpoints.base import api_key_source

    monkeypatch.setattr("rsched.secrets.load_secrets", lambda: {"K": "sk-stored"})
    assert api_key_source(api_key="sk-inline", key_var="K", key_env_file="") == {
        "source": "inline", "var": "K", "shadowed_secret": True}
    assert api_key_source(api_key="", key_var="K", key_env_file="") == {
        "source": "secret", "var": "K"}
    monkeypatch.setattr("rsched.secrets.load_secrets", dict)
    envf = tmp_path / "creds.env"
    envf.write_text("K=sk-file\n", encoding="utf-8")
    assert api_key_source(api_key="", key_var="K", key_env_file=str(envf)) == {
        "source": "env_file", "var": "K", "env_file": str(envf)}
    # env file CONFIGURED but the key is not in it: the resolver RAISES here, so the
    # label must flag the miss instead of reporting a benign keyless "none"
    assert api_key_source(api_key="", key_var="K",
                          key_env_file=str(tmp_path / "missing.env")) == {
        "source": "none", "var": "K", "env_file": str(tmp_path / "missing.env"),
        "env_file_miss": True}
    assert api_key_source(api_key="sk-i", key_var="", key_env_file="") == {
        "source": "inline", "var": None, "shadowed_secret": False}


def test_token_source_ladder(tmp_path, monkeypatch):
    """claude-cli's analog: process env → inline (shadow-flagged) → secret → env file."""
    from rsched.endpoints.claude_cli import token_source

    monkeypatch.delenv(TOKEN_VAR, raising=False)
    monkeypatch.setattr("rsched.secrets.load_secrets", dict)
    assert token_source(str(tmp_path / "missing.env"), "") == {
        "source": "none", "var": TOKEN_VAR}
    envf = tmp_path / "oauth.env"
    envf.write_text(f"{TOKEN_VAR}=tok-file\n", encoding="utf-8")
    assert token_source(str(envf), "") == {
        "source": "env_file", "var": TOKEN_VAR, "env_file": str(envf)}
    monkeypatch.setattr("rsched.secrets.load_secrets", lambda: {TOKEN_VAR: "tok-stored"})
    assert token_source(str(envf), "") == {"source": "secret", "var": TOKEN_VAR}
    assert token_source(str(envf), "tok-inline") == {
        "source": "inline", "var": TOKEN_VAR, "shadowed_secret": True}
    monkeypatch.setenv(TOKEN_VAR, "tok-env")
    assert token_source(str(envf), "tok-inline") == {
        "source": "process_env", "var": TOKEN_VAR}
