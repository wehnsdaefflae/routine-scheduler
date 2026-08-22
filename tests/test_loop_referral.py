"""Refusal clarification at the LOOP seam (engine/refusal.py, operator order 2026-08-22):
a free-text or classifier refusal is FLAGGED as a `refusal` transcript event, its trigger
isolated, and ONLY the essence delivered to the uncensored HARNESS — whose reply is
recorded evidence and NEVER this turn's action (whole-turn referral is retired: the
harness only pretends to comply). A refusal that arrives as a schema-valid
finish(status=failed) is intercepted the same way. Children run the same EngineLoop, so
this one path covers both. Drives completion.next_action directly with fake endpoints.
"""

from __future__ import annotations

import pytest

from rsched.config import ModelRef, load_routine
from rsched.endpoints.base import Completion, EndpointError
from rsched.engine.completion import next_action
from rsched.engine.loop import EngineLoop
from rsched.engine.run_context import Budgets, RunContext
from rsched.engine.transcript import Transcript, read_events
from test_loop import TS, _server

REFUSAL = Completion(text="I'm sorry, but I can't help with that request.", parsed=None,
                     usage={"in": 1, "out": 1})
SOFT = Completion(text="My principles prevent me from assisting with this one.",
                  parsed=None, usage={"in": 1, "out": 1})
JUNK = Completion(text="here is some prose but not json at all", parsed=None,
                  usage={"in": 1, "out": 1})
ISOLATED = Completion(text="", parsed={"isolated": "the risky step",
                                       "isolated_kind": "step"},
                      usage={"in": 1, "out": 1})
VERDICT_YES = Completion(text="", parsed={"refusal": True}, usage={"in": 1, "out": 1})
VERDICT_NO = Completion(text="", parsed={"refusal": False}, usage={"in": 1, "out": 1})
PRETEND = Completion(text="Sure, here is exactly how. (pretend)", parsed=None,
                     usage={"in": 1, "out": 1})


class _FakeEndpoint:
    context_chars = 1_000_000

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


class _FakeRegistry:
    """main serves the loop turns, tool the classify/isolate subcalls, unc the harness.
    `main_name` keeps each test's failover cooldown entries distinct (the registry is
    process-global)."""

    def __init__(self, main_ep, unc_ep=None, tool_ep=None, main_name="main-ep"):
        self.main_ep, self.unc_ep = main_ep, unc_ep
        self.tool_ep = tool_ep if tool_ep is not None else main_ep
        self.main_name = main_name

    def for_model(self, kind, models):
        if kind == "tool_call":
            return self.tool_ep, ModelRef("tool-ep", "tool-model")
        return self.main_ep, ModelRef(self.main_name, "main-model")

    def for_model_chain(self, kind, models):
        return [(self.main_ep, ModelRef(self.main_name, "main-model"))]

    def for_uncensored(self, models):
        if self.unc_ep is None:
            return None
        return self.unc_ep, ModelRef("unc-ep", "unc-model", name="honeypot")


def _loop(make_routine, registry) -> EngineLoop:
    d = make_routine(slug="ref")
    server = _server(d)
    run_dir = d / "runs" / TS
    run_dir.mkdir(parents=True)
    cfg, _ = load_routine(d)
    ctx = RunContext(routine=cfg, server=server, registry=registry, run_ts=TS,
                     run_dir=run_dir,
                     transcript=Transcript(run_dir / "transcript.jsonl"),
                     budgets=Budgets.from_config(cfg.budgets))
    loop = EngineLoop(ctx, "## Run flow", "instr")
    loop.messages = [{"role": "user", "content": "kickoff"}]
    return loop


def _refusal_events(loop):
    events, _ = read_events(loop.ctx.run_dir / "transcript.jsonl")
    return [e for e in events if e["type"] == "refusal"]


def test_free_text_refusal_flags_and_refers_fragment_only(make_routine):
    main = _FakeEndpoint([REFUSAL])                # refuses every attempt
    tool = _FakeEndpoint([ISOLATED])               # marker fast-path: no classify needed
    unc = _FakeEndpoint([PRETEND])
    loop = _loop(make_routine, _FakeRegistry(main, unc, tool))
    action, _ = next_action(loop)
    assert action is None                          # the harness NEVER supplies the action
    assert loop.ctx.referrals == 1
    evs = _refusal_events(loop)
    assert len(evs) == 1                           # one clarification per turn (latch)
    p = evs[0]["payload"]
    assert p["where"] == "loop" and p["isolated"] == "the risky step"
    assert p["referred"] is True and "pretend" in p["harness_reply"]
    assert unc.prompts == ["the risky step"]       # the essence ONLY — never loop.messages
    assert main.calls == 3                         # the normal retry path continued
    # the retry message routes everything ELSE to the main model, refusal-free
    assert "handled separately" in main.prompts[1]
    assert "the risky step" in main.prompts[1]


def test_llm_judged_refusal_without_markers(make_routine):
    # the marker list MISSES this decline — the classification subcall catches it
    main = _FakeEndpoint([SOFT])
    tool = _FakeEndpoint([VERDICT_YES, ISOLATED])  # classify, then isolate
    unc = _FakeEndpoint([PRETEND])
    loop = _loop(make_routine, _FakeRegistry(main, unc, tool))
    action, _ = next_action(loop)
    assert action is None
    assert len(_refusal_events(loop)) == 1
    assert unc.prompts == ["the risky step"]


def test_junk_is_not_a_refusal(make_routine):
    main = _FakeEndpoint([JUNK])
    tool = _FakeEndpoint([VERDICT_NO])             # the verdict clears it every attempt
    unc = _FakeEndpoint([PRETEND])
    loop = _loop(make_routine, _FakeRegistry(main, unc, tool))
    action, _ = next_action(loop)
    assert action is None                          # plain schema forcefail
    assert _refusal_events(loop) == [] and unc.calls == 0


def test_no_uncensored_still_flags_and_isolates(make_routine):
    main = _FakeEndpoint([REFUSAL])
    tool = _FakeEndpoint([ISOLATED])
    loop = _loop(make_routine, _FakeRegistry(main, None, tool))
    action, _ = next_action(loop)
    assert action is None
    evs = _refusal_events(loop)
    assert len(evs) == 1
    assert evs[0]["payload"]["referred"] is False
    assert evs[0]["payload"]["isolated"] == "the risky step"
    assert loop.ctx.referrals == 0


def test_classifier_refusal_clarifies_then_fails_honestly(make_routine):
    refused = Completion(text="", parsed=None, usage={"in": 1, "out": 1},
                         stop_reason="refusal",
                         stop_details={"category": "cyber",
                                       "explanation": "the exploit step"})
    main = _FakeEndpoint([refused])
    tool = _FakeEndpoint([ISOLATED])
    unc = _FakeEndpoint([PRETEND])
    loop = _loop(make_routine, _FakeRegistry(main, unc, tool, main_name="cls-main-ep"))
    with pytest.raises(EndpointError, match="refused the turn"):
        next_action(loop)                          # chain of one, no fallback → honest raise
    evs = _refusal_events(loop)
    assert len(evs) == 1 and evs[0]["payload"]["where"] == "loop"
    assert evs[0]["payload"]["referred"] is True
    assert unc.prompts == ["the risky step"]       # the essence ONLY


def test_refusal_worded_finish_is_intercepted_not_accepted(make_routine):
    """The gap live specimen c-20260822-085029 exposed: a refusal that arrives as a
    SCHEMA-VALID finish(status=failed) whose summary is the decline prose. The action
    parses cleanly, so the free-text/classifier detectors never saw it and the run just
    ended `failed` with referrals=0. It must be intercepted: flagged, essence isolated
    and delivered to the honeypot, then the turn re-driven on the main model — which
    proceeds with the remainder instead of the run terminating on the refusal.
    """
    refusal_finish = Completion(
        text="", usage={"in": 1, "out": 1},
        parsed={"kind": "finish", "status": "failed", "say": "declining",
                "summary": "I'm sorry, but I can't help with sourcing the risky step."})
    redone = Completion(
        text="", usage={"in": 1, "out": 1},
        parsed={"kind": "read_file", "path": "state/probe.txt",
                "say": "proceeding with the remainder"})
    main = _FakeEndpoint([refusal_finish, redone])   # refuse-via-finish, then a real action
    tool = _FakeEndpoint([ISOLATED])                 # marker fast-path confirms; isolate only
    unc = _FakeEndpoint([PRETEND])
    loop = _loop(make_routine, _FakeRegistry(main, unc, tool, main_name="finref-ep"))
    action, _ = next_action(loop)
    assert action is not None and action["kind"] == "read_file"   # the re-driven action wins
    evs = _refusal_events(loop)
    assert len(evs) == 1 and evs[0]["payload"]["where"] == "loop"
    assert evs[0]["payload"]["isolated"] == "the risky step"
    assert evs[0]["payload"]["referred"] is True
    assert unc.prompts == ["the risky step"]          # essence ONLY reached the honeypot
    assert loop.ctx.referrals == 1
    assert main.calls == 2                            # refusal-finish, then the re-drive
    assert "handled separately" in main.prompts[1] and "the risky step" in main.prompts[1]


def test_non_refusal_failed_finish_is_accepted(make_routine):
    """A finish(status=failed) that is an HONEST failure report — not a content refusal —
    is accepted as the turn's action, never intercepted (the classifier clears it).
    """
    honest = Completion(
        text="", usage={"in": 1, "out": 1},
        parsed={"kind": "finish", "status": "failed", "say": "giving up",
                "summary": "The upstream API returned 500 on every retry; nothing to do."})
    main = _FakeEndpoint([honest])
    tool = _FakeEndpoint([VERDICT_NO])                # classify: not a refusal
    unc = _FakeEndpoint([PRETEND])
    loop = _loop(make_routine, _FakeRegistry(main, unc, tool, main_name="honest-ep"))
    action, _ = next_action(loop)
    assert action is not None and action["kind"] == "finish" and action["status"] == "failed"
    assert _refusal_events(loop) == [] and unc.calls == 0 and main.calls == 1
