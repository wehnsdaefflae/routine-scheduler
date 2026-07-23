"""The claude-CLI wire protocol: command construction, env scrubbing, token/credential
resolution, prompt serialization, stream-json encoding, and result-envelope parsing —
everything about TALKING to `claude -p`, with no session state. The adapter
(claude_cli.ClaudeCliEndpoint) owns sessions, retries, and the media-capability latch.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from collections.abc import Mapping
from pathlib import Path

from ..paths import expand
from .base import (
    EndpointError,
    Message,
    anthropic_usage,
    read_media_b64,
)


def _has_media(messages: list[Message]) -> bool:
    return any(m.get("media") for m in messages)


def _cli_content_blocks(content: str, media: list[dict]) -> list[dict]:
    """Text + images → Anthropic-shape content blocks for a stream-json message (images
    only; PDFs are filtered out upstream by supports_media).
    """
    blocks: list[dict] = [{"type": "text", "text": content}] if content else []
    blocks.extend({"type": "image", "source": {
        "type": "base64", "media_type": item["media_type"],
        "data": read_media_b64(item["path"])}} for item in media)
    return blocks


def stream_json_stdin(messages: list[Message]) -> str:
    """NDJSON stdin for `--input-format stream-json`: one line per message, each an
    `{"type": <role>, "message": {role, content:[blocks]}}` envelope.
    """
    lines = []
    for m in messages:
        role = m["role"]
        blocks = _cli_content_blocks(m.get("content", ""), m.get("media") or [])
        lines.append(json.dumps({"type": role, "message": {"role": role, "content": blocks}},
                                ensure_ascii=False))
    return "\n".join(lines)

# Vars that would re-route the child CLI to metered API-key auth (or a proxy), plus the
# SSH agent socket — the same never-inherit rule utils_lib.STRIP_VARS applies to util
# subprocesses (a forwarded agent must not reach ANY child we spawn).
STRIP_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_KEY", "ANTHROPIC_AUTH_TOKEN",
              "ANTHROPIC_BASE_URL", "ANTHROPIC_CUSTOM_HEADERS",
              "SSH_AUTH_SOCK", "SSH_AGENT_PID")
TOKEN_VAR = "CLAUDE_CODE_OAUTH_TOKEN"  # noqa: S105 — the env var's NAME, not a credential


# Per-run CLI session dirs live under one cache root; each run mints one and nothing else
# removes them. Swept opportunistically when a new session opens (best-effort — the dir is
# shared, concurrent engines race freely and losers just skip).
SESSION_CWD_MAX_AGE_S = 7 * 86400


def _gc_session_cwds(root: Path) -> None:
    if not root.is_dir():
        return
    cutoff = time.time() - SESSION_CWD_MAX_AGE_S
    for d in root.iterdir():
        try:
            if d.is_dir() and d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            continue


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


def token_source(credentials_env: str, inline: str) -> dict:
    """Which rung the subscription token would come from RIGHT NOW — labels only, never the
    token (the Settings UI shows this on the endpoint card). Must mirror the effective
    ladder in complete(): process env var → inline token (UI-set) → the secrets store →
    the credentials env-file. `shadowed_secret` flags an inline token hiding a set secret.
    """
    from ..secrets import load_secrets
    secret_set = bool(load_secrets().get(TOKEN_VAR))
    if os.environ.get(TOKEN_VAR):
        return {"source": "process_env", "var": TOKEN_VAR}
    if inline:
        return {"source": "inline", "var": TOKEN_VAR, "shadowed_secret": secret_set}
    if secret_set:
        return {"source": "secret", "var": TOKEN_VAR}
    if resolve_token(credentials_env):
        return {"source": "env_file", "var": TOKEN_VAR, "env_file": credentials_env}
    return {"source": "none", "var": TOKEN_VAR}


def scrub_env(base: Mapping[str, str], *, token: str | None,
              max_tokens: int | None = None) -> dict:
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
              resume: str | None = None, input_stream_json: bool = False) -> list[str]:
    cmd = [cli, "-p", "--model", model,
           "--tools", "",                 # no built-in tools → no agentic loop
           "--disable-slash-commands",
           "--strict-mcp-config",         # with no --mcp-config: no MCP servers
           "--setting-sources", ""]       # ignore user/project settings
    if input_stream_json:
        # Image turns: base64 blocks ride an NDJSON stdin. The CLI requires the OUTPUT format
        # to match (and --verbose) when input is stream-json; the reply is then an event
        # stream whose final `result` event carries the same envelope parse_result reads.
        cmd += ["--input-format", "stream-json", "--output-format", "stream-json", "--verbose"]
    else:
        cmd += ["--output-format", "json"]
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
    cue keeps stateless calls faithful to the conversation.
    """
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


def _result_event(stdout_text: str) -> dict:
    """The terminal `{"type":"result", …}` line of a `--output-format stream-json` stream —
    same fields as the `--output-format json` envelope. Non-JSON/other-type lines are skipped.
    """
    obj = None
    for line in stdout_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(ev, dict) and ev.get("type") == "result":
            obj = ev
    if obj is None:
        raise EndpointError(
            f"claude-cli: no result event in stream-json output: {stdout_text[:300]}")
    return obj


def parse_result(stdout_text: str, want_json: bool,
                 stream_out: bool = False) -> tuple[str, dict | None, dict, str, dict]:
    """CLI --output-format json envelope (or the final `result` event of a stream-json
    output stream, used for image turns) → (text, parsed, usage, stop_reason,
    stop_details — the envelope's diagnostic dict, e.g. {"category": ...} on a
    classifier refusal; {} when absent).
    A garbled/truncated stdout is a transport fault — retryable, like an unparseable
    HTTP body (json_or_raise), so with_retries catches it.
    """
    try:
        obj = _result_event(stdout_text) if stream_out else json.loads(stdout_text)
    except json.JSONDecodeError as exc:
        raise EndpointError(f"claude-cli: unparseable CLI output: {stdout_text[:300]}",
                            retryable=True) from exc
    if obj.get("is_error"):
        msg = str(obj.get("result") or "claude CLI reported an error")
        raise EndpointError(f"claude-cli: {msg}", auth="401" in msg)
    # input_tokens excludes cache traffic on this API — without cached_in/cache_write a run
    # shows "in=4" while the real prompt was served from (and written into) the cache.
    usage = anthropic_usage(obj.get("usage") or {})
    text = obj.get("result", "") or ""
    # The envelope reports why generation stopped as `stop_reason` (newer CLIs) or only the
    # result `subtype` (e.g. success / error_during_execution) — surface what it has.
    stop = str(obj.get("stop_reason") or "")
    if not stop and str(obj.get("subtype") or "") not in ("", "success"):
        stop = str(obj["subtype"])
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
    details = obj.get("stop_details")
    return text, parsed, usage, stop, details if isinstance(details, dict) else {}


def _msg_hashes(messages: list[Message]) -> list[str]:
    # A text-only message hashes exactly as before (session prefix-matching is unchanged);
    # media adds a suffix so an image turn is never confused with a same-text one.
    out = []
    for m in messages:
        key = f"{m['role']}\x00{m['content']}"
        if m.get("media"):
            key += "\x00" + json.dumps(m["media"], sort_keys=True)
        out.append(hashlib.sha1(key.encode("utf-8"), usedforsecurity=False).hexdigest())
    return out
