"""Claude Code CLI as a dumb completion endpoint (subscription-billed) — NEVER a harness.

Ports the mechanics of `gu claude` (~/.local/share/global-utils/utils/claude/main.py):
stripped-down `claude -p` (all tools off, no settings), metered-auth env vars scrubbed,
subscription token injected from the credentials env-file, schema via `--json-schema`,
result envelope parsed.

Two modes:
- One-shot (no `session`): temp cwd, --no-session-persistence, the whole conversation
  re-serialized into one prompt — the original stateless behavior.
- Session (the engine passes a stable `session` key per run): a CLI session is kept per
  key (--session-id first, --resume after) and each turn sends ONLY the new user
  messages. This is the caching-shaped path: the single-growing-prompt serialization can
  never prefix-match on the server, so every turn used to re-process the entire
  transcript (quota-charged); with a real session the prior turns are proper messages
  and Anthropic's prompt cache serves them at cache-read weight. Any prefix change
  (compaction, model switch mid-run) or resume failure falls back to a fresh session
  seeded with the full conversation — semantics never depend on session state.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import uuid

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
              effort: str | None, session_id: str | None = None,
              resume: str | None = None) -> list[str]:
    cmd = [cli, "-p", "--model", model,
           "--tools", "",                 # no built-in tools → no agentic loop
           "--disable-slash-commands",
           "--strict-mcp-config",         # with no --mcp-config: no MCP servers
           "--setting-sources", "",       # ignore user/project settings
           "--output-format", "json"]
    if resume:
        cmd += ["--resume", resume]       # continue the per-run CLI session (delta turns)
    elif session_id:
        cmd += ["--session-id", session_id]   # open the per-run CLI session
    else:
        cmd += ["--no-session-persistence"]   # one-shot mode: leave nothing behind
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
    # input_tokens excludes cache traffic on this API — without these two fields a run
    # shows "in=4" while the real prompt was served from (and written into) the cache.
    if usage_raw.get("cache_read_input_tokens"):
        usage["cached_in"] = int(usage_raw["cache_read_input_tokens"])
    if usage_raw.get("cache_creation_input_tokens"):
        usage["cache_write"] = int(usage_raw["cache_creation_input_tokens"])
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


def _msg_hashes(messages: list[Message]) -> list[str]:
    return [hashlib.sha1(f"{m['role']}\x00{m['content']}".encode("utf-8")).hexdigest()
            for m in messages]


class ClaudeCliEndpoint:
    """`claude -p` fully stripped (tools off, no MCP/settings, our system prompt replacing
    its own) — a SUBSCRIPTION-billed completion function. Metered-auth env vars are
    scrubbed so it can never silently fall back to API billing. With a `session` key it
    keeps one CLI session per run and sends per-turn deltas (see the module docstring)."""

    def __init__(self, cfg: EndpointConfig):
        self.name = cfg.name
        self.credentials_env = cfg.credentials_env
        self.oauth_token = cfg.api_key            # inline token pasted in Settings (optional)
        self.context_chars = cfg.context_chars
        # session key → {"sid": CLI session id, "hashes": per-message sha1 of what the
        # session has already seen, "cwd": the stable dir the CLI keys its store to}
        self._sessions: dict[str, dict] = {}
        self._lock = threading.Lock()

    def complete(self, messages: list[Message], *, model: str, schema: dict | None = None,
                 effort: str | None = None, max_tokens: int | None = None,
                 timeout: int = DEFAULT_TIMEOUT, session: str | None = None) -> Completion:
        cli = find_cli()
        if not cli:
            raise EndpointError("claude-cli: claude CLI not found on PATH (or set $CLAUDE_CLI)")
        from ..secrets import load_secrets
        token = resolve_token(self.credentials_env, self.oauth_token or load_secrets().get(TOKEN_VAR, ""))
        if not token:
            raise EndpointError(
                f"claude-cli: no subscription token — paste one in Settings, set {TOKEN_VAR}, or "
                f"put it in {self.credentials_env} (run `claude setup-token`); refusing API billing",
                auth=True,
            )
        system, rest = split_system(messages)
        schema_str = json.dumps(schema) if schema is not None else None
        env = scrub_env(os.environ, token=token, max_tokens=max_tokens)

        if session:
            hashes = _msg_hashes(messages)
            with self._lock:
                st = dict(self._sessions.get(session) or {})
            delta = self._session_delta(st, messages, hashes)
            if delta is not None:
                try:
                    return self._run_session(cli, model, system, schema_str, effort, env,
                                             timeout, session, st["sid"], st["cwd"],
                                             prompt=delta, hashes=hashes, resume=True,
                                             want_json=schema is not None)
                except EndpointError:
                    # a broken/expired CLI session must never break the run — reseed fresh
                    with self._lock:
                        self._sessions.pop(session, None)
            sid = str(uuid.uuid4())
            cwd = expand("~/.cache/rsched/claude-cli") / hashlib.sha1(
                session.encode("utf-8")).hexdigest()[:16]
            cwd.mkdir(parents=True, exist_ok=True)
            return self._run_session(cli, model, system, schema_str, effort, env, timeout,
                                     session, sid, str(cwd), prompt=render_prompt(rest),
                                     hashes=hashes, resume=False,
                                     want_json=schema is not None)

        prompt = render_prompt(rest)
        cmd = build_cmd(cli, model, system=system or None, schema_str=schema_str, effort=effort)
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

    @staticmethod
    def _session_delta(st: dict, messages: list[Message], hashes: list[str]) -> str | None:
        """The new user content since the session last saw this conversation — or None
        when the session can't continue it (no state, rewritten prefix after compaction,
        or unexpected roles in the delta)."""
        seen = st.get("hashes")
        if not st.get("sid") or not st.get("cwd") or not seen:
            return None
        if len(hashes) <= len(seen) or hashes[:len(seen)] != seen:
            return None
        new = messages[len(seen):]
        if new and new[0]["role"] == "assistant":
            new = new[1:]   # the model's own last reply — the CLI session already holds it
        if not new or any(m["role"] != "user" for m in new):
            return None
        return "\n\n".join(m["content"] for m in new)

    def _run_session(self, cli, model, system, schema_str, effort, env, timeout,
                     session: str, sid: str, cwd: str, *, prompt: str, hashes: list[str],
                     resume: bool, want_json: bool) -> Completion:
        cmd = build_cmd(cli, model, system=system or None, schema_str=schema_str,
                        effort=effort, session_id=None if resume else sid,
                        resume=sid if resume else None)
        try:
            r = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                               timeout=timeout, env=env, cwd=cwd)
        except subprocess.TimeoutExpired as exc:
            raise EndpointError(f"claude-cli: call timed out after {timeout}s", retryable=True) from exc
        if r.returncode != 0 and not r.stdout.strip():
            raise EndpointError(
                f"claude-cli: exited {r.returncode}: {r.stderr.strip()[:300] or '(no stderr)'}",
                retryable=True,
            )
        text, parsed, usage = parse_result(r.stdout, want_json=want_json)
        with self._lock:
            self._sessions[session] = {"sid": sid, "hashes": list(hashes), "cwd": cwd}
        return Completion(text=text, parsed=parsed, usage=usage)
