"""The SUPERVISOR model role (D164) — a fourth role beside main / tool_call / uncensored, for
the escalation ladder's overseer.

The operator's answer was option B: add the role, defaulting to `main`, and accept that a
weaker-than-worker overseer becomes configurable. So the two things worth pinning are the
DEFAULT (unset must behave exactly as the code-pinned `main` did, or every ladder on the
instance changes behaviour the day this ships) and the FALLBACK DIRECTION (to the routine's
own main, never to the server's system model — F643's lesson is that a role resolving
somewhere nobody chose is how a routine goes dark).
"""

import pytest

from rsched.config import (
    MODEL_KINDS,
    ROUTINE_MODEL_ROLES,
    EndpointConfig,
    ModelConfig,
    ServerConfig,
)
from rsched.config.base import supervisor_role
from rsched.endpoints import EndpointRegistry


@pytest.fixture
def registry():
    """A registry over a three-model catalog, resolving through the real for_* methods. The
    system model is a THIRD model, so "falls back to main" and "falls back to the system
    model" cannot be confused for one another.
    """
    server = ServerConfig()
    server.endpoints = {"e1": EndpointConfig(name="e1", kind="openai", base_url="http://x")}
    server.models = {
        "sys": ModelConfig(name="sys", endpoint="e1", model="sys-id"),
        "worker": ModelConfig(name="worker", endpoint="e1", model="m-worker"),
        "overseer": ModelConfig(name="overseer", endpoint="e1", model="m-overseer"),
    }
    server.system_model = "sys"
    return EndpointRegistry(server)


def test_the_role_is_one_of_the_chat_roles():
    """It belongs in MODEL_KINDS, not beside the decision roles: it names a CHAT model and
    every chat-role reader (efforts, the mid-run switch, the patch validator) resolves through
    the chat catalog."""
    assert "supervisor" in MODEL_KINDS
    assert "supervisor" in ROUTINE_MODEL_ROLES
    assert MODEL_KINDS[:3] == ("main", "tool_call", "uncensored"), "the three stay as they were"


def _resolved(registry, models):
    """What a supervisory child actually runs on: the rule, then the ordinary role resolve —
    the same two steps childrun and list_models take."""
    return registry.for_model(supervisor_role(models), models, {})[1].name


def test_an_unset_supervisor_resolves_to_the_routines_own_main(registry):
    """THE DEFAULT. Unset, a rung runs on exactly the model the ladder pinned in code — and on
    the routine's MAIN, not the server's system model, even when they differ."""
    assert supervisor_role({}) == "main"
    assert supervisor_role({"main": "worker"}) == "main"
    assert _resolved(registry, {"main": "worker"}) == "worker"
    assert _resolved(registry, {"main": "worker"}) != "sys", "never the system model"
    # it tracks main wherever main points
    assert _resolved(registry, {"main": "overseer"}) == "overseer"
    # with NOTHING set, it lands where main lands — the pre-existing behaviour, unchanged
    assert _resolved(registry, {}) == registry.for_model("main", {}, {})[1].name


def test_a_configured_supervisor_is_used_and_may_differ_from_the_worker(registry):
    """What the operator bought: the overseer becomes configurable, including weaker than the
    worker it judges."""
    models = {"main": "worker", "supervisor": "overseer"}
    assert supervisor_role(models) == "supervisor"
    assert _resolved(registry, models) == "overseer"


def test_the_role_needs_no_configuration_to_be_used_as_an_override():
    """Unlike `uncensored`, which is opt-in and refuses when unset, a `model: supervisor`
    override always resolves — so the ladder's own dispatch can name the role unconditionally.
    """
    from types import SimpleNamespace

    from rsched.engine.subruns import SubrunManager

    ctx = SimpleNamespace(routine=SimpleNamespace(models={}, efforts={}),
                          server=SimpleNamespace(models={"worker": object()}),
                          registry=SimpleNamespace(for_uncensored=lambda *_a: None))
    mgr = SubrunManager.__new__(SubrunManager)
    mgr.parent = SimpleNamespace(ctx=ctx)
    assert mgr._model_reason({"model": "supervisor"}) is None
    assert mgr._model_reason({"model": "main"}) is None
    # `uncensored` still refuses when unset — the roles differ in exactly this
    assert "not configured" in (mgr._model_reason({"model": "uncensored"}) or "")
    # a name that is neither a role nor a catalog model still teaches the alternatives
    assert "neither a role" in (mgr._model_reason({"model": "no-such-model"}) or "")


def test_the_ladder_asks_for_the_role_rather_than_pinning_a_model():
    """The point of the item: the rung dispatch names the ROLE, so configuring it has an
    effect at all. A code-pinned `main` here is what D164 removed."""
    import inspect

    from rsched.engine import ladder

    src = inspect.getsource(ladder)
    assert '"model": "supervisor"' in src
    assert '"model": "main"' not in src


def test_list_models_reports_the_binding_and_whether_it_is_configured():
    """A role a run cannot see is a role nobody configures. `list_models` carries the resolved
    binding plus `configured`, so a run can tell an inherited main from a chosen overseer."""
    import inspect

    from rsched.engine import llmaction

    src = inspect.getsource(llmaction.do_list_models)
    assert 'roles["supervisor"]' in src
    assert '"configured"' in src
