"""What a failed call DECLARED about itself — the one structural reading of a provider error.

R3108, increment two, decided by the operator on 2026-10-17: *"Good idea! I want you to replace
the prose checks completely"*. Three places decided retry, failover and prompt-window policy by
matching ENGLISH SENTENCES in a provider's error body — `endpoints/base.py`'s `transient_402`,
`engine/overflow.py`'s `_OVERFLOW_HINTS`/`_NEVER_OVERFLOW_STATUS`, `engine/degrade.py`'s
`_FAILOVER_REASONS`. All three now ask this module instead, and the phrase lists are gone.

THE DISTINCTION THIS MODULE RESTS ON. A provider's `error.code` / `error.type` / `error.param` /
`error.metadata.limit_source` are a DECLARED ENUM: the provider commits to the token, documents
it, and does not reword it per locale or per release. Its `message` is prose for a human. Reading
the enum is structural; reading the sentence is guessing, and the guess failed in both directions:

* 2026-09-21 — the Anthropic overflow shape matched none of the four openai-flavoured hints, so
  `self-audit:20260921-000321` 400'd four times with its prompt growing 1,017,305 → 1,045,385
  while the engine read every rejection as a broken provider and failed over. A phrase list is
  disarmed for every vendor whose wording nobody has added yet, and a miss produces no failure —
  only a wrong answer.
* 2026-10-16 — conversation `c-20261016-075101` died twice in 30 s at `model_chain_exhausted …
  after 0 rungs`, because a 402 whose body did not carry the one sentence the rule looked for was
  read as fatal while the condition cleared itself.

WHY THE ENUM TABLES ARE NOT THE OLD PROBLEM IN NEW CLOTHES. A phrase list had to carry every
vendor's wording or the guard was off. These tables carry what is KNOWN, and the fallback under
them is structural, not prose: a 400/413 that is structured and states a token figure with an
exceeds relation is an overflow whatever enum it names (`is_overflow`), and a failure's class
falls back to its HTTP status (`failover_class`). A vendor nobody has met is therefore covered by
the shape of its answer, and a new enum token is an improvement, never the difference between
armed and disarmed.

WHAT IS STRUCTURAL ABOUT A BODY WITH NO JSON. Proxies in this instance's chain (CLIProxy /
OpenRouter-style) sometimes relay an upstream failure as a bare body — the reason
`endpoints/failure_hints.py` exists. Two readings survive that: the HTTP status, and a DECLARED
ENUM TOKEN appearing bare in the text (`auth_unavailable` on a proxy 503). `_declared_token`
accepts a snake_case token only when it is in one of the tables below — it matches the providers'
own vocabulary, never an English sentence — and `structured` stays False so a caller can tell the
difference. Where that leaves a decision genuinely undecidable, each site states which way it
fails and why; `engine/errorclass.py` counts those rows.
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

#: Declared enum tokens that are NEVER a context fault, however their message is worded. A rate
#: limit, a credit balance and an auth failure all quote token counts, and reading one as an
#: overflow is worse than missing an overflow: it pins the run's window to a number that was
#: never a context size — a per-minute budget, a plan ceiling — for every remaining turn, while
#: suppressing the failover the real fault needed.
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

#: What the ENGINE itself found, declared on the error at the raise instead of written into a
#: sentence and read back out of it. `_FAILOVER_REASONS` matched the engine's own vocabulary
#: ("refused the turn", "empty completion") in whatever prose arrived, so a provider quoting
#: those words was filed as a classifier refusal.
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

    This is where the structured data has to be taken: `base.py`'s `raise_for_status` renders only
    `resp.text[:300]` into the message it raises, so a provider that puts its error object after
    300 characters of prose reaches no decision point through the text at all (increment one's
    `error_keys_in_excerpt` field was measuring exactly that loss).
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
    that carries no signal (a hand-built one, or one from a path that predates this module).
    Weaker than `from_body` by construction: the text may be a 300-character excerpt.
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
    4. otherwise: a structured 400/413 that STATES a token figure → yes. This is the vendor
       nobody has met yet, covered by the shape of its answer rather than by its wording.
    An unstructured body stops at step 4 and is declined: the false positive pins the run's
    window to a figure that was never a context size, which `overflow.py` records as the worse
    of the two errors. `engine/errorclass.py` counts those declines.
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
    """WHY a context-fault reading was declined, for the measurement increment one shipped:
    `never_overflow_status` (a declared non-context fault — the guard working as designed),
    `no_structured_error` (nothing declared anything — the operator's reportable path), or
    `no_hint_match` (declared, but nothing in it says the input did not fit).
    """
    if (any(t in NEVER_OVERFLOW_CODES for t in signal.declared) or signal.limit_source
            or signal.status in NEVER_OVERFLOW_STATUSES):
        return "never_overflow_status"
    return "no_hint_match" if signal.structured else "no_structured_error"


def failover_class(signal: ErrorSignal) -> str:
    """The failure's class for the health stream: `rate_limit`, `auth`, `refusal`, `empty`,
    `server`, or `other` when nothing declared enough to say. The vocabulary is unchanged — only
    the basis is. `other` stays an honest bucket: a guess that makes a new failure mode look like
    a known one is what this replaces.
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


#: Statuses that mean "the request never got a verdict", so sending it again can work: 408
#: request timeout — a proxy answers it when its own upstream stream drops, and calling that
#: fatal spent a MODEL SWITCH on a transient — 409 conflict, 429 rate limit. Every 5xx joins
#: them in `retryable` (529 rides that branch). Moved here from `base.py` with R3108: which
#: failures never got a verdict is the same classification question as the rest of this module,
#: and that file is at the house size bar.
RETRYABLE_STATUSES = frozenset({408, 409, 429})


def retry_after_seconds(headers: Any) -> float | None:
    """The server's Retry-After hint in SECONDS, or None (F220). Honors the numeric-seconds form
    (what OpenAI-compatible providers send on a 429, e.g. `Retry-After: 20`); an HTTP-date form is
    ignored — rare here, and clock skew makes it unreliable — so the generic backoff applies
    instead. A non-positive or unparseable value → None. Moved from `base.py` with R3108: it is
    one more thing the failure STATED, and that file is at the house size bar.
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

    A provider answers 402 for two conditions. Credit the account HAS, reserved against requests
    already in the air, is declared by `error.metadata.limit_source` (OpenRouter) or by a declared
    rate-limit token: waiting clears it. An account that cannot pay declares a billing token or
    nothing but its own message, and waiting changes nothing.

    With NOTHING structured the honest answer is retryable, and the asymmetry is measured: fatal
    cost conversation `c-20261016-075101` two deaths in 30 s at 0 rungs while the condition
    cleared itself, where retryable costs two backoff sleeps before it fails exactly as before.
    """
    if signal.status != 402:
        return False
    declared = signal.declared
    if any(t in BILLING_CODES for t in declared):
        return False
    return bool(signal.limit_source or any(t in RATE_LIMIT_CODES for t in declared)
                or not signal.structured)
