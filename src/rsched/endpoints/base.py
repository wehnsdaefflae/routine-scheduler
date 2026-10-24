"""ChatEndpoint protocol: one stateless completion in, text or natively-parsed JSON out.

Adapters return complete responses (no token streaming — the engine streams whole transcript
events). Retryable transport errors are raised as EndpointError(retryable=True); the shared
`with_retries` helper (tenacity) gives HTTP adapters a uniform 3-try exponential backoff.

What media an endpoint may be handed lives NEXT DOOR, in `media_types.py`: the one
modality-tagged allowlist (image / pdf / audio / video), the size and pixel bounds, and the
`supports_media_type` core every adapter's `supports_media` is built from. It is a separate
module because it is pure — tables and file-header reads, no transport — and because this file
is over the house size bar and may only shrink.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import httpx
from tenacity import Retrying, retry_if_exception, stop_after_attempt, wait_exponential

from . import attempts

# {"role": "system"|"user"|"assistant", "content": str} — plus an OPTIONAL "media" list for
# native media input: [{"path": <abs file>, "media_type": <mime>}], the mime being one of
# `media_types.MEDIA_MODALITIES`. `content` stays a str always (so every str-assuming site keeps
# working); only adapters whose model is multimodal read `media` and fold the files into the
# provider payload at send time.
Message = dict

DEFAULT_TIMEOUT = 600

#: How long to wait for the TCP/TLS handshake, separately from the 600 s a model may
#: legitimately spend THINKING. httpx applies a scalar timeout to every phase, so one
#: value meant a provider that stopped answering at the IP level (route flap, firewall)
#: held a turn for 600 s × 3 tries = half an hour, with no observation and no cooldown
#: until the end. A connect that has not completed in ten seconds is not slow, it is gone.
CONNECT_TIMEOUT = 10.0

# F220: the longest a server-sent Retry-After hint is honored before with_retries falls back
# to its own schedule — bounds how long a single rate-limited attempt can pause a run.
RETRY_AFTER_CAP_S = 30.0

class EndpointError(Exception):
    """A transport failure. `retryable` feeds the with_retries backoff; `auth` lets the
    UI say "check the key" instead of a bare error.
    """

    def __init__(self, message: str, *, retryable: bool = False, auth: bool = False,
                 retry_after: float | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.auth = auth
        # F220: the server's explicit Retry-After hint (seconds), when it sent one on a 429.
        # with_retries honors it (capped) instead of its generic exponential schedule, so a
        # provider asking for a longer pause is waited out rather than failed over prematurely.
        self.retry_after = retry_after


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
    # The schema'd objects a reply carried AFTER `parsed`, in wire order — a reply that chains as
    # several CALLS (F754); empty for a reply with one object or none. `parsed` stays exactly one
    # object, which is what its ~30 readers mean by it; the rest live here, and
    # `replyactions.reply_actions` folds them in after the text actions so chaining runs them in
    # order. Dropping them was the R2443-R2445 false-claim shape on the call channel.
    parsed_rest: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=lambda: {"in": 0, "out": 0})
    provider: str = ""            # serving provider behind an aggregator (OpenRouter), if reported
    # Why generation stopped, VERBATIM from the provider (anthropic stop_reason, openai
    # finish_reason, ollama done_reason) — "" when unreported. One mapped exception:
    # openai_compat promotes the spec's dedicated `message.refusal` field to "refusal" when
    # content is empty, so the same semantic isn't hidden behind a bare finish_reason
    # "stop". The engine keys off this to tell a classifier refusal (HTTP 200, stop_reason
    # "refusal"/"content_filter", usually an EMPTY reply) from a provider hiccup: a refusal
    # is referred/failed over, NEVER blind-retried against the same model
    # (engine/completion.py REFUSAL_STOPS).
    stop_reason: str = ""
    # Provider detail on WHY it stopped ({category, explanation, ...} verbatim on a Messages
    # API classifier refusal, which can omit it; the promoted refusal's prose on
    # openai_compat) — {} when unreported. The Anthropic adapter adds `unread` to a reply it
    # could read nothing from: its content blocks by type, never a value. Diagnostic only:
    # surfaced in the refusal and empty-completion error events so the transcript says what
    # came back (F164, R5); the engine branches on stop_reason, never on this. It also adds
    # `forced_reask` — what an unreadable `auto` reply carried — to the answer it got by asking
    # once more with the tool forced, a turn that ran at the proxy's default effort.
    stop_details: dict = field(default_factory=dict)


class ChatEndpoint(Protocol):
    """What every adapter implements: one stateless completion in, a Completion out.
    No streaming, no state, no tools — endpoints are transports, never a second harness.
    Nothing identifies the conversation: caching is the PROVIDER's, earned by a byte-stable
    prefix (implicit for OpenAI-style providers, `cache_control` breakpoints for anthropic),
    which the engine's append-only message list gives it without a key. Nor the model's
    window: one endpoint serves many models, so the window rides the resolved ModelRef.
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
        message's `media` list NATIVELY — given the resolved model's modality bits, which the
        caller passes because one endpoint serves many models: `multimodal` always, plus
        `audio` / `video` for the two time-based modalities. False → the engine routes that
        file through the `vision` util (or refuses it, saying why). The adapter contributes
        only the WIRE facts on top: PDFs ride the Anthropic adapter, audio rides the
        OpenAI-compatible one, and each adapter answers False for what its wire cannot carry.
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
        # the resolver RAISES here (an env file was explicitly configured and the key
        # is not in it) — reporting a benign "none" made the Settings card lie
        return {"source": "none", "var": key_var, "env_file": key_env_file,
                "env_file_miss": True}
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

    `timeout` bounds the ANSWER (a reasoning model's whole turn); reaching the host is
    bounded much tighter — see CONNECT_TIMEOUT.
    """
    try:
        return httpx.post(url, json=body, headers=headers,
                          timeout=httpx.Timeout(timeout, connect=CONNECT_TIMEOUT))
    except httpx.HTTPError as exc:
        raise EndpointError(f"{name}: {_transport_cause(exc, timeout)}", retryable=True) from exc


def _transport_cause(exc: httpx.HTTPError, timeout: float) -> str:
    """What ended the attempt, in words — because httpx's timeout exceptions carry an EMPTY
    message (F655: `str(httpx.ReadTimeout(""))` is `""`), so the operator's 600 s read cut
    reached his console as `claude-proxy: ` and said nothing about the bound it hit. The
    attempt record this error travels in is only as useful as this string, and a timeout is
    exactly the failure a reader needs named: which clock ran out, and what it was set to.
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


#: Statuses that mean "the request never got a verdict", so sending it again can work: 408
#: request timeout, 409 conflict, 429 rate limit, every 5xx (529 rides that branch) — what
#: both provider SDKs retry — and a 402 whose body names in-flight requests, where the
#: provider holds credit the account HAS and says "retry after in-flight requests settle".
#: An out-of-credit 402 stays fatal. 408 is here because a proxy answers it when its own
#: upstream stream drops, and calling that fatal spent a MODEL SWITCH on a transient.
RETRYABLE_STATUSES = frozenset({408, 409, 429})


def raise_for_status(resp: httpx.Response, name: str) -> None:
    """The shared HTTP-status classifier: 401/403 → auth (the UI says "check the key"),
    408/409/429/5xx and a concurrency 402 → retryable, any other non-200 → fatal.
    """
    if resp.status_code == 200:
        return
    msg = f"{name}: HTTP {resp.status_code}: {resp.text[:300]}"
    if resp.status_code in (401, 403):
        raise EndpointError(msg, auth=True)
    transient_402 = resp.status_code == 402 and "in-flight requests" in resp.text
    if resp.status_code in RETRYABLE_STATUSES or resp.status_code >= 500 or transient_402:
        raise EndpointError(msg, retryable=True, retry_after=_retry_after_seconds(resp))
    raise EndpointError(msg)


def _retry_after_seconds(resp: httpx.Response) -> float | None:
    """The server's Retry-After hint in SECONDS, or None. Honors the numeric-seconds form
    (what OpenAI-compatible providers send on a 429, e.g. `Retry-After: 20`); an HTTP-date
    form is ignored (rare here, and clock-skew makes it unreliable) so the generic backoff
    applies instead. A non-positive or unparseable value → None.
    """
    raw = resp.headers.get("retry-after")
    if not raw:
        return None
    try:
        secs = float(raw.strip())
    except (TypeError, ValueError):
        return None
    return secs if secs > 0 else None


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
    RSCHED_RETRY_BASE_DELAY (default 1.0). The suite zeroes it so retry LOGIC runs while
    the clock does not — which only holds if every schedule reads it from here. The two
    schedules themselves differ on purpose and stay apart: this wrapper's exponential
    wait, and the engine's linear empty-completion pause (engine/completion.py).
    """
    import os

    return float(os.environ.get("RSCHED_RETRY_BASE_DELAY", "1.0"))


def with_retries(fn, *, tries: int = 3, base_delay: float | None = None):
    """Run fn(); on EndpointError(retryable=True) back off 1s/2s and retry (3 tries total).
    Non-retryable EndpointErrors propagate immediately; the last error is re-raised as-is.
    The default backoff honors RSCHED_RETRY_BASE_DELAY (`retry_base_delay`, read per call):
    the test suite zeroes it — dead-endpoint tests exercise the retry LOGIC, never the clock.

    Every boundary is reported through `attempts.boundary` BEFORE the loop sleeps (F655). The
    caller that observes a call wraps this from outside and therefore cannot see an attempt end:
    one `started` record covered up to three attempts of `DEFAULT_TIMEOUT` each, so a retrying
    call and a thinking one looked the same for as long as half an hour. Only the loop knows,
    and this is where it says so. The terminal failure is NOT reported here — the observing
    wrapper records that as the call's own `failed`, so nothing is booked twice.
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
        # F220: when the provider sent a Retry-After on a 429, wait exactly that (capped),
        # so a rate limit asking for a longer pause is honored instead of exhausting the
        # generic 1s/2s schedule and failing over. base_delay==0 (the test clock) short-
        # circuits the hint too, so retry-LOGIC tests never sleep on a real header.
        exc = state.outcome.exception() if state.outcome else None
        hint = getattr(exc, "retry_after", None)
        if hint and base_delay:
            return min(float(hint), RETRY_AFTER_CAP_S)
        return exp(state)

    return Retrying(
        retry=retry_if_exception(lambda e: isinstance(e, EndpointError) and e.retryable),
        stop=stop_after_attempt(tries),
        wait=wait,
        before_sleep=before_sleep,
        reraise=True,
    )(fn)
