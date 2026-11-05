"""R3108 increment two: retry, window and failover policy decided STRUCTURALLY, not from prose.

Increment one (`tests/test_error_class_undecided.py`) counted how often the three prose lists had
no structural basis for a decision they made anyway. This increment replaces them, as the
operator decided on 2026-10-17: *"Good idea! I want you to replace the prose checks completely"*
— the routine that filed it proposed keeping prose as a fallback and he overrode that.

What each site asks instead is `endpoints/errorsignal.py`: the HTTP status, and the provider's own
`error.code` / `error.type` / `error.param` / `error.metadata.limit_source` decoded from the body.

The four specimens below are the shapes that cost something, and each test names the wrong answer
the phrase lists gave it:

* a BILLING error whose message happens to say "maximum context length" — `_OVERFLOW_HINTS`
  matched it and nothing in `_NEVER_OVERFLOW_STATUS` excluded it, so the run's window would be
  pinned to a number that was never a context size (the failure mode `overflow.py`'s own comment
  warns about, from the other direction);
* a vendor overflow worded in none of the four openai-flavoured phrases — the 2026-09-21 shape,
  which is why that list had to grow a phrase after `self-audit:20260921-000321` 400'd four times
  with its prompt growing 1,017,305 → 1,045,385. A list that has to be extended per vendor is
  disarmed for every vendor not yet seen;
* a provider body QUOTING the engine's own sentences ("refused the turn", "empty completion") —
  `_FAILOVER_REASONS` read the engine's vocabulary back out of a stranger's prose;
* a 402 relayed with no JSON at all — fatal under the prose rule, which is how conversation
  `c-20261016-075101` died twice in 30 s at `after 0 rungs` while the condition cleared itself.
"""

from __future__ import annotations

import pytest

from rsched.endpoints import errorsignal
from rsched.endpoints.base import EndpointError, raise_for_status
from rsched.engine import degrade, overflow

#: A billing cap whose MESSAGE carries overflow vocabulary. `error.code` says what it is.
BILLING_400 = ('prov: HTTP 400: {"error":{"code":"billing_hard_limit_reached","message":'
               '"maximum context length is 8192 tokens for your current plan"}}')
#: A real context overflow in words no phrase list carries — `error.type` and the stated pair.
STRUCTURED_OVERFLOW_400 = ('claude: HTTP 400: {"type":"error","error":{"type":'
                           '"string_above_max_length","message":"input exceeds the maximum: '
                           '1200000 tokens > 1000000"}}')
#: A provider failure whose prose quotes the engine's own refusal wording.
MIMIC_500 = ('prov: HTTP 500: {"error":{"message":"the model refused the turn and returned an '
             'empty completion","type":"server_error"}}')
#: A 402 a proxy relayed as a bare body: no error object, no remedy sentence, nothing to match.
PROXY_402 = "payment required"


class _Resp:
    """The attributes `raise_for_status` reads, and nothing else."""

    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text
        self.headers: dict[str, str] = {}


def test_a_billing_cap_quoting_context_words_is_not_an_overflow() -> None:
    """`error.code` is a declared enum and says `billing_hard_limit_reached`. Under the phrase
    list this 400 WAS an overflow, so the window guard would shrink the run to 8,192 tokens on
    the strength of a sentence about a payment plan."""
    assert overflow._is_overflow_text(BILLING_400) is False
    assert overflow.parse_overflow_limit(BILLING_400) is None


def test_a_vendor_overflow_no_phrase_covers_is_still_an_overflow() -> None:
    """The 2026-09-21 mode, structurally: `error.type` names the fault and the message states
    the pair. None of the four hint phrases appear, so the phrase list missed it entirely."""
    assert overflow._is_overflow_text(STRUCTURED_OVERFLOW_400) is True
    assert overflow.parse_overflow_pair(STRUCTURED_OVERFLOW_400) == (1_200_000, 1_000_000)


def test_a_provider_quoting_the_engines_own_words_is_classed_by_its_status() -> None:
    """`_FAILOVER_REASONS` matched the engine's OWN sentences — "refused the turn", "empty
    completion" — in whatever prose arrived, so a 500 that mentioned them was filed as a
    classifier refusal. A 500 is a server fault whatever its body says."""
    assert degrade._failover_reason(EndpointError(MIMIC_500)) == "server"


def test_the_engine_declares_its_own_failure_class() -> None:
    """The two classes that are not the provider's at all: a classifier refusal and a run of
    empty completions are the ENGINE's findings. It declares them on the error instead of
    writing a sentence and reading it back."""
    refused = EndpointError("m refused the turn (stop_reason=refusal)",
                            signal=errorsignal.ErrorSignal(engine="refusal"))
    empty = EndpointError("empty completion x2",
                          signal=errorsignal.ErrorSignal(engine="empty"))
    assert degrade._failover_reason(refused) == "refusal"
    assert degrade._failover_reason(empty) == "empty"


def test_a_402_with_nothing_structured_is_retryable() -> None:
    """The live defect's cost, generalised. A 402 is either credit held against requests in the
    air (waiting clears it) or an account that cannot pay; with no error object the body says
    neither, and the measured cost of guessing FATAL was a conversation dying twice at 0 rungs,
    against two backoff sleeps for guessing retryable — after which it fails exactly as before."""
    with pytest.raises(EndpointError) as exc:
        raise_for_status(_Resp(402, PROXY_402), "proxy")
    assert exc.value.retryable
    assert exc.value.signal.structured is False


def test_a_402_declaring_a_credit_limit_is_retryable_and_one_without_stays_fatal() -> None:
    """The two live OpenRouter shapes, decided on `error.metadata.limit_source` — the field by
    which the provider says the refusal came from a LIMIT it imposes on credit the account has,
    rather than from the account's balance. Same verdicts as the prose rule, no sentence read."""
    limited = ('{"error":{"message":"would exceed your available credits","code":402,'
               '"metadata":{"limit_source":"openrouter_credits"}}}')
    broke = ('{"error":{"message":"This request requires more credits, or fewer max_tokens. '
             'You requested up to 16384 tokens, but can only afford 9590","code":402}}')
    with pytest.raises(EndpointError) as transient:
        raise_for_status(_Resp(402, limited), "OpenRouter")
    assert transient.value.retryable
    with pytest.raises(EndpointError) as fatal:
        raise_for_status(_Resp(402, broke), "OpenRouter")
    assert not fatal.value.retryable


def test_the_signal_survives_a_body_the_error_text_truncated() -> None:
    """Why the signal is attached at the raise and not re-parsed downstream: `raise_for_status`
    renders only `resp.text[:300]` into the message, so a provider that puts its error object
    after 300 characters of prose reaches no decision point at all through the text."""
    body = '{"error":{"message":"%s","code":"context_length_exceeded"}}' % ("x" * 400)
    with pytest.raises(EndpointError) as exc:
        raise_for_status(_Resp(400, body), "prov")
    assert exc.value.signal.code == "context_length_exceeded"
    assert errorsignal.from_text(str(exc.value)).code == ""
