"""D81: the optional `model` field on llm/subtask names a model ROLE — the call runs on
that role's configured model instead of the default (llm → tool_call, subtask →
subroutine). 'uncensored' is rejected with a teaching observation when the routine has no
models.uncensored entry; an explicit uncensored llm call is never re-referred.
"""

from types import SimpleNamespace

from rsched.config import ModelRef
from rsched.endpoints.base import Completion
from rsched.engine.actions import validate_action
from rsched.engine.executor import do_llm
from rsched.engine.subruns import SubrunManager


class _Ep:
    def __init__(self, text="reply"):
        self.calls = []
        self._text = text

    def complete(self, messages, **kw):
        self.calls.append(kw)
        return Completion(text=self._text, parsed=None, usage={"in": 1, "out": 1})


class _Registry:
    def __init__(self, unc=False):
        self.eps: dict[str, _Ep] = {}
        self.roles: list[str] = []
        self._unc = unc

    def for_model(self, kind, models):
        self.roles.append(kind)
        ep = self.eps.setdefault(kind, _Ep())
        return ep, ModelRef(f"{kind}-ep", f"{kind}-model")

    def for_uncensored(self, models):
        if not self._unc:
            return None
        self.roles.append("uncensored")
        ep = self.eps.setdefault("uncensored", _Ep())
        return ep, ModelRef("unc-ep", "unc-model")


def _ctx(registry):
    return SimpleNamespace(registry=registry, routine=SimpleNamespace(models={}),
                           add_usage=lambda u: None)


def test_schema_accepts_role_on_llm_and_subtask_only():
    ok = {"say": "s", "kind": "llm", "prompt": "p", "model": "main"}
    assert validate_action(ok) == []
    assert validate_action({"say": "s", "kind": "subtask", "prompt": "p",
                            "model": "uncensored"}) == []
    assert validate_action({"say": "s", "kind": "llm", "prompt": "p", "model": "gpt-x"})
    assert validate_action({"say": "s", "kind": "spawn", "prompt": "p", "model": "main"})


def test_llm_role_override_resolves_that_role():
    reg = _Registry()
    obs = do_llm({"prompt": "p", "model": "main", "say": "s"}, _ctx(reg))
    assert obs["reply"] == "reply" and reg.roles == ["main"]
    reg2 = _Registry()
    do_llm({"prompt": "p", "say": "s"}, _ctx(reg2))
    assert reg2.roles == ["tool_call"]           # default unchanged


def test_llm_uncensored_unconfigured_is_a_teaching_error():
    reg = _Registry(unc=False)
    obs = do_llm({"prompt": "p", "model": "uncensored", "say": "s"}, _ctx(reg))
    assert "uncensored" in obs["error"] and reg.roles == []   # no endpoint call happened


def test_llm_explicit_uncensored_is_not_re_referred():
    reg = _Registry(unc=True)
    reg.eps["uncensored"] = _Ep(text="I'm sorry, but I can't help with that request.")
    obs = do_llm({"prompt": "p", "model": "uncensored", "say": "s"}, _ctx(reg))
    # the refusal-shaped text comes back verbatim: one call, no referral loop
    assert obs["reply"].startswith("I'm sorry") and reg.roles == ["uncensored"]
    assert not obs.get("referred")


def test_subtask_uncensored_unconfigured_rejected_before_build():
    ctx = SimpleNamespace(sub_counter=[0], depth=0,
                          budgets=SimpleNamespace(max_subruns=5, max_subrun_depth=2),
                          registry=_Registry(unc=False),
                          routine=SimpleNamespace(models={}))
    mgr = SubrunManager(SimpleNamespace(ctx=ctx))
    obs = mgr.subtask({"kind": "subtask", "prompt": "p", "model": "uncensored"})
    assert obs["rejected"] and "uncensored" in obs["reason"]
