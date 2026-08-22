"""The optional `model` field on llm/spawn/subtask (D81, extended by the 2026-08-22 user
order): a ROLE (main/tool_call/uncensored) or a CATALOG model NAME — llm defaults to
tool_call, children (spawn/subtask) default to the routine's MAIN model (the per-routine
subroutine role is retired). 'uncensored' is rejected with a teaching observation when the
routine has no models.uncensored entry; an unknown catalog name is a teaching error naming
the real catalog; an explicit uncensored llm call is never re-referred.
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
        self.names: list[str] = []
        self._unc = unc

    def for_model(self, kind, models):
        self.roles.append(kind)
        ep = self.eps.setdefault(kind, _Ep())
        return ep, ModelRef(f"{kind}-ep", f"{kind}-model")

    def for_name(self, name):
        self.names.append(name)
        ep = self.eps.setdefault(name, _Ep())
        return ep, ModelRef("cat-ep", f"{name}-model", name=name)

    def for_uncensored(self, models):
        if not self._unc:
            return None
        self.roles.append("uncensored")
        ep = self.eps.setdefault("uncensored", _Ep())
        return ep, ModelRef("unc-ep", "unc-model")

    def resolve(self, name):
        return _Ep(), ModelRef("cat-ep", f"{name}-model", name=name)


def _server(catalog=("glm-5", "opus-4")):
    return SimpleNamespace(models={n: SimpleNamespace(fallbacks=[]) for n in catalog})


def _ctx(registry):
    return SimpleNamespace(registry=registry, routine=SimpleNamespace(models={}),
                           server=_server(), add_usage=lambda u: None)


def test_schema_accepts_roles_and_catalog_names():
    # the schema no longer enums the field: roles AND catalog names pass, spawn included —
    # membership is checked at dispatch, where the teaching error can name the catalog
    for action in (
        {"say": "s", "kind": "llm", "prompt": "p", "model": "main"},
        {"say": "s", "kind": "subtask", "prompt": "p", "model": "uncensored"},
        {"say": "s", "kind": "llm", "prompt": "p", "model": "glm-5"},
        {"say": "s", "kind": "spawn", "prompt": "p", "model": "main"},
    ):
        assert validate_action(action) == []


def test_llm_role_override_resolves_that_role():
    reg = _Registry()
    obs = do_llm({"prompt": "p", "model": "main", "say": "s"}, _ctx(reg))
    assert obs["reply"] == "reply" and reg.roles == ["main"]
    reg2 = _Registry()
    do_llm({"prompt": "p", "say": "s"}, _ctx(reg2))
    assert reg2.roles == ["tool_call"]           # default unchanged


def test_llm_catalog_name_override_resolves_by_name():
    reg = _Registry()
    obs = do_llm({"prompt": "p", "model": "glm-5", "say": "s"}, _ctx(reg))
    assert obs["reply"] == "reply"
    assert reg.names == ["glm-5"] and reg.roles == []


def test_llm_unknown_model_is_a_teaching_error_naming_the_catalog():
    reg = _Registry()
    obs = do_llm({"prompt": "p", "model": "nope-9", "say": "s"}, _ctx(reg))
    assert "nope-9" in obs["error"] and "glm-5" in obs["error"]   # the catalog is taught
    assert reg.roles == [] and reg.names == []                    # nothing was called


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


def _child_ctx(registry):
    return SimpleNamespace(sub_counter=[0], depth=0,
                           budgets=SimpleNamespace(max_subruns=5, max_subrun_depth=2),
                           registry=registry, routine=SimpleNamespace(models={}),
                           server=_server())


def test_subtask_uncensored_unconfigured_rejected_before_build():
    mgr = SubrunManager(SimpleNamespace(ctx=_child_ctx(_Registry(unc=False))))
    obs = mgr.subtask({"kind": "subtask", "prompt": "p", "model": "uncensored"})
    assert obs["rejected"] and "uncensored" in obs["reason"]


def test_child_unknown_model_rejected_before_build_naming_catalog():
    # BOTH schedulers pre-validate: the rejection teaches the catalog, nothing is built
    for verb in ("subtask", "spawn"):
        mgr = SubrunManager(SimpleNamespace(ctx=_child_ctx(_Registry())))
        obs = getattr(mgr, verb)({"kind": verb, "prompt": "p", "model": "nope-9"})
        assert obs["rejected"] and "nope-9" in obs["reason"] and "glm-5" in obs["reason"]


def test_child_catalog_name_passes_prevalidation():
    # a real catalog name clears _model_reason (build itself is exercised in
    # test_childrun_roots against the seeded library)
    mgr = SubrunManager(SimpleNamespace(ctx=_child_ctx(_Registry())))
    assert mgr._model_reason({"model": "glm-5"}) is None
    assert mgr._model_reason({"model": "main"}) is None
    assert mgr._model_reason({}) is None


def test_list_models_reports_roles_and_catalog():
    """The discovery half of the per-call override: resolved role bindings + every
    catalog name a `model` field may carry; an unset uncensored role reads as None
    (honest), never as an invented binding."""
    from rsched.engine.executor import do_list_models

    obs = do_list_models(_ctx(_Registry(unc=False)))
    assert obs["kind"] == "list_models"
    assert obs["roles"]["main"]["model"] == "main-model"
    assert obs["roles"]["tool_call"]["model"] == "tool_call-model"
    assert obs["roles"]["uncensored"] is None
    assert [m["name"] for m in obs["models"]] == ["glm-5", "opus-4"]
    assert obs["models"][0]["model"] == "glm-5-model"
    assert obs["models"][0]["fallbacks"] == []
