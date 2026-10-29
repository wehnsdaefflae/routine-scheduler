"""The `child-exit` HOOK POINT — the run-tree moment a rule can reach (D152 step 6).

`hooks/points.py` has carried four points with an empty `fires` since the registry shipped in
0.458.0: `spawn`, `child-exit`, `run-end`, `restart`. The operator's brief put that traffic
first — *"Messages between runs, their children, and the user should implement hooks"* — and
this is the first of the four to fire: the rule layer reaches it at the turn boundary where a
child's hand-back is delivered to its parent (`engine/control.announce_finished_subruns`).

What these defend, beyond "it fires":

- **ORDER.** The note lands AFTER the hand-back message, never before. A rule commenting on a
  hand-back the model cannot yet see is worse than no comment: it reads as a claim about
  something absent.
- **The GUARD.** A parent with four children must not read one rule's line four times. The
  once-per-run assist budget covers this moment like every other.
- **The PAYLOAD coupling.** `child-exit` changes no control flow, so it carries the free
  payloads and never `hold` — the same line `hooks/points.ACTION_POINTS` derives from
  `HOLDING_POINTS`.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from rsched import assists as lib
from rsched.engine import assist
from rsched.engine.assist_child import child_fell_short
from rsched.hooks import dispatch, points

RULE = "evidence-discipline"


def _assist(*, payload: str = "remind", predicate: str = "child-fell-short",
            moment: str = "child-exit", action: dict | None = None) -> lib.Assist:
    block = [{"id": "on-a-child-that-fell-short", "moment": moment, "predicate": predicate,
              "payload": payload, "line": "read what it says is unfinished",
              **({"action": action} if action else {})}]
    got, problems = lib.normalize_assists(block, rule=RULE)
    assert problems == [], problems
    return got[0]


class _Transcript:
    """Its own stub, never attributes bolted onto a RunContext double (F644)."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def event(self, kind: str, payload: dict) -> None:
        self.events.append((kind, payload))


def _loop(tmp_path, assists_list: list[lib.Assist]):
    ctx = SimpleNamespace(routine=SimpleNamespace(dir=tmp_path, rules=[RULE]),
                          transcript=_Transcript(), user_replies=0, turn=1)
    return SimpleNamespace(ctx=ctx, assists=assists_list, assists_fired=set(),
                           assist_finish_deferred=False, assist_user_replies=0,
                           assist_undo_points=set(), assist_undo_found=None, messages=[])


def _row(status: str = "partial", n: int = 1) -> dict:
    return {"mode": "parallel", "n": n, "label": f"child{n}", "workflow": "general-task",
            "status": status, "turns": 7, "summary": "did half of it"}


# -- the predicate ------------------------------------------------------------------


@pytest.mark.parametrize(("status", "expected"), [("ok", False), ("partial", True),
                                                  ("failed", True), ("OK", False), ("", False)])
def test_the_predicate_fires_only_for_a_child_that_did_not_finish_ok(status, expected):
    """`ok` is the one status that means the child did what it was asked. A missing status is
    not a shortfall either — it is an absence, and a rule that fired on it would fire on every
    row a future mode writes before the field exists.
    """
    assert child_fell_short(SimpleNamespace(child=_row(status))) is expected


def test_the_predicate_is_inert_without_a_child_row():
    """So the same predicate declared at a wrong moment is a lint error, not a mystery fire."""
    assert child_fell_short(SimpleNamespace(child=None)) is False
    assert child_fell_short(SimpleNamespace(child="partial")) is False


# -- the registry ------------------------------------------------------------------


def test_child_exit_is_a_live_point_that_names_its_seam():
    """The honest half of "are hooks fully implemented": a point that fires names the engine
    seam that fires it, and the gap list shrinks by exactly this one.
    """
    point = points.point("child-exit")
    assert point is not None
    assert point.live is True
    assert point.fires == ("rule",)
    assert "announce_finished_subruns" in point.site
    assert dispatch.live("child-exit") is True
    gaps = {n for n in points.POINT_NAMES if not points.point(n).live}
    assert gaps == {"run-end", "restart"}      # step 7 wired `spawn`, this point's twin


def test_child_exit_cannot_hold_and_carries_both_free_payloads():
    assert dispatch.holds("child-exit") is False
    assert points.allows("child-exit", "remind") is True
    assert points.allows("child-exit", "action") is True
    assert points.allows("child-exit", "hold") is False
    assert "child-exit" in points.ACTION_POINTS
    assert "child-exit" not in points.HOLDING_POINTS


def test_the_assist_moment_and_the_point_are_the_same_vocabulary():
    assert "child-exit" in lib.MOMENTS
    assert points.for_moment("child-exit") == "child-exit"
    assert set(lib.MOMENT_PAYLOADS["child-exit"]) == {"remind", "action"}


def test_the_linter_refuses_a_hold_at_this_moment():
    """The coupling is enforced where a rule is authored, not only where it fires."""
    _, problems = lib.normalize_assists(
        [{"id": "x", "moment": "child-exit", "predicate": "child-fell-short",
          "payload": "hold", "line": "no"}], rule=RULE)
    assert any("child-exit" in p and "hold" in p for p in problems), problems


# -- the runtime ------------------------------------------------------------------


def test_the_line_is_appended_as_an_engine_note_when_a_child_falls_short(tmp_path):
    fired = _assist()
    loop = _loop(tmp_path, [fired])
    assist.at_child_exit(loop, _row("failed"))
    notes = [p for k, p in loop.ctx.transcript.events if k == "user_injection"]
    assert len(notes) == 1
    assert RULE in notes[0]["text"]
    # the assist's OWN key, never a hand-spelled one: the record is what a resumed leg rebuilds
    # its once-per-run guard from (engine/guardscope.py), so the two must be the same string
    assert notes[0]["assists"] == [fired.key]
    assert len(loop.messages) == 1
    assert loop.messages[0]["content"].startswith("ENGINE NOTE:")


def test_an_ok_child_fires_nothing(tmp_path):
    loop = _loop(tmp_path, [_assist()])
    assist.at_child_exit(loop, _row("ok"))
    assert loop.ctx.transcript.events == []
    assert loop.messages == []


def test_four_children_spend_the_assist_once(tmp_path):
    """The once-per-run budget, which is the whole reason a parent of four children does not
    read one line four times.
    """
    loop = _loop(tmp_path, [_assist()])
    for n in range(1, 5):
        assist.at_child_exit(loop, _row("partial", n=n))
    assert len(loop.messages) == 1


def test_a_run_with_no_assists_does_no_work(tmp_path):
    loop = _loop(tmp_path, [])
    assist.at_child_exit(loop, _row("failed"))
    assert loop.messages == []
