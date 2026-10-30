"""What a supervisor is TOLD the run is for — `engine/laddergoal.py` (F803).

Its own file because `tests/test_ladder_trigger.py` is over the house size bar and may only
shrink, and because the defect this covers is one a reader needs the reasoning for.

## The defect

`ladder._goal` probed `ctx.routine` for `instruction`, then `task`, then `name`, and
`ladder._stopping` probed it for `done_when` and `finish_line`. **`RoutineConfig` declares none
of those four fields**, and `config/base._Config` sets `extra="ignore"`, so an undeclared key
cannot survive as an attribute either. Every rung ever dispatched therefore judged drift against
the routine's two-word DISPLAY NAME, with an EMPTY list of stopping conditions — while `_goal`'s
own docstring promised "the instruction it was started with, never a paraphrase: a supervisor
judging drift against a summary of the goal cannot see drift INTO that summary".

It survived a green suite because `test_ladder_trigger._Routine` defined all three attributes,
so the readers were proved against a shape production never produces. Same class as
R2348/R2352 in the same module (the fake said `subs`, so did `ladder.py`, and every rung died
of an AttributeError behind green tests). `test_the_reader_cannot_be_fooled_by_a_fake_again`
below pins the invariant against the REAL config class, so no fake can hide it a third time.
"""

from __future__ import annotations

from pathlib import Path

from rsched.config.routine import RoutineConfig
from rsched.engine import laddergoal

RECIPE = """# Worker

## Done when

- d1 — the queue is empty
- d2 · publish — the page is live
"""


class _Ctx:
    def __init__(self, routine, goals=()):
        self.routine = routine
        self.goals = list(goals)


class _Loop:
    def __init__(self, ctx, instruction=""):
        self.ctx = ctx
        self.instruction = instruction


def _routine(tmp_path: Path) -> RoutineConfig:
    """A REAL RoutineConfig — the shape production actually produces, fakes excluded."""
    (tmp_path / "main.md").write_text(RECIPE, encoding="utf-8")
    return RoutineConfig(slug="worker", name="Worker", dir=str(tmp_path))


def test_the_reader_cannot_be_fooled_by_a_fake_again():
    """The four keys the old readers probed are not fields of RoutineConfig, and `extra="ignore"`
    drops them — so no config object can ever carry them, whatever a test fake does."""
    fields = set(RoutineConfig.model_fields)
    assert not fields & {"instruction", "task", "done_when", "finish_line"}
    assert RoutineConfig.model_config.get("extra") == "ignore"


def test_a_top_level_runs_goal_is_its_recipe_never_its_display_name(tmp_path):
    """A scheduled run has NO instruction string: `instruction.md` is a compile seed real
    routines do not persist (`engine/runtime.py` reads it as ""), so the honest goal is the
    recipe's own Done-when lines — what the FINISH is held to."""
    goal = laddergoal.goal(_Loop(_Ctx(_routine(tmp_path))))
    assert "the queue is empty" in goal
    assert "the page is live" in goal
    assert "Worker" not in goal                  # never the display label
    assert "d1" in goal and "d2" in goal         # each line keeps its id


def test_a_child_runs_goal_is_its_prompt_verbatim(tmp_path):
    """A child run and a conversation DO carry the real text, on `loop.instruction`."""
    loop = _Loop(_Ctx(_routine(tmp_path)), instruction="Ship the thing the operator decided.")
    assert laddergoal.goal(loop) == "Ship the thing the operator decided."


def test_a_recipe_without_done_when_yields_an_empty_goal_rather_than_a_label(tmp_path):
    """No sources, no goal — visibly empty, not quietly substituted. A dispatch with an empty
    goal is a signal a reader can act on; the display name read as a real goal and could not."""
    (tmp_path / "main.md").write_text("# Worker\n\nno done-when section here\n", encoding="utf-8")
    cfg = RoutineConfig(slug="worker", name="Worker", dir=str(tmp_path))
    assert laddergoal.goal(_Loop(_Ctx(cfg))) == ""


def test_stopping_composes_the_three_sources_the_finish_gate_uses(tmp_path):
    """The recipe's Done-when lines, the open finish-line outcomes and the open goals a person
    asked — so the supervisor and the finish gate cannot disagree about what the run is held to.
    """
    cfg = _routine(tmp_path)
    (tmp_path / "state").mkdir(exist_ok=True)
    (tmp_path / "state" / "finish-line.json").write_text(
        '{"outcomes": [{"id": "g1", "text": "the mirror is live", "judge": "run",'
        ' "status": "open"}], "until": ""}', encoding="utf-8")
    ctx = _Ctx(cfg, goals=[{"id": "b1", "status": "open", "text": "the operator can read it"},
                           {"id": "b2", "status": "met", "text": "already done"}])
    lines = laddergoal.stopping(_Loop(ctx))
    assert [ln.split(" ")[0] for ln in lines] == ["d1", "d2", "g1", "b1"]   # met goal excluded
    assert "the mirror is live" in lines[2]
    assert "(run judges)" in lines[2]
    assert "(asked by a person)" in lines[3]


def test_stopping_is_empty_for_a_routine_with_no_sources_at_all(tmp_path):
    """Total by construction: an empty list is the honest answer, and it is what the OLD reader
    returned for EVERY run — which is why an empty `stopping` must never read as normal."""
    cfg = RoutineConfig(slug="worker", name="Worker", dir=str(tmp_path))
    assert laddergoal.stopping(_Loop(_Ctx(cfg))) == []
