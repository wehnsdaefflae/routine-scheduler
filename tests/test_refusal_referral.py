"""Refusal referral: a refused `llm` tool-call is re-issued to the routine's optional
`uncensored` model (opt-in). Covers the classifier and do_llm's routing behaviour."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from rsched.config import ModelRef
from rsched.endpoints.base import Completion, EndpointError
from rsched.engine.executor import _looks_like_refusal, do_llm

REFUSAL = "I'm sorry, but I can't help with that request."
ANSWER = "Sure — here is the information you asked for: the capital of France is Paris."


# --- classifier --------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    REFUSAL,
    "I can't help with that.",
    "I cannot assist with that, as it goes against my guidelines.",
    "I'm unable to help with that.",
    "I must decline.",
    "  i can't provide instructions for that\n",
])
def test_looks_like_refusal_positive(text):
    assert _looks_like_refusal(text) is True


@pytest.mark.parametrize("text", [
    ANSWER,
    "",
    "Here's a caveat: I can't guarantee the weather, but tomorrow looks sunny.",  # marker not a decline / not at head
    "The function returns None when it cannot find a match.",
])
def test_looks_like_refusal_negative(text):
    assert _looks_like_refusal(text) is False


# --- do_llm routing ----------------------------------------------------------------

class _FakeEndpoint:
    def __init__(self, reply: str = "", parsed=None, raises: bool = False):
        self.reply, self.parsed, self.raises = reply, parsed, raises
        self.calls = 0

    def complete(self, messages, **kw):
        self.calls += 1
        if self.raises:
            raise EndpointError("boom")
        return Completion(text=self.reply, parsed=self.parsed, usage={"in": 1, "out": 1})


class _FakeRegistry:
    def __init__(self, tool_ep, unc_ep=None):
        self.tool_ep, self.unc_ep = tool_ep, unc_ep

    def for_model(self, kind, models):
        assert kind == "tool_call"
        return self.tool_ep, ModelRef("tool-ep", "tool-model")

    def for_uncensored(self, models):
        if self.unc_ep is None:
            return None
        return self.unc_ep, ModelRef("unc-ep", "unc-model")


def _ctx(registry):
    usages = []
    ctx = SimpleNamespace(registry=registry,
                          routine=SimpleNamespace(models={}),
                          add_usage=usages.append, referrals=0)
    ctx.usages = usages
    return ctx


def _action():
    return {"kind": "llm", "prompt": "do the thing", "say": "call"}


def test_refers_refusal_to_uncensored_when_configured():
    tool = _FakeEndpoint(reply=REFUSAL)
    unc = _FakeEndpoint(reply="Here is the uncensored answer.")
    ctx = _ctx(_FakeRegistry(tool, unc))
    out = do_llm(_action(), ctx)
    assert out["referred"] is True
    assert ctx.referrals == 1                      # the audit counter
    assert out["model"] == "unc-model" and out["endpoint"] == "unc-ep"
    assert out["reply"] == "Here is the uncensored answer."
    assert tool.calls == 1 and unc.calls == 1


def test_no_referral_when_uncensored_unset():
    tool = _FakeEndpoint(reply=REFUSAL)
    out = do_llm(_action(), _ctx(_FakeRegistry(tool, None)))
    assert "referred" not in out
    assert out["model"] == "tool-model"
    assert out["reply"] == REFUSAL
    assert tool.calls == 1


def test_no_referral_for_genuine_answer():
    tool = _FakeEndpoint(reply=ANSWER)
    unc = _FakeEndpoint(reply="unused")
    out = do_llm(_action(), _ctx(_FakeRegistry(tool, unc)))
    assert "referred" not in out
    assert out["reply"] == ANSWER
    assert unc.calls == 0


def test_no_referral_for_structured_reply():
    # A schema-parsed reply is an answer, never a refusal — even if its text looks like one.
    tool = _FakeEndpoint(reply=REFUSAL, parsed={"ok": True})
    unc = _FakeEndpoint(reply="unused")
    out = do_llm(_action(), _ctx(_FakeRegistry(tool, unc)))
    assert "referred" not in out
    assert unc.calls == 0


def test_uncensored_endpoint_error_keeps_primary_refusal():
    tool = _FakeEndpoint(reply=REFUSAL)
    unc = _FakeEndpoint(raises=True)
    out = do_llm(_action(), _ctx(_FakeRegistry(tool, unc)))
    assert "referred" not in out
    assert out["reply"] == REFUSAL
    assert out["model"] == "tool-model"
    assert unc.calls == 1


def test_primary_endpoint_error_returns_error():
    tool = _FakeEndpoint(raises=True)
    unc = _FakeEndpoint(reply="unused")
    out = do_llm(_action(), _ctx(_FakeRegistry(tool, unc)))
    assert "error" in out
    assert unc.calls == 0
