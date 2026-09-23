"""The token estimate is calibrated against what the provider ACTUALLY counted.

`estimate_input_tokens` is 3.5 UTF-8 bytes per token — a packing guess — and the compaction
gates compare it against a real window. Running light meant the gate never fired and the
turn died at the provider instead (self-audit 20260921-000321: four `prompt is too long`
400s in one run). Every completion already reports the true prompt size, so the ratio is
measured on the turn that came back and applied to the next one.
"""

from __future__ import annotations

from rsched.config import ModelRef
from rsched.endpoints.base import Completion
from rsched.engine.compaction import estimate_input_tokens
from rsched.engine.completion import next_action
from rsched.engine.window import compact_if_needed, note_prompt_size
from test_loop_referral import _FakeEndpoint, _FakeRegistry, _loop

REF = ModelRef("calib-ep", "calib-model", context_tokens=200_000, max_tokens=1_000)


class _CalibRegistry(_FakeRegistry):
    """Serves REF for the run's turns — a big honest window, so the only thing that can
    move the compaction gate in these tests is the measured ratio."""

    def for_model(self, kind, models):
        if kind == "tool_call":
            return super().for_model(kind, models)
        return self.main_ep, REF

    def for_model_chain(self, kind, models):
        return [(self.main_ep, REF)]


def _loop_with_prompt(make_routine, ep=None, *, messages: int = 40, body: int = 10_000):
    """A run whose prompt has a compactable middle and sits well under the uncached 0.6
    gate BY ESTIMATE — the state a run was in when the provider rejected it at 1.4x."""
    loop = _loop(make_routine, _CalibRegistry(ep if ep is not None else _FakeEndpoint([])))
    loop.messages = [{"role": "user" if i % 2 else "assistant", "content": "x" * body}
                     for i in range(messages)]
    # not at a stage boundary: anticipatory compaction would tighten the gate by 0.85 on the
    # first call only, and the ratio is what these tests are measuring
    loop._last_seen_phase = loop.ctx.phase
    # …and the WINDOW is the gate under test, not the token budget's own ">10% of what is
    # left per turn" cap (the fixture routine allows 100k tokens in total)
    loop.ctx.budgets.max_total_tokens = -1
    return loop


def test_estimate_alone_leaves_the_gate_shut(make_routine):
    loop = _loop_with_prompt(make_routine)
    loop._schema_off = True
    assert 100_000 < estimate_input_tokens(loop.messages) < 120_000
    compact_if_needed(loop, None, REF)
    assert loop._evict_warned is False


def test_reported_prompt_size_shrinks_the_run_local_window(make_routine):
    loop = _loop_with_prompt(make_routine)
    loop._schema_off = True
    # the provider counted ~1.4x what we estimated — the tokenizer, not the message list
    note_prompt_size(loop, REF, {"in": 100_000, "cached_in": 50_000, "cache_write": 10_000})
    assert 1.3 < loop._token_ratio < 1.5
    compact_if_needed(loop, None, REF)
    # the same prompt now reads as what the provider will count, so the run is warned a turn
    # BEFORE the middle goes, instead of a 400 arriving first
    assert loop._evict_warned is True


def test_a_short_prompt_never_writes_a_ratio(make_routine):
    """The framing a provider counts and the message list does not is roughly FIXED, so
    `reported / estimate` explodes on a short prompt. Only a prompt already occupying a
    fifth of the window calibrates — the only turns where the correction decides anything.
    """
    loop = _loop_with_prompt(make_routine, messages=1, body=2)
    note_prompt_size(loop, REF, {"in": 900})
    assert loop._token_ratio == 1.0


def test_a_heavy_estimate_and_a_wild_reading_are_both_clamped(make_routine):
    loop = _loop_with_prompt(make_routine)
    loop._schema_off = True
    note_prompt_size(loop, REF, {"in": 60_000})          # we over-estimated: nothing to fix
    assert loop._token_ratio == 1.0
    note_prompt_size(loop, REF, {"in": 900_000})         # not a tokenizer difference
    assert loop._token_ratio == 2.0


def test_the_action_schema_is_subtracted_before_dividing(make_routine):
    """The provider counts the forced schema in the prompt; `loop.messages` never carries
    it. Dividing by the raw reported figure would read that fixed cost as tokenizer drift.
    """
    loop = _loop_with_prompt(make_routine)
    loop._schema_off = True
    note_prompt_size(loop, REF, {"in": 160_000})
    without_schema = loop._token_ratio
    loop._schema_off = False
    note_prompt_size(loop, REF, {"in": 160_000})
    assert loop._token_ratio < without_schema


def test_the_ratio_is_measured_on_every_turn(make_routine):
    """It rides the live turn, not a special path: one completion, one measurement."""
    ep = _FakeEndpoint([Completion(text="", parsed={"kind": "finish", "status": "ok",
                                                    "summary": "s" * 200, "say": "done"},
                                   usage={"in": 160_000, "out": 10})])
    loop = _loop_with_prompt(make_routine, ep)
    action, _usage = next_action(loop)
    assert action["kind"] == "finish"
    assert loop._token_ratio > 1.2
