"""The HOOK POINT REGISTRY — `rsched/hooks/points.py` and the one evaluator over it.

The operator asked (2026-10-26) whether hooks are fully implemented, naming pre-action, post
action and pre finalize hooks "and many more". Three of those already fired — at hard-coded
call sites, with nothing naming the set, so the question could only be answered by reading the
turn loop. These pin the table that answers it.

What they are really defending is the thing a registry is for: that it stays the SAME
vocabulary as the layers it describes. A table that drifts from `assists.MOMENT_PAYLOADS` or
from `reminders.KINDS` is worse than no table, because it reads as fact while being a second
opinion — the "three stores with a shared name" failure the hook design names in its own
docstring.
"""

from __future__ import annotations

import pytest

from rsched import assists, reminders
from rsched.hooks import dispatch, points


def test_every_assist_moment_is_a_point_and_carries_the_same_payloads():
    """The rule layer's four moments ARE four of these points under older names. If the two
    tables disagree about what a moment may carry, the registry is a second opinion rather
    than the one vocabulary — so the correspondence is asserted, not documented.
    """
    for moment, payloads in assists.MOMENT_PAYLOADS.items():
        name = points.for_moment(moment)
        assert name, f"assist moment {moment!r} maps to no hook point"
        point = points.point(name)
        assert point is not None
        for payload in payloads:
            assert points.allows(name, payload), (
                f"{moment!r} may carry {payload!r} but point {name!r} does not allow it")


def test_every_reminder_trigger_kind_is_a_point():
    """`reminders.KINDS` is the authored vocabulary — `action`, `result`, `prose` and the five
    `turn:*` moments. Each is tested at a point; a kind with no point would be a trigger the
    registry cannot place, which is how the five hard-coded fire sites came about.
    """
    for kind in reminders.KINDS:
        name = points.for_trigger(kind)
        assert name, f"reminder trigger kind {kind!r} maps to no hook point"
        assert points.point(name) is not None


def test_exactly_two_points_can_change_what_runs_and_the_rest_are_free():
    """Which points can alter control flow — and a CORRECTION to the hook design's rule 2,
    found by building this table.

    The design (`state/hooks-design.md`, 2026-10-18) says `hold_before_action` is the only
    delivery that can change what runs, "every other moment rides an observation or a prompt
    section". That was true when it was written and is not true of the engine now: the
    pre-finish rung (`engine/finishgate.py` asking `assist.at_finish`) SETS THE FINISH ASIDE,
    so the run takes another turn it would not have taken. It costs a turn and it changes what
    runs, which is the definition the rule cares about.

    So there are two, and they are different in kind: `pre-action` PREVENTS an action,
    `pre-finalize` DEFERS a finish. Both are pinned by count, because a third appearing here
    without a decision would mean control flow can be altered somewhere the design does not
    account for — which is exactly what this test would have caught about pre-finalize if the
    table had existed when that rung was built.
    """
    assert points.HOLDING_POINTS == ("pre-action", "pre-finalize")
    assert dispatch.holds("pre-action") is True
    assert dispatch.holds("pre-finalize") is True
    for name in points.POINT_NAMES:
        if name not in ("pre-action", "pre-finalize"):
            assert dispatch.holds(name) is False, f"{name} must ride a carrier that costs nothing"


def test_a_free_point_cannot_hold_and_the_held_point_cannot_merely_remind():
    """The coupling is the table's substance. At `pre-action` the action has already been
    emitted, so "remind and let it run" is not expressible — the only way to reach the model is
    to stop the action. At a free point there is no action to stop.
    """
    assert points.allows("pre-action", "hold") is True
    assert points.allows("pre-action", "remind") is False
    assert points.allows("post-action", "remind") is True
    assert points.allows("post-action", "hold") is False


def test_the_registry_says_which_points_nothing_fires_at_yet():
    """The honest half of the operator's answer. A registry listing only what is built cannot
    say what is missing, so the points that exist as moments but have no mechanism are IN the
    table with an empty `fires` — and they are readable as such.
    """
    gaps = [n for n in points.POINT_NAMES if not points.point(n).live]
    assert gaps == []                       # D152 step 9 wired `restart`, the last of four
    assert set(points.LIVE_POINTS) == set(points.POINT_NAMES)
    # The empty list is only worth anything because the same table would still SHOW a gap: a
    # point with no mechanism has no site either, since naming one would be a guess and this
    # table is read as fact.
    dead = points.Point(name="x", about="nothing fires here", payloads=("note",),
                        carrier="prompt_section")
    assert dead.live is False and dead.fires == () and dead.site == ""
    assert dispatch.live("x") is False       # and an unknown point fires nothing at all
    for name in points.LIVE_POINTS:
        assert dispatch.live(name) is True
        assert points.point(name).site, f"{name} fires but names no engine seam"


def test_every_live_point_names_a_mechanism_the_vocabulary_knows():
    for name in points.LIVE_POINTS:
        for mechanism in points.point(name).fires:
            assert mechanism in points.MECHANISMS


def test_every_point_declares_a_known_carrier_and_known_payloads():
    for name in points.POINT_NAMES:
        point = points.point(name)
        assert point.carrier in points.CARRIERS
        assert point.payloads, f"{name} allows no payload at all"
        for payload in point.payloads:
            assert payload in points.PAYLOADS


def test_a_payload_the_engine_cannot_honour_is_not_in_the_vocabulary():
    """`run` — execute something at a point — is the Claude-Code half of the operator's ask and
    needs his decision first (what may run, in whose sandbox, whether it may veto an action
    without a model turn). An enum value the engine cannot honour would be a lie in its own
    registry, which is why `assists.PAYLOADS` omits `scaffold` and `do` too.
    """
    assert "run" not in points.PAYLOADS
    assert set(points.PAYLOADS) >= set(assists.PAYLOADS)


def test_dispatch_returns_the_candidates_that_fire_in_order():
    fired = dispatch.at("post-action", ["a", "b", "c"], lambda c: c != "b")
    assert fired == ["a", "c"]


def test_dispatch_drops_a_candidate_whose_payload_the_point_cannot_carry():
    """A layer that offered the wrong payload at a point used to be a silent no-op. The table
    drops it here, which is the difference between a registry and a comment.
    """
    fired = dispatch.at("pre-action", ["hold-one", "remind-one"], lambda _c: True,
                        payload=lambda c: c.split("-")[0])
    assert fired == ["hold-one"]


def test_a_predicate_that_raises_does_not_fire_and_does_not_fail_the_turn():
    """`assist._fire`'s own discipline, promoted to the seam: a hook layer that is live in
    every run must never be able to break one. The raising candidate is simply absent.
    """
    def boom(candidate):
        if candidate == "bad":
            raise RuntimeError("predicate exploded")
        return True

    assert dispatch.at("post-action", ["ok", "bad", "fine"], boom) == ["ok", "fine"]


def test_an_unknown_point_fires_nothing_rather_than_raising():
    """Lenient like `hooks.from_record`, and for the same reason: the store is shared by
    mechanisms being migrated one at a time, so a caller from a newer build must be inert.
    """
    assert dispatch.at("no-such-point", ["a"], lambda _c: True) == []
    assert dispatch.holds("no-such-point") is False
    assert dispatch.live("no-such-point") is False
    assert points.point("no-such-point") is None
    assert points.allows("no-such-point", "remind") is False


@pytest.mark.parametrize("name", ["run-start", "pre-action", "post-action", "turn-boundary",
                                  "pre-finalize"])
def test_the_points_the_operator_named_are_all_live(name):
    """His words: "a pre-action hook, post action hook, pre finalize hook, and many more". The
    first three fire today, and this is what says so without reading the loop.
    """
    assert dispatch.live(name) is True
