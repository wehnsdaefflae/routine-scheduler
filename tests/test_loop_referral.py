"""Refusal takeover at the LOOP seam (engine/refusal.py, operator order 2026-10-16):
a free-text or classifier refusal is FLAGGED as a `refusal` transcript event and the
refused TURN is re-driven WHOLE on the routine's uncensored model - its output IS the
turn's action (the honeypot premise - isolate an essence, deliver only it, discard the
reply - is retired). A refusal that arrives as a schema-valid finish(status=failed) is
intercepted the same way. Every consecutive regular-model refusal doubles the takeover
window: 1 -> 2 -> 4 -> 8 -> 16 turns (TAKEOVER_CAP), then the next request hands back to
the regular model; a clean regular turn resets the episode. Children run the same
EngineLoop, so this one path covers both. Drives completion.next_action directly with
fake endpoints.
"""

from __future__ import annotations

import json

from conftest import finish
from helpers import run_context
from rsched.config import ModelRef
from rsched.endpoints.base import Completion, EndpointError
from rsched.engine.completion import next_action
from rsched.engine.loop import EngineLoop
from rsched.engine.transcript import read_events
from test_loop import TS, _server

REFUSAL = Completion(text="I'm sorry, but I can't help with that request.", parsed=None,
                     usage={"in": 1, "out": 1})
VERDICT_YES = Completion(text="", parsed={"refusal": True}, usage={"in": 1, "out": 1})
CLASSIFIER_REFUSAL = Completion(text="", stop_reason="refusal",
                                stop_details={"category": "bio"}, usage={"in": 1, "out": 1})


def _as_completion(item):
    """A dict becomes an ACTION: serialized as JSON text (parsed by the loop), so a
    scripted reply can be a valid turn action via conftest's helpers."""
    if isinstance(item, dict):
        return Completion(text=json.dumps(item), usage={"in": 1, "out": 1})
    return item


class _FakeEndpoint:
    context_tokens = 1_000_000

    def __init__(self, completions):
        self._completions = [_as_completion(c) for c in completions]
        self.prompts: list[str] = []

    @property
    def calls(self) -> int:
        return len(self.prompts)

    def complete(self, messages, **kw):
        self.prompts.append(messages[-1]["content"])
        c = self._completions[min(len(self.prompts) - 1, len(self._completions) - 1)]
        if isinstance(c, EndpointError):
            raise c
        return c


class _FakeRegistry:
    """main serves the loop turns, tool the classify subcalls, unc the takeover model.
    `main_name` keeps each test's failover cooldown entries distinct (the registry is
    process-global)."""

    def __init__(self, main_ep, unc_ep=None, tool_ep=None, main_name="main-ep"):
        self.main_ep, self.unc_ep = main_ep, unc_ep
        self.tool_ep = tool_ep if tool_ep is not None else main_ep
        self.main_name = main_name

    def for_model(self, kind, models, efforts):
        if kind == "tool_call":
            return self.tool_ep, ModelRef("tool-ep", "tool-model")
        return self.main_ep, ModelRef(self.main_name, "main-model")

    def for_model_chain(self, kind, models, efforts):
        return [(self.main_ep, ModelRef(self.main_name, "main-model"))]

    def for_uncensored(self, models, efforts):
        if self.unc_ep is None:
            return None
        return self.unc_ep, ModelRef("unc-ep", "unc-model", name="uncensored-model")


def _loop(make_routine, registry, slug="ref") -> EngineLoop:
    d = make_routine(slug=slug)
    loop = EngineLoop(run_context(d, TS, server=_server(d), registry=registry),
                      "## Run flow", "instr")
    loop.messages = [{"role": "user", "content": "kickoff"}]
    return loop


def _refusal_events(loop):
    events, _ = read_events(loop.ctx.run_dir / "transcript.jsonl")
    return [e for e in events if e["type"] == "refusal"]


def test_free_text_refusal_is_taken_over_whole(make_routine):
    """A prose refusal arms the episode and the uncensored model re-drives the SAME turn:
    its reply IS the action - the whole request, not an excised essence."""
    main = _FakeEndpoint([REFUSAL])                # refuses every attempt
    tool = _FakeEndpoint([VERDICT_YES])            # classify: it IS a refusal
    unc = _FakeEndpoint([finish(summary="served by the uncensored model")])
    loop = _loop(make_routine, _FakeRegistry(main, unc, tool))
    action, _ = next_action(loop)
    assert action["kind"] == "finish"              # the uncensored reply IS the action
    assert loop.ctx.referrals == 1
    evs = _refusal_events(loop)
    assert len(evs) == 1                           # one flag per turn (latch)
    p = evs[0]["payload"]
    assert p["where"] == "loop" and p["takeover"] is True
    assert p["takeover_model"] == "uncensored-model"
    assert p["level"] == 1 and p["window"] == 1
    assert unc.prompts[0] == "kickoff"             # the WHOLE context went to it
    assert main.calls == 1                         # refused once, never re-asked


def test_classifier_refusal_is_taken_over_whole(make_routine):
    """A classifier stop (stop_reason=refusal) takes the same takeover path: flag,
    escalate, re-drive the turn on the uncensored model."""
    main = _FakeEndpoint([CLASSIFIER_REFUSAL])
    unc = _FakeEndpoint([finish(summary="served by the uncensored model")])
    loop = _loop(make_routine, _FakeRegistry(main, unc, main))
    action, _ = next_action(loop)
    assert action["kind"] == "finish"
    assert loop.ctx.referrals == 1
    evs = _refusal_events(loop)
    assert evs[0]["payload"]["takeover"] is True
    assert evs[0]["payload"]["window"] == 1
    assert unc.calls == 1


def test_backoff_windows_double_per_consecutive_refusal(make_routine):
    """Every consecutive regular-model refusal doubles the window; the refused request
    is the window's first turn (left = window - 1); the episode lives on the run ctx.
    Levels 1-5 window 1, 2, 4, 8, 16; past 5 the window holds at the cap."""
    for level, window in ((1, 1), (2, 2), (3, 4), (4, 8), (5, 16), (6, 16)):
        main = _FakeEndpoint([CLASSIFIER_REFUSAL])
        unc = _FakeEndpoint([finish()])
        loop = _loop(make_routine, _FakeRegistry(main, unc, main,
                                                 main_name=f"bo{level}"),
                      slug=f"bo{level}")
        loop.ctx.refusal_level = level - 1         # the episode so far
        action, _ = next_action(loop)
        assert action["kind"] == "finish"
        assert loop.ctx.refusal_level == level
        assert loop.ctx.takeover_left == window - 1
        evs = _refusal_events(loop)
        assert evs[0]["payload"]["window"] == window


def test_clean_regular_turn_resets_episode(make_routine):
    """A turn the REGULAR chain completes cleanly resets refusal_level to 0, so the
    next refusal starts at window 1 again (the hand-back proved it can serve)."""
    main = _FakeEndpoint([finish(summary="served by the regular model")])
    unc = _FakeEndpoint([finish()])
    loop = _loop(make_routine, _FakeRegistry(main, unc, main))
    loop.ctx.refusal_level = 3                    # a prior episode
    action, _ = next_action(loop)
    assert action["kind"] == "finish"
    assert loop.ctx.refusal_level == 0
    assert unc.calls == 0                         # the regular model served it


def test_takeover_window_serves_next_turns(make_routine):
    """While a window is open (takeover_left > 0) the uncensored model serves the NEXT
    turn too, consuming one turn of it; after it closes the regular model is back."""
    main = _FakeEndpoint([CLASSIFIER_REFUSAL, finish(summary="regular again")])
    unc = _FakeEndpoint([finish(summary="unc window"), finish(summary="unc window 2")])
    loop = _loop(make_routine, _FakeRegistry(main, unc, main, main_name="win"))
    action, _ = next_action(loop)                 # refusal -> level 1, window 1, left 0
    assert action["kind"] == "finish"
    loop.ctx.takeover_left = 3                    # as if level 3's window were open
    action, _ = next_action(loop)
    assert action["kind"] == "finish"
    assert loop.ctx.takeover_left == 2             # consumed one
    assert unc.calls == 2 and main.calls == 1     # unc served this turn, not main


def test_window_closes_then_regular_serves_again(make_routine):
    """With the window empty the regular model serves the next request - the hand-back."""
    main = _FakeEndpoint([CLASSIFIER_REFUSAL, finish(summary="regular again")])
    unc = _FakeEndpoint([finish(summary="unc window")])
    loop = _loop(make_routine, _FakeRegistry(main, unc, main, main_name="handback"))
    next_action(loop)                             # refusal -> window 1 (left 0)
    assert loop.ctx.refusal_level == 1
    action, _ = next_action(loop)                 # left == 0 -> regular serves
    assert action["kind"] == "finish"
    assert main.calls == 2 and unc.calls == 1


def test_no_uncensored_fails_run_honestly(make_routine):
    """No uncensored role configured: the takeover cannot serve, the fallback chain
    advances as before, and a chain of one ends the run honestly with the category."""
    import pytest

    main = _FakeEndpoint([CLASSIFIER_REFUSAL])
    loop = _loop(make_routine, _FakeRegistry(main, None, main, main_name="nonep"))
    with pytest.raises(EndpointError, match="no usable fallback model"):
        next_action(loop)
    evs = _refusal_events(loop)
    assert evs[0]["payload"]["takeover"] is False


def test_uncensored_model_refusal_is_honest_dead_end(make_routine):
    """The UNCENSORED model itself refusing mid-takeover is an honest dead end: the
    takeover chain is its own (a chain of one here), so the run dies naming the
    refusal - never a back-off rung, never a ping-pong back to the regular model."""
    import pytest

    main = _FakeEndpoint([CLASSIFIER_REFUSAL])
    unc = _FakeEndpoint([CLASSIFIER_REFUSAL])
    loop = _loop(make_routine, _FakeRegistry(main, unc, main, main_name="uncdead"))
    with pytest.raises(EndpointError):
        next_action(loop)
    evs = _refusal_events(loop)
    assert evs[0]["payload"]["takeover"] is True  # the main refusal WAS taken over
