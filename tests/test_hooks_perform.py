"""The `action` PAYLOAD — a hook that PERFORMS one of the engine's own actions at a point.

The operator, 2026-10-26, asked what a hook may do and answered it: *"Any action. This includes
utils and therefore scripts."* These pin the two halves of that:

- the REGISTRY half — which points carry `action`, and the rule that decides it. A point that
  can change what runs does NOT carry it, which is his second ruling the same day: *"No veto
  ever — a hook performs and reports; only a hold (prose to the model) can stop an action."*;
- the PERFORMER half — that `hooks/perform.perform` runs an authored action through the MODEL's
  own path (schema → `validate_action` against the firing routine's capabilities →
  `actionroute.dispatch_action`), never `executor.dispatch`, and that no failure of a hook can
  fail the run it fires in.

The seam under test is the one `control.run_user_command` has used in production since slash
commands shipped; `perform` is that core with a hook's authorship instead of the user's.
"""

from __future__ import annotations

import pytest

from rsched.hooks import perform as perform_mod
from rsched.hooks import points


def test_action_is_in_the_payload_vocabulary():
    """His ruling in one assertion: a hook may perform an action, so the registry says so."""
    assert "action" in points.PAYLOADS


def test_no_holding_point_carries_an_action_payload():
    """The rule that decides `ACTION_POINTS`, pinned both ways.

    `pre-action` and `pre-finalize` are the two points that change what runs. The operator ruled
    (2026-10-26): *"No veto ever — a hook performs and reports; only a hold (prose to the model)
    can stop an action."* So a performed action never changes control flow: those two points
    carry `hold`/`remind` and never `action`, and every OTHER point carries it. This assertion
    is that ruling — a future change making it pass would be reversing his decision.
    """
    for name in points.HOLDING_POINTS:
        assert not points.allows(name, "action"), (
            f"{name!r} can change what runs AND carries an action payload — that is the "
            "third control path HOLDING_POINTS reserves for the operator")
    assert set(points.ACTION_POINTS) == set(points.POINT_NAMES) - set(points.HOLDING_POINTS)
    assert points.ACTION_POINTS, "no point carries an action payload at all"


def test_wiring_a_site_never_changes_what_a_point_may_carry():
    """A point declares what it WOULD carry whether or not anything fires there yet — otherwise
    each campaign step would have to edit the payload column as well as wire the site, and the
    table would stop being readable as 'what is possible here'.

    All FOUR points that shipped dead with the registry declared `action` while nothing fired at
    them, and steps 6-9 wired every one: `child-exit` on a child's hand-back, `spawn` on its
    start, `run-end` once the result is written, `restart` as the daemon exits. Not one needed
    its payload column touched, which is the property this test is really about.
    """
    for name in ("child-exit", "spawn", "run-end", "restart"):
        assert points.allows(name, "action")
        assert points.point(name).live


class _Transcript:
    """The sanctioned recorder stub: the real `Transcript.event()` appends to a file, so its
    double keeps the rows in a list the test reads back (`RECORDERS` in
    tests/test_double_conformance.py exempts exactly this attribute, for exactly this reason).
    """

    def __init__(self):
        self.events = []

    def event(self, kind, payload):
        self.events.append((kind, payload))


class _Ctx:
    """A RunContext double that assigns only attributes the real class has — F644's rule, which
    `tests/test_double_conformance.py` enforces and which caught the first version of this file.
    """

    def __init__(self):
        self.transcript = _Transcript()

    def write_status(self):
        pass


class _Loop:
    def __init__(self, allowed=("util",)):
        self.ctx = _Ctx()
        self.allowed_tools = list(allowed)
        # None is `validate_action`'s documented "no grant layer" — the kind gate still applies,
        # which is the half these tests are about. A dict is NOT a stand-in for Grants: it has
        # no `.deny`, and passing one made the call raise instead of refuse.
        self.grants = None
        self.executed_actions = 0


def test_a_point_that_does_not_carry_an_action_refuses_it_and_names_the_ones_that_do():
    loop = _Loop()
    obs = perform_mod.perform(loop, {"say": "x", "kind": "util", "name": "websearch"},
                              point="pre-action", source="rule:some-rule")
    assert "does not carry an `action` payload" in obs["error"]
    assert "run-start" in obs["error"]            # it teaches where it CAN be used
    assert loop.executed_actions == 0             # nothing ran
    assert loop.ctx.transcript.events == []       # and nothing was recorded as having run


def test_an_action_outside_the_firing_routines_capabilities_never_dispatches(monkeypatch):
    """The capability model bounds a hook for free: `validate_action` is the model's own gate,
    so a hook can never do what the routine it fires in may not. Proven by refusing a kind the
    loop's workflow does not allow, with `dispatch_action` monkeypatched to explode if reached.
    """
    from rsched.engine import actionroute

    def _boom(*_a, **_k):
        raise AssertionError("dispatch_action must not be reached for a refused action")

    monkeypatch.setattr(actionroute, "dispatch_action", _boom)
    loop = _Loop(allowed=("read_file",))          # no `util` in this workflow
    obs = perform_mod.perform(loop, {"say": "x", "kind": "util", "name": "websearch"},
                              point="turn-boundary", source="feature:degrade")
    assert obs["kind"] == perform_mod.OBS_FLAG
    assert obs["error"]
    assert loop.executed_actions == 0


def test_a_malformed_action_is_an_observation_not_an_exception():
    loop = _Loop()
    obs = perform_mod.perform(loop, {"kind": "util"},      # no `say` — the schema requires it
                              point="turn-boundary", source="routine:x")
    assert obs["kind"] == perform_mod.OBS_FLAG
    assert obs["error"]


def test_a_performed_action_is_dispatched_recorded_and_grounds_a_finish(monkeypatch):
    """The happy path, and the three things it must leave behind: the observation the action
    produced, a transcript row flagged as a HOOK's action (not the model's, not the user's) and
    naming its point and author, and `executed_actions` incremented so a later model finish is
    grounded exactly as a slash command grounds one.
    """
    from rsched.engine import actionroute

    seen = {}

    def _ok(loop, action, ctx):
        seen["action"] = action
        return {"kind": "util", "exit_code": 0, "stdout": "ok"}

    monkeypatch.setattr(actionroute, "dispatch_action", _ok)
    loop = _Loop()
    obs = perform_mod.perform(loop, {"say": "x", "kind": "util", "name": "websearch"},
                              point="post-action", source="rule:evidence-discipline")
    assert obs["exit_code"] == 0
    assert seen["action"]["name"] == "websearch"
    assert loop.executed_actions == 1
    kind, payload = loop.ctx.transcript.events[-1]
    assert kind == "observation"
    assert payload[perform_mod.OBS_FLAG] is True
    assert payload["point"] == "post-action"
    assert payload["source"] == "rule:evidence-discipline"


def test_a_dispatch_that_raises_cannot_fail_the_run(monkeypatch):
    """`dispatch.at`'s rule 2 at the executing end: a hook layer live in every run must not be
    able to break one. The exception becomes the observation.
    """
    from rsched.engine import actionroute

    def _raise(*_a, **_k):
        raise RuntimeError("the util exploded")

    monkeypatch.setattr(actionroute, "dispatch_action", _raise)
    loop = _Loop()
    obs = perform_mod.perform(loop, {"say": "x", "kind": "util", "name": "websearch"},
                              point="turn-boundary", source="routine:x")
    assert "the util exploded" in obs["error"]
    assert loop.executed_actions == 0


def test_perform_routes_through_actionroute_and_never_the_executor():
    """The trap `run_user_command`'s docstring carries, asserted on the source: `/util` once
    went straight to `executor.dispatch` and so skipped D39's secret-exposure gate, letting a
    DECLINED secret reach a util the chat ran. A hook is authored, not a decision about
    exposure, so it must take the model's routing table too.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(perform_mod))
    imported = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert any(m.endswith("actionroute") for m in imported), (
        "perform must take the model's routing table")
    assert not any("executor" in m for m in imported), (
        "perform imports the executor — that path skips D39's secret-exposure gate")
    called = {ast.unparse(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
    assert "dispatch_action" in called
    assert not any("executor" in c for c in called)


@pytest.mark.parametrize("point", ["", "nope", "PRE-ACTION"])
def test_an_unknown_point_performs_nothing(point):
    loop = _Loop()
    obs = perform_mod.perform(loop, {"say": "x", "kind": "util", "name": "websearch"},
                              point=point, source="routine:x")
    assert obs["error"]
    assert loop.executed_actions == 0
