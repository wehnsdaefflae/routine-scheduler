"""ChatEndpoint protocol: one stateless completion in, text or natively-parsed JSON out.

Adapters return complete responses (no token streaming — the engine streams whole transcript
events). Retryable transport errors are raised as EndpointError(retryable=True); the shared
`with_retries` helper (tenacity) gives HTTP adapters a uniform 3-try exponential backoff.

What media an endpoint may be handed lives in `media_types.py` (the modality allowlist,
size bounds and the `supports_media_type` core every adapter's `supports_media` builds on).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import httpx
from tenacity import Retrying, retry_if_exception, wait_exponential

from . import attempts, errorsignal

# {"role": "system"|"user"|"assistant", "content": str} — plus an OPTIONAL "media" list for
# native media input: [{"path": <abs file>, "media_type": <mime>}] (`media_types.MEDIA_MODALITIES`).
# `content` stays a str always; only multimodal adapters read `media`, at send time.
Message = dict

DEFAULT_TIMEOUT = 600

#: The TCP/TLS handshake bound, separate from the 600 s a model may spend THINKING: a connect
#: that has not completed in ten seconds is not slow, it is gone.
CONNECT_TIMEOUT = 10.0

# The longest a server-sent Retry-After hint is honored — bounds how long one rate-limited
# attempt can pause a run.
RETRY_AFTER_CAP_S = 30.0
#: A REFUSED connection is retried this long, at most STEP between tries (`retry_base_delay` units).
UNREACHABLE_PATIENCE, UNREACHABLE_STEP = 120.0, 10.0

class EndpointError(Exception):
    """A transport failure. `retryable` feeds the with_retries backoff, `auth` lets the UI say
    "check the key", `signal` carries what the failure DECLARED (read it with
    `errorsignal.of`, never by matching sentences in the message).
    """

    def __init__(self, message: str, *, retryable: bool = False, auth: bool = False,
                 retry_after: float | None = None, unreachable: bool = False,
                 signal: errorsignal.ErrorSignal | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.unreachable = unreachable  # a REFUSED connection: retried for UNREACHABLE_PATIENCE
        self.auth = auth
        # Retry-After (seconds) from a 429; with_retries honors it (capped) over its schedule.
        self.retry_after = retry_after
        # Status + the provider's error.code/type/param, decoded from the WHOLE body at the
        # raise — `message` keeps only resp.text[:300].
        self.signal = signal


@dataclass
class Completion:
    """One whole model reply: raw text, the natively schema-parsed object when the
    endpoint produced one, token usage, and the serving provider when reported.

    usage keys: "in" (fresh input) and "out" always; "cached_in" (prompt-cache reads) and
    "cache_write" when reported; "cost" (real $) when known; "notional" ($ at list price,
    stamped by the instrumentation seam — endpoints/prices.py). Adapters keep cache traffic
    OUT of "in" so token budgets keep their meaning.
    """

    text: str                     # raw reply text ("" when only parsed content came back)
    parsed: dict | None = None    # object from the endpoint's native schema mode, if any
    # The schema'd objects a reply carried AFTER `parsed`, in wire order (a reply chaining several
    # CALLS); `parsed` stays exactly one object. `replyactions.reply_actions` folds these in after
    # the text actions — never drop them.
    parsed_rest: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=lambda: {"in": 0, "out": 0})
    provider: str = ""            # serving provider behind an aggregator (OpenRouter), if reported
    # Why generation stopped, VERBATIM from the provider — "" when unreported. One mapping:
    # openai_compat promotes an empty reply's `message.refusal` to "refusal". The engine keys
    # off this to tell a classifier refusal from a hiccup (engine/completion.py REFUSAL_STOPS).
    stop_reason: str = ""
    # Provider detail on WHY it stopped ({category, explanation, ...}) — {} when unreported. The
    # Anthropic adapter adds `unread` (content block types of an unreadable reply) and
    # `forced_reask` (what an unreadable `auto` reply carried before the forced re-ask).
    # Diagnostic only: the engine branches on stop_reason, never on this.
    stop_details: dict = field(default_factory=dict)


class ChatEndpoint(Protocol):
    """What every adapter implements: one stateless completion in, a Completion out.
    No streaming, no state, no tools — endpoints are transports, never a second harness.
    Caching is the PROVIDER's, earned by the engine's byte-stable prefix; the model's window
    rides the resolved ModelRef, since one endpoint serves many models.
    """

    name: str

    def complete(
        self,
        messages: list[Message],
        *,
        model: str,
        schema: dict | None = None,
        effort: str | None = None,
        max_tokens: int | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        temperature: float | None = None,
        cacheable: bool = True,
    ) -> Completion: ...

    def supports_media(self, media_type: str, *, multimodal: bool, audio: bool,
                       video: bool) -> bool:
        """Whether a file of `media_type` (a `media_types.MEDIA_MODALITIES` entry) can ride a
        message's `media` list NATIVELY, given the resolved model's modality bits. False → the
        engine routes it through the `vision` util (or refuses it). The adapter adds only its
        WIRE facts (PDFs on Anthropic, audio on OpenAI-compatible).
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
    if key_env_file and key_var:
        if key_from_env_file(key_env_file, key_var):
            return {"source": "env_file", "var": key_var, "env_file": key_env_file}
        # the resolver RAISES here, so this is not a benign "none"
        return {"source": "none", "var": key_var, "env_file": key_env_file,
                "env_file_miss": True}
    return {"source": "none", "var": key_var or None}


def resolve_api_key(*, name: str, api_key: str, key_var: str, key_env_file: str,
                    required: bool) -> str:
    """The shared credential ladder: inline `api_key` (UI-set) wins, then `key_var` in the
    central secrets store, then `key_var` inside `key_env_file`. A full miss raises
    auth-flagged when the endpoint requires a key (`required`, the anthropic case) or when
    an env file was explicitly configured; otherwise returns "none" — the placeholder
    bearer keyless local backends (vLLM) ignore.
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

    `timeout` bounds the ANSWER (a reasoning model's whole turn); reaching the host is
    bounded much tighter — see CONNECT_TIMEOUT.
    """
    try:
        return httpx.post(url, json=body, headers=headers,
                          timeout=httpx.Timeout(timeout, connect=CONNECT_TIMEOUT))
    except httpx.HTTPError as exc:
        raise EndpointError(f"{name}: {_transport_cause(exc, timeout)}", retryable=True,
                            unreachable=isinstance(exc, httpx.ConnectError)) from exc


def _transport_cause(exc: httpx.HTTPError, timeout: float) -> str:
    """What ended the attempt, in words — httpx's timeout exceptions carry an EMPTY message,
    and a timeout needs naming: which clock ran out, and what it was set to.
    """
    text = str(exc).strip()
    if isinstance(exc, httpx.ConnectTimeout):
        return f"connect timeout after {CONNECT_TIMEOUT:g}s (the host never answered)"
    if isinstance(exc, httpx.ReadTimeout):
        return f"read timeout after {timeout:g}s (connected, but no reply within the bound)"
    if isinstance(exc, httpx.TimeoutException):
        kind = type(exc).__name__.removesuffix("Timeout").lower() or "transport"
        return f"{kind} timeout after {timeout:g}s"
    return text or type(exc).__name__


#: Re-exported from `errorsignal`, where the failure classification lives.
RETRYABLE_STATUSES = errorsignal.RETRYABLE_STATUSES


def raise_for_status(resp: httpx.Response, name: str) -> None:
    """The shared HTTP-status classifier: 401/403 → auth (the UI says "check the key"),
    408/409/429/5xx and a held-credit 402 → retryable, any other non-200 → fatal. The signal is
    built HERE from the whole body, because `msg` keeps only its first 300 characters.
    """
    if resp.status_code == 200:
        return
    msg = f"{name}: HTTP {resp.status_code}: {resp.text[:300]}"
    signal = errorsignal.from_body(resp.status_code, resp.text)
    if resp.status_code in (401, 403):
        raise EndpointError(msg, auth=True, signal=signal)
    if errorsignal.retryable(signal):
        raise EndpointError(msg, retryable=True, signal=signal,
                            retry_after=errorsignal.retry_after_seconds(resp.headers))
    raise EndpointError(msg, signal=signal)


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


def retry_base_delay() -> float:
    """The unit of every retry backoff in the system, read PER CALL from
    RSCHED_RETRY_BASE_DELAY (default 1.0); the suite zeroes it so retry LOGIC runs without the
    clock. The exponential wait here and the engine's linear empty-completion pause differ on
    purpose.
    """
    import os

    return float(os.environ.get("RSCHED_RETRY_BASE_DELAY", "1.0"))


def with_retries(fn, *, tries: int = 3, base_delay: float | None = None):
    """Run fn(); on EndpointError(retryable=True) back off 1s/2s and retry (3 tries total).
    Non-retryable EndpointErrors propagate immediately; the last error is re-raised as-is.
    The default backoff unit is `retry_base_delay()`.

    Every boundary is reported through `attempts.boundary` BEFORE the loop sleeps — only the
    loop can tell a retrying call from a thinking one. The terminal failure is NOT reported
    here: the observing wrapper records it as the call's own `failed`.
    """
    if base_delay is None:
        base_delay = retry_base_delay()
    exp = wait_exponential(multiplier=base_delay)

    def before_sleep(state):
        exc = state.outcome.exception() if state.outcome else None
        if exc is not None:
            attempts.boundary(attempt=state.attempt_number, tries=tries, error=exc,
                              retry_in=float(state.upcoming_sleep or 0.0))

    def wait(state):
        # A 429's Retry-After is waited exactly (capped) rather than failing over;
        # base_delay==0 (the test clock) skips the hint too.
        exc = state.outcome.exception() if state.outcome else None
        hint = getattr(exc, "retry_after", None)
        if hint and base_delay:
            return min(float(hint), RETRY_AFTER_CAP_S)
        if getattr(exc, "unreachable", False):
            return min(exp(state), UNREACHABLE_STEP * base_delay)
        return exp(state)

    def stop(state) -> bool:
        exc = state.outcome.exception() if state.outcome else None
        patient = getattr(exc, "unreachable", False) and \
            (state.seconds_since_start or 0.0) < UNREACHABLE_PATIENCE * base_delay
        return not patient and state.attempt_number >= tries

    return Retrying(
        retry=retry_if_exception(lambda e: isinstance(e, EndpointError) and e.retryable),
        stop=stop,
        wait=wait,
        before_sleep=before_sleep,
        reraise=True,
    )(fn)
