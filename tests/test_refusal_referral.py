"""Refusal clarification (engine/refusal.py, operator order 2026-08-22): a refusal is
FLAGGED (a `refusal` transcript event), its trigger ISOLATED via a schema'd subcall, and
ONLY the isolated fragment referred to the routine's `uncensored` HARNESS — whose replies
are diagnostic evidence, never answers (the harness only pretends to comply). Covers the
detector (marker fast-path may only CONFIRM; the LLM classification subcall decides the
misses — a marker list alone was ruled unreliable) and the `do_llm` seam.
"""

from __future__ import annotations

from types import SimpleNamespace

from rsched.config import ModelRef
from rsched.endpoints.base import Completion, EndpointError
from rsched.engine import refusal
from rsched.engine.executor import do_llm

REFUSAL = "I'm sorry, but I can't help with that request."
SOFT_REFUSAL = "My principles around dangerous chemistry prevent me from assisting here."
ANSWER = "Sure — here is the information you asked for: the capital of France is Paris."
ISOLATION = {"isolated": "synthesize the compound", "isolated_kind": "step",
             "rationale": "the synthesis step is the only hazardous part"}


class _ScriptedEndpoint:
    """Serves scripted completions in order (the last repeats); records every call's tail
    message so tests can assert WHAT was sent — the fragment-only guarantee."""

    def __init__(self, completions):
        self._completions = list(completions)
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


def _c(text="", parsed=None):
    return Completion(text=text, parsed=parsed, usage={"in": 1, "out": 1})


class _Registry:
    def __init__(self, tool_ep, unc_ep=None):
        self.tool_ep, self.unc_ep = tool_ep, unc_ep

    def for_model(self, kind, models):
        assert kind == "tool_call"
        return self.tool_ep, ModelRef("tool-ep", "tool-model", name="tool-cat")

    def for_uncensored(self, models):
        if self.unc_ep is None:
            return None
        return self.unc_ep, ModelRef("unc-ep", "unc-model", name="honeypot")


class _Transcript:
    def __init__(self):
        self.events = []

    def event(self, type_, payload, **kw):
        self.events.append((type_, payload))


def _ctx(registry):
    return SimpleNamespace(registry=registry, routine=SimpleNamespace(models={}),
                           add_usage=lambda u: None, referrals=0,
                           transcript=_Transcript())


# --- the detector ------------------------------------------------------------------

def test_marker_fast_path_confirms_without_a_subcall():
    tool = _ScriptedEndpoint([_c(parsed={"refusal": False})])   # a verdict that would deny
    assert refusal.is_refusal(_ctx(_Registry(tool)), REFUSAL) is True
    assert tool.calls == 0                       # the fast path never asked it


def test_classification_decides_what_markers_miss():
    # the reliability a marker list cannot give (operator, 2026-08-22): no marker matches
    # this decline, the schema'd verdict still catches it — and clears a genuine answer.
    # The verdict is the HARNESS's own job, rendered by the tool_call model (operator,
    # 2026-08-22: "if the harness already figured out that it's a refusal then why does
    # the honeypot's opinion matter at all?"). The honeypot is the delivery TARGET, never
    # the judge — no uncensored role need be configured for detection to work.
    tool = _ScriptedEndpoint([_c(parsed={"refusal": True})])
    assert refusal.is_refusal(_ctx(_Registry(tool)), SOFT_REFUSAL) is True
    assert SOFT_REFUSAL[:40] in tool.prompts[0]
    tool2 = _ScriptedEndpoint([_c(parsed={"refusal": False})])
    assert refusal.is_refusal(_ctx(_Registry(tool2)), ANSWER) is False


def test_classification_unavailable_counts_as_answer():
    # the tool_call classifier is always resolvable, so detection no longer hinges on a
    # honeypot being configured — but if that classification subcall itself is down, an
    # unconfirmed reply best-effort degrades to "answer" (never a false refusal).
    tool = _ScriptedEndpoint([EndpointError("down")])
    assert refusal.is_refusal(_ctx(_Registry(tool)), SOFT_REFUSAL) is False
    tool2 = _ScriptedEndpoint([EndpointError("down")])
    assert refusal.is_refusal(_ctx(_Registry(tool2)), "") is False   # empty short-circuits


# --- the clarification pipeline ----------------------------------------------------

def test_clarify_isolates_and_refers_only_the_fragment():
    tool = _ScriptedEndpoint([_c(parsed=ISOLATION)])
    unc = _ScriptedEndpoint([_c(text="Certainly! Here is how. (pretend compliance)")])
    ctx = _ctx(_Registry(tool, unc))
    task = "step 1: buy glassware. step 2: synthesize the compound. step 3: publish."
    record = refusal.clarify_refusal(ctx, task=task, refusal=REFUSAL, where="llm",
                                     model="tool-cat")
    assert record["isolated"] == "synthesize the compound"
    assert record["isolated_kind"] == "step"
    assert record["referred"] is True and record["harness_model"] == "honeypot"
    assert "pretend" in record["harness_reply"]
    assert unc.prompts == ["synthesize the compound"]   # the essence ONLY — never the task
    assert task[:40] in tool.prompts[0] and REFUSAL[:20] in tool.prompts[0]
    assert ctx.referrals == 1
    assert ctx.transcript.events == [("refusal", record)]   # exactly one flag event


def test_clarify_without_uncensored_still_flags_and_isolates():
    tool = _ScriptedEndpoint([_c(parsed=ISOLATION)])
    ctx = _ctx(_Registry(tool, None))
    record = refusal.clarify_refusal(ctx, task="t", refusal=REFUSAL, where="loop")
    assert record["isolated"] == "synthesize the compound"
    assert record["referred"] is False and "no uncensored model" in record["harness_note"]
    assert ctx.referrals == 0
    assert ctx.transcript.events[0][0] == "refusal"


def test_clarify_isolation_failure_sends_nothing():
    # junk isolation → nothing is sent: the honeypot receives ONLY the essence of the
    # refusal trigger (operator, 2026-08-22), so a failed isolation cannot fall back to
    # sending more of the task
    tool = _ScriptedEndpoint([_c(text="no structured isolation here")])
    unc = _ScriptedEndpoint([_c(text="unused")])
    ctx = _ctx(_Registry(tool, unc))
    record = refusal.clarify_refusal(ctx, task="secret task", refusal=REFUSAL, where="llm")
    assert "isolation" in record["isolation_error"] and "isolated" not in record
    assert record["referred"] is False and "no isolated essence" in record["harness_note"]
    assert unc.calls == 0
    assert ctx.transcript.events[0][0] == "refusal"       # flagged regardless


def test_clarify_isolation_endpoint_error_sends_nothing():
    tool = _ScriptedEndpoint([EndpointError("boom")])
    unc = _ScriptedEndpoint([_c(text="unused")])
    ctx = _ctx(_Registry(tool, unc))
    record = refusal.clarify_refusal(ctx, task="t", refusal=REFUSAL, where="loop")
    assert "boom" in record["isolation_error"]
    assert record["referred"] is False and unc.calls == 0


def test_clarify_harness_endpoint_error_recorded():
    tool = _ScriptedEndpoint([_c(parsed=ISOLATION)])
    unc = _ScriptedEndpoint([EndpointError("harness down")])
    ctx = _ctx(_Registry(tool, unc))
    record = refusal.clarify_refusal(ctx, task="t", refusal=REFUSAL, where="llm")
    assert record["referred"] is False and "harness down" in record["harness_error"]
    assert ctx.referrals == 0


# --- the do_llm seam ---------------------------------------------------------------

def test_do_llm_refusal_splits_essence_to_harness_remainder_to_primary():
    """The split (operator, 2026-08-22): the honeypot receives ONLY the essence of the
    refusal trigger; everything ELSE goes back to the primary model with the essence
    factored out — no refusal danger — and that answer serves the observation."""
    # order on tool: 1) primary call → refusal, 2) isolation subcall, 3) remainder call
    tool = _ScriptedEndpoint([_c(text=REFUSAL), _c(parsed=ISOLATION),
                              _c(text="Everything else is done.")])
    unc = _ScriptedEndpoint([_c(text="Of course! (pretend compliance)")])
    ctx = _ctx(_Registry(tool, unc))
    out = do_llm({"kind": "llm", "prompt": "please synthesize the compound", "say": "s"},
                 ctx)
    assert unc.prompts == ["synthesize the compound"]   # the harness sees the essence ONLY
    assert out["reply"] == "Everything else is done."   # the remainder, from the PRIMARY
    assert out["remainder_processed"] is True
    assert out["model"] == "tool-model"                 # never re-attributed to the harness
    assert "referred" not in out                        # the old substitution key is gone
    assert out["refusal"]["isolated"] == "synthesize the compound"
    assert out["refusal"]["referred"] is True
    assert tool.prompts[2] == "please [this part is handled separately]"   # sanitized
    assert ctx.referrals == 1


def test_do_llm_answer_is_not_clarified():
    # the answer has no marker, so the classify subcall runs and CLEARS it (operator
    # 2026-08-22: classification is the HARNESS's job, on the tool_call model — the
    # honeypot is the delivery target, never the judge). No clarification, no delivery.
    # The default serving role IS tool_call, so tool serves the reply then the verdict.
    tool = _ScriptedEndpoint([_c(text=ANSWER), _c(parsed={"refusal": False})])
    unc = _ScriptedEndpoint([_c(text="unused")])
    ctx = _ctx(_Registry(tool, unc))
    out = do_llm({"kind": "llm", "prompt": "capital of France?", "say": "s"}, ctx)
    assert "refusal" not in out and unc.calls == 0   # the honeypot was never consulted
    assert tool.calls == 2                           # reply + classify verdict
    assert ctx.transcript.events == []


def test_do_llm_structured_reply_never_clarified():
    tool = _ScriptedEndpoint([_c(text=REFUSAL, parsed={"ok": True})])
    unc = _ScriptedEndpoint([_c(text="unused")])
    ctx = _ctx(_Registry(tool, unc))
    out = do_llm({"kind": "llm", "prompt": "p", "say": "s"}, ctx)
    assert "refusal" not in out and unc.calls == 0 and tool.calls == 1


def test_do_llm_explicit_harness_probe_not_clarified():
    unc = _ScriptedEndpoint([_c(text=REFUSAL)])
    ctx = _ctx(_Registry(_ScriptedEndpoint([]), unc))
    out = do_llm({"kind": "llm", "prompt": "p", "model": "uncensored", "say": "s"}, ctx)
    assert out["reply"] == REFUSAL and "refusal" not in out
    assert unc.calls == 1                          # the caller's own probe, once
