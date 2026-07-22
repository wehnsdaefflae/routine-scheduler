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

Multimodal (images): the CLI stays fully stripped (`--tools ""`) — the transport-only
invariant is intact. When a message carries a `media` list, the send switches to
`--input-format stream-json` and the image rides as a base64 block INSIDE the message (the
same Anthropic vision shape), so the model sees it as data, never by reading a file. That
CLI input format is undocumented, so it is de-risked: a stream-json send that fails flips a
per-process capability flag (`supports_media` then routes further images to the vision util)
AND raises so the engine's runtime net falls back for the current image. PDFs are not sent
natively (stream-json takes images only) — they route to the vision util.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from collections.abc import Mapping
from pathlib import Path

from ..config import EndpointConfig
from ..paths import expand
from .base import (
    DEFAULT_TIMEOUT,
    Completion,
    EndpointError,
    Message,
    anthropic_usage,
    read_media_b64,
    split_system,
    supports_media_type,
    with_retries,
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
                 stream_out: bool = False) -> tuple[str, dict | None, dict, str]:
    """CLI --output-format json envelope (or the final `result` event of a stream-json
    output stream, used for image turns) → (text, parsed, usage, stop_reason).
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
    return text, parsed, usage, stop


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


class ClaudeCliEndpoint:
    """`claude -p` fully stripped (tools off, no MCP/settings, our system prompt replacing
    its own) — a SUBSCRIPTION-billed completion function. Metered-auth env vars are
    scrubbed so it can never silently fall back to API billing. With a `session` key it
    keeps one CLI session per run and sends per-turn deltas (see the module docstring).
    """

    def __init__(self, cfg: EndpointConfig):
        self.name = cfg.name
        self.credentials_env = cfg.credentials_env
        self.oauth_token = cfg.api_key            # inline token pasted in Settings (optional)
        self.context_chars = cfg.context_chars
        # None = untested; True/False set on the first stream-json send. Once False (a
        # send failed → the CLI likely lacks stream-json image input), supports_media returns
        # False so further images route to the vision util instead of re-failing.
        self._media_capable: bool | None = None
        # session key → {"sid": CLI session id, "hashes": per-message sha1 of what the
        # session has already seen, "cwd": the stable dir the CLI keys its store to}
        self._sessions: dict[str, dict] = {}
        self._lock = threading.Lock()

    def supports_media(self, media_type: str, *, multimodal: bool) -> bool:
        """Images only (when the resolved model is multimodal), and only until a stream-json
        send has proven the CLI can't take them (then everything routes to the vision util).
        PDFs always route to the vision util.
        """
        if self._media_capable is False:
            return False
        return supports_media_type(media_type, multimodal=multimodal, pdf=False)

    def complete(self, messages: list[Message], *, model: str, schema: dict | None = None,
                 effort: str | None = None, max_tokens: int | None = None,
                 timeout: int = DEFAULT_TIMEOUT, session: str | None = None,
                 temperature: float | None = None) -> Completion:  # noqa: ARG002 — see below
        # temperature is accepted for protocol conformance but ignored: the stripped
        # `claude -p` subscription CLI exposes no sampling knob.
        cli = find_cli()
        if not cli:
            raise EndpointError("claude-cli: claude CLI not found on PATH (or set $CLAUDE_CLI)")
        from ..secrets import load_secrets
        token = resolve_token(self.credentials_env,
                              self.oauth_token or load_secrets().get(TOKEN_VAR, ""))
        if not token:
            raise EndpointError(
                f"claude-cli: no subscription token — paste one in Settings, set "
                f"{TOKEN_VAR}, or put it in {self.credentials_env} "
                "(run `claude setup-token`); refusing API billing",
                auth=True,
            )
        system, rest = split_system(messages)
        schema_str = json.dumps(schema) if schema is not None else None
        env = scrub_env(os.environ, token=token, max_tokens=max_tokens)

        if session:
            hashes = _msg_hashes(messages)
            with self._lock:
                st = dict(self._sessions.get(session) or {})
            delta = self._session_delta(st, messages, hashes)   # list[Message] | None
            if delta is not None:
                try:
                    return self._run_session(cli, model, system, schema_str, effort, env,
                                             timeout, session, st["sid"], st["cwd"],
                                             msgs=delta,
                                             render=lambda ms: "\n\n".join(m["content"]
                                                                           for m in ms),
                                             hashes=hashes, resume=True,
                                             want_json=schema is not None)
                except EndpointError:
                    # a broken/expired CLI session must never break the run — reseed fresh
                    with self._lock:
                        self._sessions.pop(session, None)
            cwd = expand("~/.cache/rsched/claude-cli") / hashlib.sha1(
                session.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
            _gc_session_cwds(cwd.parent)   # once per run: prune week-old sibling dirs
            cwd.mkdir(parents=True, exist_ok=True)
            return self._run_session(cli, model, system, schema_str, effort, env, timeout,
                                     session, "", str(cwd), msgs=rest,
                                     render=render_prompt, hashes=hashes, resume=False,
                                     want_json=schema is not None)

        prompt = render_prompt(rest)
        cmd = build_cmd(cli, model, system=system or None, schema_str=schema_str, effort=effort)

        def attempt() -> Completion:
            try:
                with tempfile.TemporaryDirectory(prefix="rsched-claude-") as tmp_cwd:
                    r = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                                       timeout=timeout, env=env, cwd=tmp_cwd, check=False)
            except subprocess.TimeoutExpired as exc:
                raise EndpointError(f"claude-cli: call timed out after {timeout}s",
                                    retryable=True) from exc
            if r.returncode != 0 and not r.stdout.strip():
                raise EndpointError(
                    f"claude-cli: exited {r.returncode}: "
                    f"{r.stderr.strip()[:300] or '(no stderr)'}",
                    retryable=True,
                )
            text, parsed, usage, stop = parse_result(r.stdout, want_json=schema is not None)
            return Completion(text=text, parsed=parsed, usage=usage, stop_reason=stop)

        # The CLI spawns a fresh process per call, so a transient failure (OOM kill, a
        # dropped upstream connection) surfaces as one bad invocation — back off and retry
        # like the HTTP adapters instead of failing the turn on a single blip.
        return with_retries(attempt)

    @staticmethod
    def _session_delta(st: dict, messages: list[Message],
                       hashes: list[str]) -> list[Message] | None:
        """The new user MESSAGES since the session last saw this conversation — or None
        when the session can't continue it (no state, rewritten prefix after compaction,
        or unexpected roles in the delta). Returned as messages (not joined text) so a
        media-carrying turn keeps its `media` for stream-json encoding.
        """
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
        return new

    def _encode(self, msgs: list[Message], render) -> tuple[str, bool]:
        """(stdin, use_stream_json). Media present → an NDJSON stream-json body; else
        `render(msgs)` as the plain text prompt. When native image input is known-broken:
        a TAIL image raises (the engine's runtime net converts it via the vision util),
        while OLDER in-context media — a reseed replaying turns whose images the model
        already saw — degrades to a placeholder note so the reseed still succeeds.
        """
        if _has_media(msgs):
            if self._media_capable is not False:
                return stream_json_stdin(msgs), True
            if msgs[-1].get("media"):
                raise EndpointError("claude-cli: native image input unavailable this run — "
                                    "routing to the vision util")
            msgs = [{**{k: v for k, v in m.items() if k != "media"},
                     "content": m["content"] + "\n[" + ", ".join(
                         Path(i["path"]).name for i in m["media"])
                     + ": shown earlier — not re-sent]"}
                    if m.get("media") else m for m in msgs]
        return render(msgs), False

    # One arg per CLI invocation fact — a params object would rename the width, not reduce it.
    def _run_session(self, cli, model, system, schema_str, effort, env, timeout,  # noqa: PLR0913
                     session: str, sid: str, cwd: str, *, msgs: list[Message], render,
                     hashes: list[str], resume: bool, want_json: bool) -> Completion:
        stdin, stream = self._encode(msgs, render)
        sid_used = sid

        def attempt() -> Completion:
            nonlocal sid_used
            # A fresh session id per OPEN attempt: if a garbled first attempt already
            # created the CLI session, retrying the same --session-id would refuse.
            sid_used = sid if resume else str(uuid.uuid4())
            cmd = build_cmd(cli, model, system=system or None, schema_str=schema_str,
                            effort=effort, session_id=None if resume else sid_used,
                            resume=sid_used if resume else None, input_stream_json=stream)
            try:
                r = subprocess.run(cmd, input=stdin, capture_output=True, text=True,
                                   timeout=timeout, env=env, cwd=cwd, check=False)
            except subprocess.TimeoutExpired as exc:
                raise EndpointError(f"claude-cli: call timed out after {timeout}s",
                                    retryable=True) from exc
            if r.returncode != 0 and not r.stdout.strip():
                raise EndpointError(
                    f"claude-cli: exited {r.returncode}: "
                    f"{r.stderr.strip()[:300] or '(no stderr)'}",
                    retryable=True,
                )
            text, parsed, usage, stop = parse_result(r.stdout, want_json=want_json,
                                                     stream_out=stream)
            return Completion(text=text, parsed=parsed, usage=usage, stop_reason=stop)

        try:
            # A RESUME gets one attempt only — its failure path (reseed a fresh session)
            # is itself the retry, and re-running a broken resume 3x would just delay it.
            # Fresh seeds and one-shot session opens get the standard backoff.
            comp = with_retries(attempt, tries=1 if resume else 3)
        except EndpointError:
            if stream and not resume:
                # Retries exhausted on a stream-json send — evidence the CLI can't take
                # image input (not a one-call blip): route further images to the vision
                # util for the rest of this process.
                self._media_capable = False
            raise
        if stream:
            self._media_capable = True    # native image input works on this CLI
        with self._lock:
            self._sessions[session] = {"sid": sid_used, "hashes": list(hashes), "cwd": cwd}
        return comp
