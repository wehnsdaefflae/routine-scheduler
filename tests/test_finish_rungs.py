"""The finish gate's ladder, declared and asked through the hook-point registry (D152 step 6).

What these pin is the step's whole claim: `pre-finalize` now GOVERNS the gate instead of
describing it. So the table must agree with the gate it describes (a rung the gate asks and the
table omits, or the reverse, is the defect step 4 found in the reminder layer's two entries),
the declared policy must be the one the gate enforces, and the registry must name the mechanism
— because a point whose `fires` omits the layer that fires most at it is a registry read as
fact that is not one.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from types import SimpleNamespace

from rsched.engine import finish_rungs, finishgate, frictions
from rsched.hooks import dispatch, points


def _loop(*, reserved: bool = False):
    return SimpleNamespace(_finish_reserved=reserved)


def _ctx(*, depth: int = 0):
    return SimpleNamespace(depth=depth)


def test_the_registry_names_the_guard_mechanism_at_pre_finalize():
    """The gate's ladder is the mechanism that fires MOST at `pre-finalize` and the table named
    only the two authored faces riding it. A `fires` list that omits a live mechanism is the
    same defect routing the reminder layer through this table exposed in step 4.
    """
    assert "guard" in points.MECHANISMS
    point = points.point(finish_rungs.POINT)
    assert point is not None
    assert finish_rungs.MECHANISM in point.fires
    assert "engine/finish_rungs.py" in point.site       # the seam, not a guess
    assert dispatch.live(finish_rungs.POINT) is True


def test_every_rung_delivers_a_payload_the_point_can_carry():
    """`pre-finalize`'s carrier is a held turn, but what reaches the model is PROSE it reads —
    so a rung's payload is `remind`, not the `hold` that PREVENTS an action at `pre-action`.
    `points.allows` refuses the mix in both directions, which is why a rung declaring the wrong
    one would be dropped by the table rather than silently never fire.
    """
    for rung in finish_rungs.RUNGS:
        assert rung.payload in points.PAYLOADS
        assert points.allows(finish_rungs.POINT, rung.payload) is True
    assert points.allows(finish_rungs.POINT, "hold") is False


def test_the_table_lists_exactly_the_rungs_the_gate_asks():
    """The pin that keeps the declaration honest: every key the table declares is asked by
    `finishgate` under that name, and every `eligible(...)` call in the gate names a declared
    rung. Read off the gate's own source by AST, so adding a rung to one side alone reds here
    instead of becoming a guard on a policy nobody declared.
    """
    tree = ast.parse(inspect.getsource(finishgate))
    asked = {
        node.args[2].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "eligible" and len(node.args) == 3
        and isinstance(node.args[2], ast.Constant)
    }
    assert asked == set(finish_rungs.RUNG_KEYS)
    assert len(finish_rungs.RUNG_KEYS) == len(finish_rungs.RUNGS) == 9


def test_the_gate_keeps_no_hand_written_copy_of_the_access_policy():
    """The duplication this step removes. Five copies of `ctx.depth == 0 and not
    loop._finish_reserved` stood in `check_finish`, each one a rung's access policy written out
    again; a copy that dropped the reserved half would lose a budget-exhausted run its authored
    summary, invisibly. One `_finish_reserved` read is LEFT on purpose: the unbacked-claim rung
    is ASKED on the reserved turn and records an error event instead of deferring, so that
    branch is a disposal, not an eligibility test.
    """
    source = inspect.getsource(finishgate)
    assert source.count("_finish_reserved") == 1
    assert "ctx.depth == 0 and not loop._finish_reserved" not in source


def test_a_child_is_asked_only_the_rung_declared_for_it():
    """A child run owes no accounting, no goals and no summary claims — but it CAN hold jobs,
    and one action settles that rung, so it is the single rung declared for any depth. That
    asymmetry was readable before only by noticing which `if` the jobs check sat outside of.
    """
    child = _ctx(depth=1)
    assert finish_rungs.eligible(_loop(), child, "jobs_owed") is True
    for key in finish_rungs.RUNG_KEYS:
        if key != "jobs_owed":
            assert finish_rungs.eligible(_loop(), child, key) is False


def test_the_reserved_finish_turn_keeps_the_two_rungs_that_may_run_there():
    """Deferring the reserved turn force-finishes the run with an engine string and loses the
    summary the reserve exists to author — so every rung skips it EXCEPT the two whose
    declaration says otherwise: `jobs_owed` (one action settles it) and `unbacked_claims`
    (asked there, disposed of as an error event).
    """
    reserved = _loop(reserved=True)
    allowed = {k for k in finish_rungs.RUNG_KEYS
               if finish_rungs.eligible(reserved, _ctx(), k)}
    assert allowed == {"jobs_owed", "unbacked_claims"}


def test_an_undeclared_rung_is_not_asked_rather_than_asked_blindly():
    """Lenient like every reader in `hooks/`, and safe in this direction: an unknown key means
    the rung is not asked, so a stale caller degrades to the finish STANDING rather than to a
    guard firing on a policy nobody declared.
    """
    assert finish_rungs.eligible(_loop(), _ctx(), "no_such_rung") is False
    assert finish_rungs.eligible(_loop(), _ctx(), "") is False


def test_a_context_missing_its_attributes_disables_no_guard_silently():
    """`eligible` runs its predicate inside `dispatch.at`, where an exception means "did not
    fire" — so a context without `depth` would quietly switch a guard OFF. The defaults are
    explicit for that reason, and they default to the STRICT reading (depth 0, not reserved).
    """
    assert finish_rungs.eligible(SimpleNamespace(), SimpleNamespace(), "accounting") is True


def test_the_friction_counter_reads_the_declaration_instead_of_a_copy():
    """`engine/frictions.RUNGS` was a hand-copied eight-key list beside a nine-rung ladder, so
    a new rung's deferrals fell into the `no_action` bucket until someone noticed. It reads the
    declaration now, which is what makes a tenth rung counted the day it is declared.
    """
    assert frictions.RUNGS is finish_rungs.RUNG_KEYS
    assert "pending_user_input" in frictions.RUNGS


def test_the_declaration_names_the_rungs_in_the_order_check_finish_asks_them():
    """A reader's list is only worth anything in the gate's own order — `frictions` picks the
    FIRST key a deferral carries, and the console names the rung from it.

    Pinned against `check_finish`'s own body, not the module text: three rungs
    (`final_undeclared`, `jobs_owed`, `accounting`) are asked inside the two helpers, which
    Python defines ABOVE the ladder — so module-order is not ladder-order, and a test that read
    it as one failed on its own premise rather than on a defect. The six asked directly must
    appear in the declared relative order, and the three asked via a helper are named here so
    the exemption is a statement rather than a gap.
    """
    body = next(n for n in ast.walk(ast.parse(inspect.getsource(finishgate)))
                if isinstance(n, ast.FunctionDef) and n.name == "check_finish")
    direct = [node.args[2].value for node in ast.walk(body)
              if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
              and node.func.attr == "eligible" and len(node.args) == 3
              and isinstance(node.args[2], ast.Constant)]
    via_helper = ["final_undeclared", "jobs_owed", "accounting"]
    assert set(direct) | set(via_helper) == set(finish_rungs.RUNG_KEYS)
    assert direct == [k for k in finish_rungs.RUNG_KEYS if k not in via_helper]


def test_the_module_lives_outside_the_gate_because_the_gate_is_at_the_size_bar():
    """Not decoration: `engine/finishgate.py` is within ~15 lines of the house's 350-line bar,
    which is why the table is its own module — the same reason `engine/remind_points.py` is not
    six lines inside `engine/remind.py`.
    """
    gate = Path(inspect.getsourcefile(finishgate))
    assert len(gate.read_text(encoding="utf-8").splitlines()) <= 350
    table = Path(inspect.getsourcefile(finish_rungs))
    assert len(table.read_text(encoding="utf-8").splitlines()) <= 350
