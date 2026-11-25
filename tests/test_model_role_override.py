"""The optional `model` field on a CHILD run (D81, extended by the 2026-08-22 user order): a ROLE
(main/tool_call/uncensored) or a CATALOG model NAME, defaulting to the routine's MAIN model (the
per-routine subroutine role is retired). 'uncensored' is rejected with a teaching observation
when the routine has no models.uncensored entry; an unknown catalog name is a teaching error
naming the real catalog. D288 left `child` the only kind taking the field, and what
`list_models` reports — the resolved role bindings and the catalog a name is checked against —
is unchanged, the half of this subject still living in the engine."""

from types import SimpleNamespace

from rsched.config import ModelRef
from rsched.endpoints.base import Completion
from rsched.engine.actions import validate_action
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

    def for_model(self, kind, models, efforts):
        self.roles.append(kind)
        ep = self.eps.setdefault(kind, _Ep())
        return ep, ModelRef(f"{kind}-ep", f"{kind}-model")

    def for_name(self, name):
        self.names.append(name)
        ep = self.eps.setdefault(name, _Ep())
        return ep, ModelRef("cat-ep", f"{name}-model", name=name)

    def for_uncensored(self, models, efforts):
        if not self._unc:
            return None
        self.roles.append("uncensored")
        ep = self.eps.setdefault("uncensored", _Ep())
        return ep, ModelRef("unc-ep", "unc-model")

    def resolve(self, name):
        return _Ep(), ModelRef("cat-ep", f"{name}-model", name=name)


def _server(catalog=("glm-5", "opus-4")):
    # `note` is read unconditionally by do_list_models (D279(a)), so a stand-in without it
    # raises AttributeError there — the stand-in trap a grep for the function's callers
    # cannot see. Kept as a stand-in rather than a real ModelConfig because these tests are
    # about ROLE resolution and construct no catalog file.
    # `endpoints` is read the same way since F868 (the teaching error names the configured
    # endpoints, and list_models lists them) — the same stand-in trap, one field later.
    return SimpleNamespace(models={n: SimpleNamespace(fallbacks=[], note="") for n in catalog},
                           decision_models={}, endpoints={})


def _ctx(registry):
    return SimpleNamespace(registry=registry, routine=SimpleNamespace(models={}, efforts={}),
                           server=_server(), add_usage=lambda u: None)


def test_schema_accepts_roles_and_catalog_names():
    # the schema no longer enums the field: roles AND catalog names pass —
    # membership is checked at dispatch, where the teaching error can name the catalog
    for action in (
        {"say": "s", "kind": "child", "mode": "sequential", "prompt": "p", "model": "uncensored"},
        {"say": "s", "kind": "child", "prompt": "p", "model": "main"},
    ):
        assert validate_action(action) == []


def test_only_a_child_run_still_takes_a_model_field():
    """D288: `llm` and `decide` carried this field too, and both retired into a util
    capability — where the model is an argument of the call, not of an action. So `child` is
    the one kind left that takes it, and an action naming it anywhere else is refused.
    """
    from rsched.engine.actions import KIND_FIELDS

    takers = {k for k, (req, opt) in KIND_FIELDS.items() if "model" in (*req, *opt)}
    assert takers == {"child"}
    assert validate_action({"say": "s", "kind": "util", "name": "x", "model": "main"}) != []


def _child_ctx(registry):
    return SimpleNamespace(sub_counter=[0], depth=0,
                           budgets=SimpleNamespace(max_child_runs=5, max_child_depth=2),
                           registry=registry, routine=SimpleNamespace(models={}, efforts={}),
                           server=_server())


def test_subtask_uncensored_unconfigured_rejected_before_build():
    mgr = SubrunManager(SimpleNamespace(ctx=_child_ctx(_Registry(unc=False))))
    obs = mgr.subtask({"kind": "child", "mode": "sequential", "prompt": "p", "model": "uncensored"})
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
    from rsched.engine.llmaction import do_list_models

    obs = do_list_models(_ctx(_Registry(unc=False)))
    assert obs["kind"] == "list_models"
    assert obs["roles"]["main"]["model"] == "main-model"
    assert obs["roles"]["tool_call"]["model"] == "tool_call-model"
    assert obs["roles"]["uncensored"] is None
    assert [m["name"] for m in obs["models"]] == ["glm-5", "opus-4"]
    assert obs["models"][0]["model"] == "glm-5-model"
    assert obs["models"][0]["fallbacks"] == []


def test_list_models_reports_an_unresolvable_uncensored_role_instead_of_raising():
    """main and tool_call each surface a resolution failure as their own error row; the
    uncensored role resolved unguarded, so naming a model the catalog no longer carries made
    the always-available discovery action raise — and an exception there ends the RUN."""
    from rsched.endpoints.base import EndpointError
    from rsched.engine.llmaction import do_list_models

    class _GoneUncensored(_Registry):
        def for_uncensored(self, models, efforts):
            raise EndpointError("model 'retired' is not in the catalog")

    obs = do_list_models(_ctx(_GoneUncensored()))
    assert obs["roles"]["uncensored"] == {"error": "model 'retired' is not in the catalog"}
    assert obs["roles"]["main"]["model"] == "main-model"


# R1611's empty-completion guard lived in `do_llm` and went with it (D288). Its two units
# were deleted here rather than moved, because there is no engine code left to assert against:
# a model call is a util call now. THE GUARD ITSELF IS OWED BY THE UTIL, and F921 ("a model
# call returning EMPTY content exits 0 — the caller cannot tell it from a real answer") says
# the util does not yet have it. That gap is reported, not papered over here: a test asserting
# engine behaviour nobody can reach would read as coverage while guarding nothing.

