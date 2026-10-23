"""Refusal takeover units (engine/refusal.py, operator order 2026-10-16): engage,
window arithmetic, the uncensored dead end, and the `llm` seam's wholesale re-issue.
The honeypot-era units (essence isolation, harness delivery of a fragment) are retired
with the premise.
"""

from __future__ import annotations

from helpers import run_context
from rsched.config import ModelRef
from rsched.endpoints.base import Completion, EndpointError
from rsched.engine import refusal
from rsched.engine.llmaction import do_llm

REFUSAL = "I'm sorry, but I can't help with that request."
ANSWER_TEXT = "Here is the answer you asked for."


def test_marker_shortcut_matches_curly_apostrophe():
    # Models type the apostrophe as U+2019; REFUSAL_MARKERS are typed straight. Both
    # spellings must hit the zero-cost shortcut, and folding must not invent refusals.
    assert refusal.looks_like_refusal("I\u2019m sorry, but I can\u2019t help with that.")
    assert refusal.looks_like_refusal(REFUSAL)
    assert not refusal.looks_like_refusal("Here\u2019s the summary you asked for.")


def test_marker_shortcut_folds_apostrophe_variants():
    # The other apostrophe spellings the fold covers, each read as the straight one.
    for apos in ("\u2018", "\u02bc", "\uff07"):
        assert refusal.looks_like_refusal(f"I{apos}m sorry, but I can{apos}t help with that.")


class _Ep:
    context_tokens = 1_000_000

    def __init__(self, completions):
        self._completions = list(completions)
        self.prompts = []

    @property
    def calls(self):
        return len(self.prompts)

    def complete(self, messages, **kw):
        self.prompts.append(messages[-1]["content"])
        c = self._completions[min(len(self.prompts) - 1, len(self._completions) - 1)]
        if isinstance(c, EndpointError):
            raise c
        return c


class _Reg:
    def __init__(self, unc_ep=None, unc_name="GLM uncensored", broken=False):
        self.unc_ep = unc_ep
        self.unc_name = unc_name
        self.broken = broken

    def for_uncensored(self, models, efforts):
        if self.unc_ep is None:
            return None
        if self.broken:
            raise EndpointError("catalog entry left the catalog")
        return self.unc_ep, ModelRef("nano gpt", "glm-unc", name=self.unc_name)

    def for_model(self, kind, models, efforts):
        raise EndpointError("no model")

    def for_model_chain(self, kind, models, efforts):
        return [(self, ModelRef("x", "y"))]


def _ctx(make_routine, registry, **kw):
    d = make_routine(slug="refunit")
    # run_context signature comes from helpers; mirror what test_loop_referral used
    from rsched.engine.loop import EngineLoop
    from test_loop import TS, _server
    loop = EngineLoop(run_context(d, TS, server=_server(d), registry=registry),
                      "## Run flow", "instr")
    loop.messages = [{"role": "user", "content": "task"}]
    return loop.ctx


def test_engage_escalates_and_arms_window(make_routine):
    ctx = _ctx(make_routine, _Reg(_Ep([Completion(text=ANSWER_TEXT)])))
    ctx.refusal_level = 0
    record, target = refusal.engage_takeover(
        ctx, refusal=REFUSAL, where="loop", model="main-model")
    assert record["takeover"] is True
    assert record["level"] == 1 and record["window"] == 1
    assert ctx.refusal_level == 1 and ctx.takeover_left == 0
    assert ctx.referrals == 1
    assert target is not None


def test_engage_doubles_window_per_level(make_routine):
    ctx = _ctx(make_routine, _Reg(_Ep([])))
    for level, window in ((1, 1), (2, 2), (3, 4), (4, 8), (5, 16), (6, 16), (7, 16)):
        ctx.refusal_level = level - 1
        record, _ = refusal.engage_takeover(ctx, refusal=REFUSAL, where="loop")
        assert record["window"] == window, (level, window)
        assert ctx.takeover_left == max(0, window - 1)


def test_engage_without_uncensored_records_and_returns_none(make_routine):
    ctx = _ctx(make_routine, _Reg(None))
    record, target = refusal.engage_takeover(ctx, refusal=REFUSAL, where="loop")
    assert target is None
    assert record["takeover"] is False
    assert "no uncensored model configured" in record["takeover_error"]
    # no episode escalation when nothing can take over
    assert ctx.refusal_level == 0


def test_engage_uncensored_refusing_model_is_dead_end(make_routine):
    """The refusing model IS the uncensored model: no takeover of itself, no rung."""
    unc_ep = _Ep([])
    ctx = _ctx(make_routine, _Reg(unc_ep))
    record, target = refusal.engage_takeover(
        ctx, refusal=REFUSAL, where="loop", model="main-model",
        refusing=("nano gpt", "glm-unc"))
    assert target is None
    assert record["takeover"] is False
    assert "dead end" in record["note"]
    assert ctx.refusal_level == 0


def test_engage_broken_uncensored_resolution_recorded(make_routine):
    ctx = _ctx(make_routine, _Reg(_Ep([]), broken=True))
    record, target = refusal.engage_takeover(ctx, refusal=REFUSAL, where="loop")
    assert target is None
    assert record["takeover"] is False
    assert "catalog" in record["takeover_error"]


def test_takeover_turn_serves_and_consumes(make_routine):
    ctx = _ctx(make_routine, _Reg(_Ep([Completion(text=ANSWER_TEXT)])))
    ctx.takeover_left = 3
    target = refusal.takeover_turn(ctx)
    assert target is not None
    assert ctx.takeover_left == 2
    assert ctx.referrals == 1
    ctx.takeover_left = 0
    assert refusal.takeover_turn(ctx) is None


def test_takeover_turn_closes_window_when_unresolvable(make_routine):
    ctx = _ctx(make_routine, _Reg(None))
    ctx.takeover_left = 2
    assert refusal.takeover_turn(ctx) is None
    assert ctx.takeover_left == 0      # the window closed, not skipped


def test_llm_refused_call_is_reissued_whole_on_uncensored(make_routine):
    """The `llm` seam: a refused sub-call is re-issued WHOLE - same messages - and the
    uncensored reply IS the observation's answer. The turn episode is untouched."""
    refuse = _Ep([Completion(text=REFUSAL)])
    unc = _Ep([Completion(text=ANSWER_TEXT)])

    class _LLMReg(_Reg):
        def __init__(self, main_ep, unc_ep):
            super().__init__(unc_ep)
            self.main_ep = main_ep

        def for_model(self, kind, models, efforts):
            return self.main_ep, ModelRef("main-ep", "main-model")

    d = make_routine(slug="refllm")
    from rsched.engine.loop import EngineLoop
    from test_loop import TS, _server
    loop = EngineLoop(run_context(d, TS, server=_server(d),
                                  registry=_LLMReg(refuse, unc)),
                      "## Run flow", "instr")
    ctx = loop.ctx
    action = {"kind": "llm", "prompt": "the full question", "say": "ask"}
    out = do_llm(action, ctx)
    assert out["takeover"] is True
    assert out["reply"] == ANSWER_TEXT
    assert out["model"] == "glm-unc"
    assert unc.prompts == ["the full question"]     # WHOLE request, not an essence
    assert out["refusal"]["takeover"] is True
    assert ctx.takeover_left == 0                    # a sub-call is not a turn


def test_llm_refused_without_uncensored_keeps_refusal(make_routine):
    refuse = _Ep([Completion(text=REFUSAL)])
    d = make_routine(slug="refllm2")
    from rsched.engine.loop import EngineLoop
    from test_loop import TS, _server
    class _MainReg(_Reg):
        def __init__(self, main_ep):
            super().__init__(None)
            self.main_ep = main_ep

        def for_model(self, kind, models, efforts):
            return self.main_ep, ModelRef("main-ep", "main-model")

    ctx = EngineLoop(run_context(d, TS, server=_server(d),
                                 registry=_MainReg(refuse)),
                     "## Run flow", "instr").ctx
    out = do_llm({"kind": "llm", "prompt": "q", "say": "ask"}, ctx)
    assert "takeover" not in out
    assert out["reply"] == REFUSAL
    assert out["refusal"]["takeover"] is False
