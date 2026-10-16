"""A 402 that says "wait" is a transient, and a 402 that says "add credits" is not.

OpenRouter answers 402 for two different conditions and the bodies say which:

* *"This request would exceed your available credits given your current in-flight requests.
  **Retry after in-flight requests settle**, or add credits."* — the account HAS the credit;
  the provider is reserving it against requests already in the air. Waiting clears it.
* *"This request requires more credits, or fewer max_tokens. You requested up to 16384 tokens,
  but can only afford 9590."* — the account does not have the credit. Waiting changes nothing.

`RETRYABLE_STATUSES` held neither, so both were fatal. On 2026-10-16 the first shape killed the
live conversation `c-20261016-075101` TWICE inside 30 seconds (11:17:51, 11:18:21), each time
with `model_chain_exhausted … after 0 rungs`: no retry, no failover, the run simply ended while
the condition was clearing itself. The second shape had failed `sprind:20260818-093527` the same
way, correctly.

So the discriminator is the body, not the status — the same judgment `base.py` already makes for
408, and the reason these cases live beside each other in one file.
"""

from __future__ import annotations

import pytest

from rsched.endpoints.base import EndpointError, raise_for_status

CONCURRENCY_402 = (
    '{"error":{"message":"This request would exceed your available credits given your '
    'current in-flight requests. Retry after in-flight requests settle, or add credits.",'
    '"code":402,"metadata":{"limit_source":"openrouter_credits"}}}')

OUT_OF_CREDIT_402 = (
    '{"error":{"message":"This request requires more credits, or fewer max_tokens. You '
    'requested up to 16384 tokens, but can only afford 9590. To increase, visit '
    'https://openrouter.ai/settings/credits and add more credits","code":402}}')


class _Resp:
    """The two attributes `raise_for_status` reads, and nothing else."""

    def __init__(self, status_code: int, text: str):
        self.status_code = status_code
        self.text = text
        self.headers: dict[str, str] = {}


def test_a_402_about_in_flight_requests_is_retryable() -> None:
    """The live defect: the provider itself names waiting as the remedy, so the request never
    got a verdict and sending it again can work. Fatal here costs the whole run at 0 rungs."""
    with pytest.raises(EndpointError) as exc:
        raise_for_status(_Resp(402, CONCURRENCY_402), "OpenRouter")
    assert exc.value.retryable, "a 402 that says 'retry after in-flight requests settle' is transient"
    assert not exc.value.auth


def test_a_402_about_insufficient_credit_stays_fatal() -> None:
    """The symmetric half, and the reason the discriminator is the body: an account that cannot
    afford the request will not afford it one second later. Retrying it burns the budget three
    times and still fails — `sprind:20260818-093527` failed on exactly this body."""
    with pytest.raises(EndpointError) as exc:
        raise_for_status(_Resp(402, OUT_OF_CREDIT_402), "OpenRouter")
    assert not exc.value.retryable, "an out-of-credit 402 must not be retried"


def test_the_other_classifications_are_unchanged() -> None:
    """The edit touches one branch of a classifier every adapter calls, so the neighbours are
    pinned in the same place: auth stays auth, the retryable set stays retryable, 200 raises
    nothing, and an unrelated 4xx stays fatal."""
    for status in (401, 403):
        with pytest.raises(EndpointError) as exc:
            raise_for_status(_Resp(status, "nope"), "prov")
        assert exc.value.auth and not exc.value.retryable
    for status in (408, 409, 429, 500, 503, 529):
        with pytest.raises(EndpointError) as exc:
            raise_for_status(_Resp(status, "later"), "prov")
        assert exc.value.retryable
    with pytest.raises(EndpointError) as exc:
        raise_for_status(_Resp(404, "gone"), "prov")
    assert not exc.value.retryable and not exc.value.auth
    assert raise_for_status(_Resp(200, "ok"), "prov") is None
