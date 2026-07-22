"""ChatEndpoint protocol: one stateless completion in, text or natively-parsed JSON out.

Adapters return complete responses (no token streaming — the engine streams whole transcript
events). Retryable transport errors are raised as EndpointError(retryable=True); the shared
`with_retries` helper (tenacity) gives HTTP adapters a uniform 3-try exponential backoff.
"""

from __future__ import annotations

import base64
import mimetypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import httpx
from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_exponential

# {"role": "system"|"user"|"assistant", "content": str} — plus an OPTIONAL "media" list for
# multimodal input: [{"path": <abs file>, "media_type": <mime>}]. `content` stays a str
# always (so every str-assuming site keeps working); only adapters whose model is multimodal
# read `media` and fold the files into the provider payload at send time.
Message = dict

DEFAULT_TIMEOUT = 600
DEFAULT_MAX_TOKENS = 8192

# Native media the orchestrator can hand an endpoint. Base64 inflates ~33%, so the raw-byte
# ceiling keeps most providers' ~10 MB request limit; a larger file (or an unlisted type)
# routes to the `vision` util instead.
IMAGE_MIMES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
PDF_MIME = "application/pdf"
NATIVE_MEDIA_MAX_BYTES = 7 * 1024 * 1024


def guess_media_type(path: str | Path) -> str | None:
    """The mime for a path IF it is a media type an endpoint might take natively, else None."""
    mime = mimetypes.guess_type(str(path))[0]
    return mime if (mime in IMAGE_MIMES or mime == PDF_MIME) else None


def read_media_b64(path: str | Path) -> str:
    """The file's bytes as a base64 ASCII string (built at send time, never stored)."""
    return base64.b64encode(Path(path).read_bytes()).decode("ascii")


def supports_media_type(mime: str, *, multimodal: bool, pdf: bool) -> bool:
    """Shared `supports_media` core: images when multimodal; PDFs only where `pdf` (native
    document support) is also true. Everything else → the caller's vision-util fallback.
    """
    if not multimodal:
        return False
    if mime in IMAGE_MIMES:
        return True
    if mime == PDF_MIME:
        return pdf
    return False


class EndpointError(Exception):
    """A transport failure. `retryable` feeds the with_retries backoff; `auth` lets the
    UI say "check the key" instead of a bare error.
    """

    def __init__(self, message: str, *, retryable: bool = False, auth: bool = False):
        super().__init__(message)
        self.retryable = retryable
        self.auth = auth


@dataclass
class Completion:
    """One whole model reply: raw text, the natively schema-parsed object when the
    endpoint produced one, token usage, and the serving provider when reported.

    usage keys: "in" (fresh input tokens) and "out" always; "cached_in" (input served
    from the provider's prompt cache, ~0.1x price) and "cache_write" (input written into
    it, ~1.25x) when the provider reports cache traffic; "cost" (real $) when known.
    Adapters keep cache traffic OUT of "in" so token budgets keep their meaning.
    """

    text: str                     # raw reply text ("" when only parsed content came back)
    parsed: dict | None = None    # object from the endpoint's native schema mode, if any
    usage: dict = field(default_factory=lambda: {"in": 0, "out": 0})
    provider: str = ""            # serving provider behind an aggregator (OpenRouter), if reported
    # Why generation stopped, VERBATIM from the provider (anthropic stop_reason, openai
    # finish_reason, the CLI envelope's subtype) — "" when unreported. The engine keys off
    # it to tell a classifier refusal (an EMPTY completion with stop_reason "refusal")
    # from a provider hiccup: the first is referred/failed over, not blind-retried.
    stop_reason: str = ""


class ChatEndpoint(Protocol):
    """What every adapter implements: one stateless completion in, a Completion out.
    No streaming, no state, no tools — endpoints are transports, never a second harness.
    `session` is a CACHING hint only (a stable opaque key per conversation): an adapter
    may use it to keep the provider's prompt cache warm across turns (claude-cli keeps a
    CLI session per key); semantics never depend on it — every call still carries the
    full message list and adapters are free to ignore it.
    """

    name: str
    context_chars: int

    def complete(
        self,
        messages: list[Message],
        *,
        model: str,
        schema: dict | None = None,
        effort: str | None = None,
        max_tokens: int | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        session: str | None = None,
        temperature: float | None = None,
    ) -> Completion: ...

    def supports_media(self, media_type: str, *, multimodal: bool) -> bool:
        """Whether a file of `media_type` (an IMAGE_MIMES entry or PDF_MIME) can ride a
        message's `media` list NATIVELY — given the resolved model's `multimodal` flag (the
        caller passes it; one endpoint serves many models). False → the engine routes that
        file through the `vision` util instead. The adapter contributes only kind/runtime
        facts on top: PDFs are anthropic-only, and claude-cli drops to False once a
        stream-json image send has proven the CLI can't take them.
        """
        ...


def key_from_env_file(path: str, var: str) -> str | None:
    """Read VAR=value from a ~/.credentials/*.env style file (comments/quotes tolerated)."""
    from ..paths import expand

    p = expand(path)
    if not p.exists():
        return None
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            if k.strip() == var:
                return v.strip().strip('"').strip("'")
    return None


def split_system(messages: list[Message]) -> tuple[str, list[Message]]:
    """Pull leading system message(s) out; most APIs want them separated."""
    system_parts: list[str] = []
    rest: list[Message] = []
    for m in messages:
        if m["role"] == "system" and not rest:
            system_parts.append(m["content"])
        else:
            rest.append(m)
    return "\n\n".join(system_parts), rest


def api_key_source(*, api_key: str, key_var: str, key_env_file: str) -> dict:
    """Which rung of the credential ladder is LIVE for these settings — labels only, never
    key values (the Settings UI shows this per endpoint). Must mirror resolve_api_key's
    precedence exactly; keeping both beside each other is what stops them drifting.
    `shadowed_secret` flags the confusing case: an inline key wins while `key_var` is also
    set in the secrets store — editing the secret then changes nothing.
    """
    from ..secrets import load_secrets
    secret_set = bool(key_var and load_secrets().get(key_var))
    if api_key:
        return {"source": "inline", "var": key_var or None, "shadowed_secret": secret_set}
    if secret_set:
        return {"source": "secret", "var": key_var}
    if key_env_file and key_var and key_from_env_file(key_env_file, key_var):
        return {"source": "env_file", "var": key_var, "env_file": key_env_file}
    return {"source": "none", "var": key_var or None}


def resolve_api_key(*, name: str, api_key: str, key_var: str, key_env_file: str,
                    required: bool) -> str:
    """The shared credential ladder: inline `api_key` (UI-set) wins, then `key_var` in the
    central secrets store, then `key_var` inside `key_env_file`. A full miss raises
    auth-flagged when the endpoint requires a key (`required`, the anthropic case) or when
    an env file was explicitly configured; otherwise returns "none" — the placeholder
    bearer keyless local backends (Ollama, vLLM) ignore.
    """
    if api_key:
        return api_key
    from ..secrets import load_secrets
    if key_var and (key := load_secrets().get(key_var)):
        return key
    if key_env_file and (key := key_from_env_file(key_env_file, key_var)):
        return key
    if required or key_env_file:
        raise EndpointError(
            f"{name}: no API key — paste one in Settings, or put "
            f"`{key_var}=...` into {key_env_file}", auth=True)
    return "none"


def post_json(url: str, body: dict, headers: dict | None, timeout: int,
              *, name: str) -> httpx.Response:
    """POST a JSON body. A network-level failure (the provider was never reached) is always
    retryable; status-code classification is the caller's (`raise_for_status`).
    """
    try:
        return httpx.post(url, json=body, headers=headers, timeout=timeout)
    except httpx.HTTPError as exc:
        raise EndpointError(f"{name}: {exc}", retryable=True) from exc


def raise_for_status(resp: httpx.Response, name: str) -> None:
    """The shared HTTP-status classifier: 401/403 → auth (the UI says "check the key"),
    429/5xx → retryable (rate limit, outage — anthropic's 529 overloaded rides the 5xx
    branch), any other non-200 → fatal.
    """
    if resp.status_code == 200:
        return
    msg = f"{name}: HTTP {resp.status_code}: {resp.text[:300]}"
    if resp.status_code in (401, 403):
        raise EndpointError(msg, auth=True)
    if resp.status_code == 429 or resp.status_code >= 500:
        raise EndpointError(msg, retryable=True)
    raise EndpointError(msg)


def anthropic_usage(raw: dict) -> dict:
    """Anthropic-shaped usage (the Messages API and the claude CLI envelope) → our usage
    dict. `input_tokens` EXCLUDES cache traffic on this API; cache reads/writes are
    surfaced as `cached_in` / `cache_write`, kept OUT of "in" so token budgets keep
    their meaning.
    """
    usage = {"in": int(raw.get("input_tokens") or 0),
             "out": int(raw.get("output_tokens") or 0)}
    if raw.get("cache_read_input_tokens"):
        usage["cached_in"] = int(raw["cache_read_input_tokens"])
    if raw.get("cache_creation_input_tokens"):
        usage["cache_write"] = int(raw["cache_creation_input_tokens"])
    return usage


def json_or_raise(resp, name: str) -> dict:
    """Parse an HTTP body that should be JSON. A 2xx with a garbled body (truncated stream,
    proxy interference) is a transport fault — raised retryable so `with_retries` catches it
    instead of a JSONDecodeError blowing past the retry wrapper.
    """
    try:
        return resp.json()
    except ValueError as exc:  # json.JSONDecodeError is a ValueError
        raise EndpointError(
            f"{name}: HTTP {resp.status_code} with unparseable JSON body: {resp.text[:300]}",
            retryable=True,
        ) from exc


def with_retries(fn, *, tries: int = 3, base_delay: float | None = None):
    """Run fn(); on EndpointError(retryable=True) back off 1s/2s and retry (3 tries total).
    Non-retryable EndpointErrors propagate immediately; the last error is re-raised as-is.
    The default backoff honors RSCHED_RETRY_BASE_DELAY (read per call): the test suite
    zeroes it — dead-endpoint tests exercise the retry LOGIC, never the backoff clock.
    """
    if base_delay is None:
        import os

        base_delay = float(os.environ.get("RSCHED_RETRY_BASE_DELAY", "1.0"))
    return Retrying(
        retry=retry_if_exception(lambda e: isinstance(e, EndpointError) and e.retryable),
        stop=stop_after_attempt(tries),
        wait=wait_exponential(multiplier=base_delay),
        reraise=True,
    )(fn)
