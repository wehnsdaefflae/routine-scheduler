"""D8 scope C: the main/subroutine turn loop refers a free-text refusal to the routine's
optional `uncensored` model. Subroutines run the same EngineLoop, so this one path covers both.
Drives EngineLoop._next_action directly with fake endpoints (no network)."""

from __future__ import annotations

from rsched.config import ModelRef, load_routine
from rsched.endpoints.base import Completion, EndpointError
from rsched.engine.loop import EngineLoop
from rsched.engine.run_context import Budgets, RunContext
from rsched.engine.transcript import Transcript
from test_loop import TS, _server

REFUSAL = Completion(text="I'm sorry, but I can't help with that request.", parsed=None,
                     usage={"in": 1, "out": 1})
VALID = Completion(text="", parsed={"kind": "read_file", "path": "state/probe.txt",
                                    "say": "reading"}, usage={"in": 1, "out": 1})
JUNK = Completion(text="here is some prose but not json at all", parsed=None,
                  usage={"in": 1, "out": 1})


class _FakeEndpoint:
    context_chars = 1_000_000

    def __init__(self, completions):
        self._completions = list(completions)
        self.calls = 0

    def complete(self, messages, **kw):
        self.calls += 1
        c = self._completions[min(self.calls - 1, len(self._completions) - 1)]
        if isinstance(c, EndpointError):
            raise c
        return c


class _FakeRegistry:
    def __init__(self, main_ep, unc_ep=None):
        self.main_ep, self.unc_ep = main_ep, unc_ep

    def for_model(self, kind, models):
        return self.main_ep, ModelRef("main-ep", "main-model")

    def for_uncensored(self, models):
        if self.unc_ep is None:
            return None
        return self.unc_ep, ModelRef("unc-ep", "unc-model")


def _loop(make_routine, registry) -> EngineLoop:
    d = make_routine(slug="ref")
    server = _server(d)
    run_dir = d / "runs" / TS
    run_dir.mkdir(parents=True)
    cfg, _ = load_routine(d)
    ctx = RunContext(routine=cfg, server=server, registry=registry, run_ts=TS, run_dir=run_dir,
                     transcript=Transcript(run_dir / "transcript.jsonl"),
                     budgets=Budgets.from_config(cfg.budgets))
    loop = EngineLoop(ctx, "## Run flow", "instr")
    loop.messages = [{"role": "user", "content": "kickoff"}]
    return loop


def test_refers_refusal_to_uncensored_when_configured(make_routine):
    main, unc = _FakeEndpoint([REFUSAL]), _FakeEndpoint([VALID])
    loop = _loop(make_routine, _FakeRegistry(main, unc))
    action, usage = loop._next_action()
    assert action["kind"] == "read_file"          # the uncensored model's action won the turn
    assert loop._referred_turn is True
    assert main.calls == 1 and unc.calls == 1
    assert usage["out"] == 2                       # both completions folded into turn usage


def test_no_referral_when_uncensored_unset(make_routine):
    main = _FakeEndpoint([REFUSAL])               # keeps refusing every attempt
    loop = _loop(make_routine, _FakeRegistry(main, None))
    action, _ = loop._next_action()
    assert action is None                          # inert: falls through to schema forcefail
    assert loop._referred_turn is False
    assert main.calls == 3                         # MAX_SCHEMA_ATTEMPTS, no referral tried


def test_malformed_non_refusal_is_not_referred(make_routine):
    main, unc = _FakeEndpoint([JUNK]), _FakeEndpoint([VALID])
    loop = _loop(make_routine, _FakeRegistry(main, unc))
    action, _ = loop._next_action()
    assert action is None
    assert unc.calls == 0                          # not a refusal → the uncensored model is untouched


def test_uncensored_failure_falls_back_to_normal_retry(make_routine):
    main = _FakeEndpoint([REFUSAL])
    unc = _FakeEndpoint([EndpointError("boom")])
    loop = _loop(make_routine, _FakeRegistry(main, unc))
    action, _ = loop._next_action()
    assert action is None
    assert loop._referred_turn is False
    assert unc.calls == 1                          # tried once, not retried (referral_tried latch)
    assert main.calls == 3


def test_uncensored_also_refuses_falls_back(make_routine):
    main = _FakeEndpoint([REFUSAL])
    unc = _FakeEndpoint([REFUSAL])                 # uncensored gives non-action text too
    loop = _loop(make_routine, _FakeRegistry(main, unc))
    action, _ = loop._next_action()
    assert action is None
    assert loop._referred_turn is False
    assert unc.calls == 1
