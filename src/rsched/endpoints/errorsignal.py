"""What a failed call DECLARED about itself — the one structural reading of a provider error.

Retry, failover and prompt-window policy (`base.py`, `engine/overflow.py`, `engine/degrade.py`)
all ask this module instead of matching English sentences in an error body (R3108).

A provider's `error.code` / `type` / `param` / `metadata.limit_source` are a DECLARED ENUM the
provider commits to; its `message` is prose for a human. Reading the enum is structural; reading
the sentence is guessing, and a phrase list is silently disarmed for any vendor whose wording
nobody added.

The tables carry what is KNOWN; the fallback under them is structural, not prose: a structured
400/413 stating a token figure is an overflow whatever enum it names (`is_overflow`), and a
class falls back to the HTTP status (`failover_class`). A new enum token is an improvement,
never the difference between armed and disarmed.

A body with NO JSON (a proxy relaying an upstream failure bare) keeps two structural readings:
the HTTP status, and a known enum token appearing bare (`_declared_token`), with
`structured=False` so a caller can tell. Each undecidable site states which way it fails;
`engine/errorclass.py` counts those rows.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

#: Declared enum tokens meaning "the input did not fit the context window".
OVERFLOW_CODES = frozenset({
    "context_length_exceeded",      # OpenAI and every openai-compatible proxy
    "string_above_max_length",      # Anthropic
    "context_window_exceeded",
    "prompt_too_long",
    "max_prompt_tokens_exceeded",
    "input_length_exceeded",
})

#: Declared enum tokens that are NEVER a context fault, however their message is worded. Reading
#: one as an overflow is worse than missing one: it pins the run's window to a figure that was
#: never a context size and suppresses the failover the real fault needed.
RATE_LIMIT_CODES = frozenset({
    "rate_limit_exceeded", "rate_limit_error", "rate_limited", "insufficient_quota",
    "quota_exceeded", "usage_limit_reached", "tokens_per_minute_exceeded",
    "requests_per_minute_exceeded", "resource_exhausted", "overloaded_error",
})
AUTH_CODES = frozenset({
    "invalid_api_key", "authentication_error", "permission_error", "permission_denied",
    "invalid_authentication", "auth_unavailable", "account_deactivated", "unauthenticated",
})
BILLING_CODES = frozenset({
    "billing_hard_limit_reached", "billing_not_active", "insufficient_credits",
    "insufficient_balance", "credit_limit_reached",
})
NEVER_OVERFLOW_CODES = RATE_LIMIT_CODES | AUTH_CODES | BILLING_CODES

#: Statuses that are never a context fault whatever they say: a quota, a balance, a credential.
NEVER_OVERFLOW_STATUSES = frozenset({401, 402, 403, 429})
#: …and the two a provider answers when the input itself was too big.
OVERFLOW_STATUSES = frozenset({400, 413})

#: What the ENGINE itself found, declared on the error at the raise rather than written into a
#: sentence (a provider quoting the same words must not be filed as a refusal).
ENGINE_CLASSES = {"refusal": "refusal", "empty": "empty", "transport": "server"}

_STATUS_RE = re.compile(r"HTTP\s+(\d{3})")
_JSON_RE = re.compile(r"[{\[]")
_SNAKE_RE = re.compile(r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+)+)\b")
#: A stated pair, "<actual> tokens > <maximum>" — the shape that makes an overshoot computable.
TOKEN_PAIR_RE = re.compile(
    r"(\d{4,9})\s*tokens?\s*(?:>|&gt;|\\u003e|exceeds?)\s*(\d{4,9})", re.IGNORECASE)
#: A stated maximum alone, as a figure attached to a window noun rather than to a sentence.
TOKEN_MAX_RE = re.compile(
    r"(?:maximum context length(?: is)?|context (?:window|length) of(?: only)?|"
    r"context_length_exceeded\D{0,40}?|maximum(?: of)?)\s*(\d{4,7})\s*tokens", re.IGNORECASE)


@dataclass(frozen=True)
class ErrorSignal:
    """One failed call's DECLARED facts. Every field is absent-by-default, so a caller that
    cannot decide knows it cannot, rather than inheriting a guess.
    """

    status: int | None = None
    code: str = ""              # error.code, normalised to a lowercase token
    type: str = ""              # error.type
    param: str = ""             # error.param (which field the provider rejected)
    limit_source: str = ""      # error.metadata.limit_source — WHICH limit refused the request
    engine: str = ""            # what the ENGINE found: one of ENGINE_CLASSES
    structured: bool = False    # an error OBJECT was present and decoded
    tokens: tuple[int | None, int | None] = field(default=(None, None))

    @property
    def declared(self) -> tuple[str, ...]:
        """The enum tokens this failure declared, in precedence order."""
        return tuple(t for t in (self.code, self.type, self.param) if t)


def _token(value: Any) -> str:
    """One declared field as a comparable token, or "" — numbers (OpenRouter sends `code: 402`)
    and nested objects are not enum tokens.
    """
    return value.strip().lower() if isinstance(value, str) else ""


def _error_object(body: Any) -> dict:
    """The error object in a decoded body, whichever envelope the vendor wraps it in."""
    if isinstance(body, dict):
        for key in ("error", "detail"):
            inner = body.get(key)
            if isinstance(inner, dict):
                return inner
        if any(k in body for k in ("code", "type", "message")):
            return body
    return {}


def _declared_token(text: str) -> str:
    """A declared enum token appearing BARE in an unstructured body (a proxy 503 answering
    `auth_unavailable`), or "". Only a token one of the tables above carries is accepted: this
    matches the providers' own vocabulary, not their prose.
    """
    known = NEVER_OVERFLOW_CODES | OVERFLOW_CODES
    return next((m.group(1) for m in _SNAKE_RE.finditer(text.lower())
                 if m.group(1) in known), "")


def _tokens_stated(text: str) -> tuple[int | None, int | None]:
    """(actual, maximum) token figures the text STATES, either side None when it does not."""
    pair = TOKEN_PAIR_RE.search(text)
    if pair:
        return int(pair.group(1)), int(pair.group(2))
    found = TOKEN_MAX_RE.search(text)
    return None, (int(found.group(1)) if found else None)


def from_body(status: int | None, text: str, *, engine: str = "") -> ErrorSignal:
    """The signal for a response, read from the WHOLE body at the raise.

    It must be taken here: the raised message carries only `resp.text[:300]`, which can miss
    an error object that comes after the prose.
    """
    body: Any = None
    opens = _JSON_RE.search(text or "")
    if opens is not None:
        try:
            body = json.loads(text[opens.start():])
        except (ValueError, TypeError):
            body = None
    err = _error_object(body)
    raw_meta = err.get("metadata")
    meta: dict = raw_meta if isinstance(raw_meta, dict) else {}
    code = _token(err.get("code")) or _declared_token(text if not err else "")
    return ErrorSignal(
        status=status, code=code, type=_token(err.get("type")), param=_token(err.get("param")),
        limit_source=_token(meta.get("limit_source")), engine=engine,
        structured=bool(err), tokens=_tokens_stated(text))


def from_text(text: str) -> ErrorSignal:
    """The signal recoverable from an error's rendered TEXT — the fallback for an EndpointError
    that carries no signal. Weaker than `from_body`: the text may be a 300-character excerpt.
    """
    found = _STATUS_RE.search(text or "")
    return from_body(int(found.group(1)) if found else None, text or "")


def of(exc: Any) -> ErrorSignal:
    """The signal an exception declares, or the best reading of its text. One accessor, so no
    site has to know whether the error came from an adapter or was constructed by the engine.
    """
    signal = getattr(exc, "signal", None)
    return signal if isinstance(signal, ErrorSignal) else from_text(str(exc) if exc else "")


def is_overflow(signal: ErrorSignal) -> bool:
    """Whether this failure says the INPUT DID NOT FIT — the window guard's precondition.

    Order matters and each step is a declaration, never a sentence:
    1. an enum token that is never a context fault (a quota, a balance, a credential) → no;
    2. a status that is never a context fault → no;
    3. an enum token that names a context fault → yes, whatever the message says;
    4. otherwise: a structured 400/413 that STATES a token figure → yes (an unknown vendor,
       covered by the shape of its answer).
    An unstructured body is declined at step 4: a false positive is the worse error.
    """
    declared = signal.declared
    if any(t in NEVER_OVERFLOW_CODES for t in declared) or signal.limit_source:
        return False
    if signal.status in NEVER_OVERFLOW_STATUSES:
        return False
    if any(t in OVERFLOW_CODES for t in declared):
        return True
    return bool(signal.structured and signal.status in OVERFLOW_STATUSES
                and any(n is not None for n in signal.tokens))


def overflow_decline_reason(signal: ErrorSignal) -> str:
    """WHY a context-fault reading was declined: `never_overflow_status` (a declared non-context
    fault), `no_structured_error` (nothing declared anything), or `no_hint_match` (declared, but
    nothing says the input did not fit).
    """
    if (any(t in NEVER_OVERFLOW_CODES for t in signal.declared) or signal.limit_source
            or signal.status in NEVER_OVERFLOW_STATUSES):
        return "never_overflow_status"
    return "no_hint_match" if signal.structured else "no_structured_error"


def failover_class(signal: ErrorSignal) -> str:
    """The failure's class for the health stream: `rate_limit`, `auth`, `refusal`, `empty`,
    `server`, or `other` when nothing declared enough to say — an honest bucket, never a guess.
    """
    if signal.engine:
        return ENGINE_CLASSES.get(signal.engine, "other")
    declared = signal.declared
    if any(t in RATE_LIMIT_CODES for t in declared):
        return "rate_limit"
    if any(t in AUTH_CODES for t in declared):
        return "auth"
    status = signal.status
    if status in (401, 403):
        return "auth"
    if status == 429:
        return "rate_limit"
    if status is not None and (status >= 500 or status == 408):
        return "server"
    return "other"


#: Statuses that mean "the request never got a verdict", so sending it again can work: 408 (a
#: proxy's dropped upstream stream — a transient, not worth a model switch), 409, 429. Every 5xx
#: joins them in `retryable`.
RETRYABLE_STATUSES = frozenset({408, 409, 429})


def retry_after_seconds(headers: Any) -> float | None:
    """The server's Retry-After hint in SECONDS, or None. Only the numeric-seconds form is
    honoured; an HTTP-date (rare, skew-prone) or a non-positive/unparseable value → None.
    """
    raw = headers.get("retry-after") if hasattr(headers, "get") else None
    if not raw:
        return None
    try:
        secs = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return secs if secs > 0 else None


def retryable(signal: ErrorSignal) -> bool:
    """Whether the request never got a verdict, so sending it again can work."""
    status = signal.status or 0
    return status in RETRYABLE_STATUSES or status >= 500 or retryable_402(signal)


def retryable_402(signal: ErrorSignal) -> bool:
    """Whether a 402 never got a verdict, so sending it again can work.

    Credit reserved against in-flight requests (declared by `limit_source` or a rate-limit
    token) clears by waiting; a billing token means the account cannot pay. With NOTHING
    structured, retryable: a wrong retry costs two backoff sleeps, a wrong fatal kills the run.
    """
    if signal.status != 402:
        return False
    declared = signal.declared
    if any(t in BILLING_CODES for t in declared):
        return False
    return bool(signal.limit_source or any(t in RATE_LIMIT_CODES for t in declared)
                or not signal.structured)
