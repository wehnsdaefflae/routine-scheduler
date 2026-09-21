"""A too-long prompt is a fault of the REQUEST, not of the model.

Live specimen (self-audit:20260921-000321, the run this test exists because of): the same
turn 400'd four times with a prompt that GREW each time — 1,017,305 → 1,025,019 →
1,038,220 → 1,045,385 tokens against a 1,000,000 maximum — and each 400 advanced the
fallback chain and cooled a perfectly healthy model for 300 s. Every other model would
have received the identical oversize prompt.

So: parse the provider's own numbers whatever the vendor's wording, shrink on the SAME
model, never mark it failed, and if it still does not fit, fail the turn saying so.
Drives completion.next_action directly with fake endpoints (no network).
"""

from __future__ import annotations

import pytest

from rsched.config import ModelRef
from rsched.endpoints import failover
from rsched.endpoints.base import Completion, EndpointError
from rsched.engine.compaction import estimate_input_tokens
from rsched.engine.completion import next_action
from rsched.engine.window import parse_overflow_limit
from test_loop_referral import _FakeEndpoint, _loop

VALID = Completion(text="", parsed={"kind": "read_file", "path": "state/probe.txt",
                                    "say": "reading"}, usage={"in": 1, "out": 1})

# Anthropic's wording, verbatim from the dead run's transcript.
ANTHROPIC_400 = ('claude-proxy: HTTP 400: {"type":"error","error":{"type":'
                 '"invalid_request_error","message":"prompt is too long: 1045385 tokens '
                 '> 1000000 maximum"}}')


class _ChainRegistry:
    """A two-member chain whose head's catalog window MATCHES the provider's real
    maximum — the F278 guard's "config was not lying" case, which is exactly the case
    the live failure fell through."""

    def __init__(self, head_ep, tail_ep, name="oversize-ep"):
        self.head_ep, self.tail_ep, self.name = head_ep, tail_ep, name

    def for_model(self, kind, models):
        return self.head_ep, ModelRef(self.name, "head-model", name="Head",
                                      context_tokens=1_000_000, max_tokens=16_384)

    def for_model_chain(self, kind, models):
        return [self.for_model(kind, models),
                (self.tail_ep, ModelRef(self.name + "-b", "tail-model", name="Tail",
                                        context_tokens=1_000_000, max_tokens=16_384))]

    def for_uncensored(self, models):
        return None


@pytest.fixture(autouse=True)
def _clean_cooldowns():
    failover.reset()
    yield
    failover.reset()


def test_parse_overflow_limit_reads_the_anthropic_wording():
    """The proximate defect: none of the hint strings appear in Anthropic's message, so
    the whole window guard was disarmed for the endpoint serving this instance."""
    assert parse_overflow_limit(ANTHROPIC_400) == 1_000_000


def test_parse_overflow_limit_keeps_the_openai_wording():
    """The vendor shapes coexist — this is the F278 specimen and must not regress."""
    openai = ("This model's maximum context length is 65536 tokens. However, you "
              "requested 81746 tokens. (context_length_exceeded)")
    assert parse_overflow_limit(openai) == 65_536
    assert parse_overflow_limit("boom 401 unauthorized") is None


def test_an_oversize_prompt_never_advances_the_fallback_chain(make_routine):
    """The operator's point: another model gets the SAME prompt. Failing over cannot
    help, and a model with a smaller window would answer from a truncated context."""
    head = _FakeEndpoint([EndpointError(ANTHROPIC_400), VALID])
    tail = _FakeEndpoint([VALID])
    loop = _loop(make_routine, _ChainRegistry(head, tail))
    loop.messages.append({"role": "user", "content": "x" * 5_000_000})
    action, _usage = next_action(loop)
    assert action["kind"] == "read_file"
    assert tail.calls == 0, "the fallback model must never see an oversize prompt"
    assert head.calls == 2, "the same model is retried once with a shrunken prompt"


def test_an_oversize_prompt_never_cools_a_healthy_model(make_routine):
    """`_switch_to_fallback` marks the abandoned model failed for 300 s. The model did
    nothing wrong — the engine over-composed the prompt — and every other run resolving
    in that window paid for it."""
    head = _FakeEndpoint([EndpointError(ANTHROPIC_400), VALID])
    tail = _FakeEndpoint([VALID])
    loop = _loop(make_routine, _ChainRegistry(head, tail, name="cool-probe"))
    loop.messages.append({"role": "user", "content": "x" * 5_000_000})
    next_action(loop)
    assert not failover.is_cooling("cool-probe", "head-model")


def test_the_retry_prompt_is_smaller_than_the_one_that_was_refused(make_routine):
    """The live loop's signature was a prompt that GREW across attempts (1,017,305 →
    1,045,385). Recovery must shrink the request it re-sends, not merely re-send it."""
    head = _FakeEndpoint([EndpointError(ANTHROPIC_400), VALID])
    tail = _FakeEndpoint([VALID])
    loop = _loop(make_routine, _ChainRegistry(head, tail, name="shrink-probe"))
    loop.messages.append({"role": "user", "content": "x" * 5_000_000})
    next_action(loop)
    assert head.calls == 2
    assert len(head.prompts[1]) < len(head.prompts[0])
    # and it is under the provider's STATED maximum, not merely smaller
    assert estimate_input_tokens(loop.messages) <= 1_000_000


def test_an_unshrinkable_prompt_fails_loudly_instead_of_failing_over(make_routine):
    """When the floor itself exceeds the window there is nothing to shrink. The honest
    outcome is a turn that dies naming the size — not a silent degrade down the chain."""
    head = _FakeEndpoint([EndpointError(ANTHROPIC_400)])   # keeps refusing
    tail = _FakeEndpoint([VALID])
    loop = _loop(make_routine, _ChainRegistry(head, tail, name="floor-probe"))
    loop.messages.append({"role": "user", "content": "x" * 5_000_000})
    with pytest.raises(EndpointError) as exc:
        next_action(loop)
    assert "too long" in str(exc.value).lower() or "prompt" in str(exc.value).lower()
    assert tail.calls == 0


def test_a_provider_fault_still_fails_over(make_routine):
    """The control, and the other half of the operator's question — a 429/5xx IS a fault
    of the endpoint, another model genuinely can serve it, and failover must still fire."""
    head = _FakeEndpoint([EndpointError("codex-proxy: HTTP 429: usage_limit_reached")])
    tail = _FakeEndpoint([VALID])
    loop = _loop(make_routine, _ChainRegistry(head, tail, name="provider-probe"))
    action, _usage = next_action(loop)
    assert action["kind"] == "read_file"
    assert tail.calls == 1, "a genuine provider fault still advances the chain"
    assert failover.is_cooling("provider-probe", "head-model")
