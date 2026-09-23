"""A live `grants` config PATCH rebuilds the base policy — with everything it was built with.

`switches._adopt("grants")` carried its OWN copy of `load_policy`'s arguments and carried two
of the five, so the rebuild silently dropped `recipe_unlocked` and `admin` (both default False)
and the `is_subrun` / `run_history="none"` wrapper a child's policy gets. An operator clicking
a grant on the Decisions page while routine-improver had its recipe unlocked revoked the
unlock mid-run; a child of any live run — children read the ROOT control.json, so they adopt
the parent's patch too — lost its child scope. Both seams now call ONE builder.
"""

from types import SimpleNamespace

from rsched.engine.loopsetup import build_base_policy
from rsched.engine.switches import _adopt


def _loop(tmp_path, *, depth=0, unlocked=False, admin=False):
    ctx = SimpleNamespace(
        server=SimpleNamespace(permissions_home=tmp_path / "permissions"),
        routine=SimpleNamespace(permissions=[], capabilities={}, grants={}, dir=tmp_path),
        run_ts="20260708-070000", depth=depth, granted_now=set(), denied_now=set(),
        granted_once=set(), grants=None)
    loop = SimpleNamespace(ctx=ctx, admin_leg=admin, _recipe_unlocked=unlocked,
                           base_grants=None, grants=None, messages=[])
    build_base_policy(loop, {})
    return loop


def test_a_live_grants_patch_keeps_the_recipe_unlock_and_the_admin_leg(tmp_path):
    loop = _loop(tmp_path, unlocked=True, admin=True)
    assert loop.base_grants.recipe_unlocked and loop.base_grants.admin

    _adopt(loop, "grants", {"some-entity": {"decision": "deny"}})

    assert loop.base_grants.recipe_unlocked, "a grant click must not re-lock the recipe"
    assert loop.base_grants.admin, "…nor demote an admin conversation leg"
    assert loop.ctx.routine.grants == {"some-entity": {"decision": "deny"}}


def test_a_live_grants_patch_keeps_a_child_scoped_as_a_child(tmp_path):
    loop = _loop(tmp_path, depth=1)
    assert loop.base_grants.is_subrun and loop.base_grants.run_history == "none"

    _adopt(loop, "grants", {})

    assert loop.base_grants.is_subrun, "a child must not inherit the routine's denial wording"
    assert loop.base_grants.run_history == "none"
