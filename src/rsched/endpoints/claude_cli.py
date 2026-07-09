"""Claude Code CLI as a dumb completion endpoint (subscription-billed) — NEVER a harness.

Ports the mechanics of `gu claude` (~/.local/share/global-utils/utils/claude/main.py):
stripped-down `claude -p` (all tools off, no settings, no session persistence, temp cwd),
metered-auth env vars scrubbed, subscription token injected from the credentials env-file,
schema via `--json-schema`, result envelope parsed. Multi-turn conversations are
re-serialized into one prompt per call — the CLI is stateless by design.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile

from ..config import EndpointConfig
from ..paths import expand
from .base import DEFAULT_TIMEOUT, Completion, EndpointError, Message, split_system

# Vars that would re-route the child CLI to metered API-key auth (or a proxy).
STRIP_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_KEY", "ANTHROPIC_AUTH_TOKEN",
              "ANTHROPIC_BASE_URL", "ANTHROPIC_CUSTOM_HEADERS")
TOKEN_VAR = "CLAUDE_CODE_OAUTH_TOKEN"


def find_cli() -> str | None:
    override = os.environ.get("CLAUDE_CLI")
    if override:
        p = expand(override)
        return str(p) if p.exists() else None
    return shutil.which("claude")


def resolve_token(credentials_env: str, inline: str = "") -> str | None:
    """$CLAUDE_CODE_OAUTH_TOKEN, else an inline token (UI-set), else the env-file — all headless."""
    if os.environ.get(TOKEN_VAR):
        return os.environ[TOKEN_VAR]
    if inline:
        return inline
    path = expand(credentials_env)
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                if k.strip() == TOKEN_VAR:
                    return v.strip().strip('"').strip("'")
    return None


def scrub_env(base: dict, *, token: str | None, max_tokens: int | None = None) -> dict:
    env = dict(base)
    for k in STRIP_VARS:
        env.pop(k, None)
    if token:
        env[TOKEN_VAR] = token
    if max_tokens:
        env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(max_tokens)
    return env


def build_cmd(cli: str, model: str, *, system: str | None, schema_str: str | None,
              effort: str | None) -> list[str]:
    cmd = [cli, "-p", "--model", model,
           "--tools", "",                 # no built-in tools → no agentic loop
           "--disable-slash-commands",
           "--no-session-persistence",
           "--strict-mcp-config",         # with no --mcp-config: no MCP servers
           "--setting-sources", "",       # ignore user/project settings
           "--output-format", "json"]
    if system:
        cmd += ["--system-prompt", system]
    if effort:
        cmd += ["--effort", effort]
    if schema_str is not None:
        cmd += ["--json-schema", schema_str]
    return cmd


def render_prompt(rest: list[Message]) -> str:
    """Serialize the non-system turns into one prompt. The transcript framing plus a final
    cue keeps stateless calls faithful to the conversation."""
    if len(rest) == 1 and rest[0]["role"] == "user":
        return rest[0]["content"]
    lines = ["The conversation so far (you are the assistant):", ""]
    for m in rest:
        tag = "USER" if m["role"] == "user" else "ASSISTANT"
        lines.append(f"<<{tag}>>")
        lines.append(m["content"])
        lines.append("")
    lines.append("Reply now with the assistant's next message only — no role tags.")
    return "\n".join(lines)


def parse_result(stdout_text: str, want_json: bool) -> tuple[str, dict | None, dict]:
    """CLI --output-format json envelope → (text, parsed, usage)."""
    try:
        obj = json.loads(stdout_text)
    except json.JSONDecodeError as exc:
        raise EndpointError(f"claude-cli: unparseable CLI output: {stdout_text[:300]}") from exc
    if obj.get("is_error"):
        msg = str(obj.get("result") or "claude CLI reported an error")
        raise EndpointError(f"claude-cli: {msg}", auth="401" in msg)
    usage_raw = obj.get("usage") or {}
    usage = {"in": int(usage_raw.get("input_tokens") or 0),
             "out": int(usage_raw.get("output_tokens") or 0)}
    text = obj.get("result", "") or ""
    parsed = None
    if want_json:
        structured = obj.get("structured_output")
        if isinstance(structured, dict):
            parsed = structured
        else:
            try:
                cand = json.loads(text)
                parsed = cand if isinstance(cand, dict) else None
            except json.JSONDecodeError:
                parsed = None
    return text, parsed, usage


class ClaudeCliEndpoint:
    def __init__(self, cfg: EndpointConfig):
        self.name = cfg.name
        self.credentials_env = cfg.credentials_env
        self.oauth_token = cfg.api_key            # inline token pasted in Settings (optional)
        self.context_chars = cfg.context_chars
        self.supports_schema = True

    def complete(self, messages: list[Message], *, model: str, schema: dict | None = None,
                 effort: str | None = None, max_tokens: int | None = None,
                 timeout: int = DEFAULT_TIMEOUT) -> Completion:
        cli = find_cli()
        if not cli:
            raise EndpointError("claude-cli: claude CLI not found on PATH (or set $CLAUDE_CLI)")
        token = resolve_token(self.credentials_env, self.oauth_token)
        if not token:
            raise EndpointError(
                f"claude-cli: no subscription token — paste one in Settings, set {TOKEN_VAR}, or "
                f"put it in {self.credentials_env} (run `claude setup-token`); refusing API billing",
                auth=True,
            )
        system, rest = split_system(messages)
        prompt = render_prompt(rest)
        schema_str = json.dumps(schema) if schema is not None else None
        cmd = build_cmd(cli, model, system=system or None, schema_str=schema_str, effort=effort)
        env = scrub_env(os.environ, token=token, max_tokens=max_tokens)
        try:
            with tempfile.TemporaryDirectory(prefix="rsched-claude-") as cwd:
                r = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                                   timeout=timeout, env=env, cwd=cwd)
        except subprocess.TimeoutExpired as exc:
            raise EndpointError(f"claude-cli: call timed out after {timeout}s", retryable=True) from exc
        if r.returncode != 0 and not r.stdout.strip():
            raise EndpointError(
                f"claude-cli: exited {r.returncode}: {r.stderr.strip()[:300] or '(no stderr)'}",
                retryable=True,
            )
        text, parsed, usage = parse_result(r.stdout, want_json=schema is not None)
        return Completion(text=text, parsed=parsed, usage=usage)
