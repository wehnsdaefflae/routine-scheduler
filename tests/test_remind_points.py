"""The REMINDER LAYER through the hook-point registry (D152 step 4).

The registry has said since 0.458.0 that the `reminder` mechanism fires at eight points, and
`points.TRIGGER_POINTS` mapped every one of `reminders.KINDS` to a point. Nothing read either:
`engine/remind.py` matched with `reminders.matching(...)` at four sites, so the table described
the layer without governing it — a second opinion that reads as fact, which is the failure the
registry's own docstring names.

These pin the reading. Two of them are regressions for defects the routing itself exposed: a
table nobody consults can hold a point whose payload would DROP the only mechanism firing
there, and that is exactly what `run-start` and `prose` did.
"""

from __future__ import annotations

import re
from types import SimpleNamespace

from rsched import reminders
from rsched.engine import remind_points
from rsched.hooks import points


def _reminder(rid: str, kind: str, pattern: str) -> reminders.Reminder:
    return reminders.Reminder(id=rid, regex=pattern, description=f"{rid} fired", kind=kind,
                              scope="local", created_run="20261101-140001", stats={})


def _loop(*rems: reminders.Reminder) -> SimpleNamespace:
    return SimpleNamespace(reminders=list(rems))


def test_every_trigger_kind_reaches_its_point_with_a_payload_the_point_allows():
    """The coupling, asked for the layer rather than for the table. A kind whose payload the
    point refuses is a reminder that can never fire — invisible while the layer matched on its
    own, and a dropped candidate the moment it asks the registry.
    """
    for kind in reminders.KINDS:
        name = points.for_trigger(kind)
        assert name, f"{kind!r} maps to no point"
        payload = remind_points.payload_of(kind)
        assert points.allows(name, payload), (
            f"{kind!r} delivers {payload!r} at {name!r}, which does not allow it")


def test_only_the_holding_kind_delivers_a_hold():
    """`reminders.HOLDING_KINDS` is the authority; this layer derives its payload from it
    rather than keeping a second list that could disagree.
    """
    assert remind_points.payload_of("action") == "hold"
    for kind in reminders.KINDS:
        if kind != "action":
            assert remind_points.payload_of(kind) == "remind", kind
    assert remind_points.payload_of("nonsense") == "remind"


def test_the_first_turn_point_carries_what_the_engine_actually_delivers():
    """REGRESSION, found by routing. `run-start` declared `("note", "action")` with the
    `engine_note` carrier, while the only mechanism firing there — a `turn:first` reminder — is
    evaluated and delivered by `remind.at_observation` on the FIRST turn's observation tail. So
    the table described a delivery nothing performs, and `points.allows` would have dropped
    every `turn:first` reminder as soon as the layer consulted it.
    """
    point = points.point("run-start")
    assert point.carrier == "observation_tail"
    assert points.allows("run-start", "remind") is True
    assert remind_points.matching_at(
        _loop(_reminder("rem-1", "turn:first", "^turn:first")), "turn:first",
        "turn:first\nthe first observation") != []


def test_the_prose_kind_is_tested_where_it_is_actually_evaluated():
    """REGRESSION, same origin. `prose` mapped to `turn-boundary`, whose carrier is an engine
    note; the kind is in fact matched in the same `at_observation` pass as `result`, because the
    turn's own `say` is available exactly when its observation is. Two kinds may share a point:
    the point says WHEN a hook is tested, the kind says WHAT it watches.
    """
    assert points.for_trigger("prose") == points.for_trigger("result") == "post-action"
    assert "reminder" not in points.point("turn-boundary").fires


def test_a_reminder_of_another_kind_never_fires_at_this_point():
    """What the kind is FOR: a pattern aimed at an action string says nothing about an
    observation, so the subjects are never crossed.
    """
    loop = _loop(_reminder("rem-a", "action", "util:x"), _reminder("rem-r", "result", "util:x"))
    assert [r.id for r in remind_points.matching_at(loop, "action", "util:x --json")] == ["rem-a"]
    assert [r.id for r in remind_points.matching_at(loop, "result", "util:x --json")] == ["rem-r"]


def test_a_pattern_that_raises_does_not_fire_and_does_not_fail_the_turn():
    """NEW to this layer, and the substance of the step rather than a tidy-up: a reminder
    pattern is user-authored and was called inside the turn loop with nothing between a
    pathological match and the run. `hooks/dispatch.at` owns that discipline for every
    mechanism — a raising predicate means "did not fire".
    """
    class _Exploding(reminders.Reminder):
        def matches(self, target: str) -> bool:
            raise RuntimeError("pattern exploded")

    bad = _Exploding(id="rem-bad", regex="x", description="boom", kind="result",
                     scope="local", created_run="20261101-140001", stats={})
    loop = _loop(bad, _reminder("rem-ok", "result", "exit 1"))
    assert [r.id for r in remind_points.matching_at(loop, "result", "exit 1")] == ["rem-ok"]


def test_an_unmapped_kind_fires_nothing_rather_than_matching_anyway():
    """No fallback to a direct match: a fallback would make the registry advisory again, which
    is the defect this step fixes.
    """
    loop = _loop(_reminder("rem-1", "result", ".*"))
    assert remind_points.matching_at(loop, "no-such-kind", "anything") == []
    assert remind_points.matching_at(_loop(), "result", "anything") == []


def test_the_order_of_the_live_set_is_preserved():
    """The caller renders one line per reminder in the set's own order, so the dispatcher may
    not reorder what it returns."""
    loop = _loop(_reminder("rem-1", "result", "e"), _reminder("rem-2", "result", "x"),
                 _reminder("rem-3", "result", "i"))
    assert [r.id for r in remind_points.matching_at(loop, "result", "exit")] == [
        "rem-1", "rem-2", "rem-3"]


def test_the_regex_still_decides_which_ones_match():
    """Routing must not change WHAT matches — only where the decision is made."""
    loop = _loop(_reminder("rem-1", "result", r"^exit 1\b"), _reminder("rem-2", "result", "nope"))
    assert [r.id for r in remind_points.matching_at(loop, "result", "exit 1 — failed")] == [
        "rem-1"]
    assert re.search(r"^exit 1\b", "exit 1 — failed")
