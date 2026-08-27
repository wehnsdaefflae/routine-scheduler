"""F278: the window guard. A provider 400 stating a SMALLER context window than the
catalog claims means the model config lies — the guard parses the stated maximum,
shrinks the run-local window, re-clamps and retries the same model exactly once.
Drives completion.next_action directly with fake endpoints (no network)."""

from __future__ import annotations

import pytest

from rsched.config import ModelRef
from rsched.endpoints.base import Completion, EndpointError
from rsched.engine.compaction import messages_size
from rsched.engine.completion import next_action
from rsched.engine.window import parse_overflow_limit
from test_loop_referral import _FakeEndpoint, _loop

VALID = Completion(text="", parsed={"kind": "read_file", "path": "state/probe.txt",
                                    "say": "reading"}, usage={"in": 1, "out": 1})
# The provider vocabulary observed live on 2026-08-05 (nano-gpt, openai-compatible).
OVERFLOW = ("This model's maximum context length is 65536 tokens. However, you requested "
            "81746 tokens (65362 in the messages, 16384 in the completion). Please reduce "
            "the length of the messages or completion. (context_length_exceeded)")


class _Registry:
    """A chain of one model whose CATALOG window (1M chars = 250k tokens) exceeds the
    provider's real 65,536 — the exact F278 configuration."""

    def __init__(self, ep):
        self.ep = ep

    def for_model(self, kind, models):
        return self.ep, ModelRef("main-ep", "main-model",
                                 context_chars=1_000_000, max_tokens=16_384)

    def for_model_chain(self, kind, models):
        return [self.for_model(kind, models)]

    def for_uncensored(self, models):
        return None


def test_parse_overflow_limit_variants():
    assert parse_overflow_limit(OVERFLOW) == 65536
    assert parse_overflow_limit("request exceeds the context window of 32768 tokens") == 32768
    assert parse_overflow_limit("boom 401 unauthorized") is None          # not an overflow
    assert parse_overflow_limit("context_length_exceeded") is None        # no stated figure


def test_overflow_shrinks_window_and_retries_same_model(make_routine):
    ep = _FakeEndpoint([EndpointError(OVERFLOW), VALID])
    loop = _loop(make_routine, _Registry(ep))
    loop.messages.append({"role": "user", "content": "x" * 400_000})
    action, _usage = next_action(loop)
    assert action["kind"] == "read_file"      # the corrected retry produced the turn
    assert ep.calls == 2                      # one overflow + one clean retry, same model
    # run-local correction: 65,536 tokens × 4 chars — re-applied on every later pick
    assert loop._window_overrides[("main-ep", "main-model")] == 262_144
    # the prompt was re-clamped under the corrected hard ceiling (49,152 tok × 3.5 chars)
    assert messages_size(loop.messages) <= 172_032
    # audit trail: transcript compaction event + the health event naming the bad entry
    assert "window_guard" in (loop.ctx.run_dir / "transcript.jsonl").read_text()
    health = (loop.ctx.server.routines_home / ".control" / "health-events.jsonl")
    assert "model_window_corrected" in health.read_text()


def test_second_overflow_never_loops(make_routine):
    ep = _FakeEndpoint([EndpointError(OVERFLOW)])   # keeps overflowing after correction
    loop = _loop(make_routine, _Registry(ep))
    with pytest.raises(EndpointError):
        next_action(loop)
    assert ep.calls == 2   # original + the one corrected retry; then honest failure


def test_stated_max_at_or_above_config_declines(make_routine):
    # 250,000 stated tokens × 4 = the configured 1M chars — config was not the problem;
    # the guard must decline and let the ordinary nets fail the run honestly.
    ep = _FakeEndpoint([EndpointError("maximum context length is 250000 tokens")])
    loop = _loop(make_routine, _Registry(ep))
    with pytest.raises(EndpointError):
        next_action(loop)
    assert ep.calls == 1
    assert getattr(loop, "_window_overrides", {}) == {}


def test_non_overflow_error_is_untouched(make_routine):
    ep = _FakeEndpoint([EndpointError("boom 500 internal")])
    loop = _loop(make_routine, _Registry(ep))
    with pytest.raises(EndpointError):
        next_action(loop)
    assert ep.calls == 1
    assert getattr(loop, "_window_overrides", {}) == {}
