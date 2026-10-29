"""The `spawn` HOOK POINT — the other end of a child's life (D152 step 7).

Step 6 made `child-exit` fire; this is its twin. `hooks/points.py` carried four points with an
empty `fires` since the registry shipped in 0.458.0 — `spawn`, `child-exit`, `run-end`,
`restart` — and the operator's brief put the run tree first: *"Messages between runs, their
children, and the user should implement hooks"*. The gap list is two after this.

What these defend, beyond "it fires":

- **ONCE PER CHILD.** The boundary runs every turn and the child keeps running for many of
  them, so the point must fire at the FIRST boundary after a spawn and never again. A rule
  re-read on every turn of a long child is the layer crying wolf.
- **ORDER.** Nothing is appended by the fire site itself: the run already has the spawn
  observation in its prompt, which is what the point is about.
- **The PAYLOAD coupling.** `spawn` changes no control flow — the child is already running —
  so it carries the free payloads and never `hold`.
- **The predicate is about the BRIEF**, the only thing about a running child a parent can still
  act on, and it is inert on an exit row so the two run-tree moments cannot cross-fire.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from rsched import assists as lib
from rsched.engine import assist, control
from rsched.engine.assist_child import child_fell_short, spawned_a_thin_brief
from rsched.hooks import dispatch, points

RULE = "evidence-discipline"

GOOD_BRIEF = (
    "Build item F999 in the worktree /home/mark/git-repos/routine-scheduler/.audit-wt/F999. "
    "Read the codebase map at /home/mark/git-repos/routine-scheduler/.codemap/index.md first, "
    "then src/rsched/web/api_items.py at the lines it names. Add the field the row is missing, "
    "a test in the same change proven red first, and sweep the docs that describe the route. "
    "Hand back artifacts/F999.md naming every file you changed and the verdict of your last "
    "unnarrowed gate run. Do not bump the version and do not commit."
)


def _assist(*, payload: str = "remind", predicate: str = "thin-child-brief",
            moment: str = "spawn", action: dict | None = None) -> lib.Assist:
    block = [{"id": "on-a-brief-that-may-not-stand-alone", "moment": moment,
              "predicate": predicate, "payload": payload, "line": "read the brief back",
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


def _start_row(brief: str, n: int = 1) -> dict:
    return {"mode": "parallel", "n": n, "label": f"child{n}", "workflow": "general-task",
            "brief": brief}


# -- the predicate ------------------------------------------------------------------


@pytest.mark.parametrize(("brief", "expected"), [
    ("continue the analysis", True),                       # too short to stand alone
    ("fix the test", True),
    ("x" * 600, True),                                     # long, but names nothing to open
    (GOOD_BRIEF, False),                                   # paths, task, hand-back
    ("", False),                                           # nothing to judge
])
def test_the_predicate_fires_on_a_brief_that_cannot_stand_alone(brief, expected):
    """Two ways to be thin, both measured on the brief ALONE: too short to carry a
    self-contained task, or long enough while naming nothing the child can open. An empty
    brief is an absence, not a shortfall — firing on it would fire on every future row
    written before the field exists.
    """
    assert spawned_a_thin_brief(SimpleNamespace(child=_start_row(brief))) is expected


def test_the_two_run_tree_predicates_never_cross_fire():
    """One Situation field carries both ends of a child's life, so each predicate has to
    recognise the row that is NOT its own: a start row has no status, an exit row has no
    brief. Without that, every exit would also be read as a spawn.
    """
    exit_row = {"mode": "parallel", "n": 1, "label": "c", "workflow": "general-task",
                "status": "partial", "turns": 7, "summary": "did half of it"}
    assert spawned_a_thin_brief(SimpleNamespace(child=exit_row)) is False
    assert child_fell_short(SimpleNamespace(child=_start_row("fix it"))) is False


def test_the_predicate_is_inert_without_a_row():
    assert spawned_a_thin_brief(SimpleNamespace(child=None)) is False
    assert spawned_a_thin_brief(SimpleNamespace(child="fix the test")) is False


# -- the registry ------------------------------------------------------------------


def test_spawn_is_a_live_point_that_names_its_seam():
    """A point that fires names the engine seam that fires it, and the gap list shrinks by
    exactly this one — the honest half of "are hooks fully implemented".
    """
    point = points.point("spawn")
    assert point is not None
    assert point.live is True
    assert point.fires == ("rule",)
    assert "announce_started_subruns" in point.site
    assert dispatch.live("spawn") is True
    gaps = {n for n in points.POINT_NAMES if not points.point(n).live}
    assert gaps == {"run-end", "restart"}


def test_spawn_cannot_hold_and_carries_both_free_payloads():
    assert dispatch.holds("spawn") is False
    assert points.allows("spawn", "remind") is True
    assert points.allows("spawn", "action") is True
    assert points.allows("spawn", "hold") is False
    assert "spawn" in points.ACTION_POINTS
    assert "spawn" not in points.HOLDING_POINTS


def test_the_assist_moment_and_the_point_are_the_same_vocabulary():
    assert "spawn" in lib.MOMENTS
    assert points.for_moment("spawn") == "spawn"
    assert set(lib.MOMENT_PAYLOADS["spawn"]) == {"remind", "action"}


def test_the_linter_refuses_a_hold_at_this_moment():
    """The coupling is enforced where a rule is authored, not only where it fires."""
    _, problems = lib.normalize_assists(
        [{"id": "x", "moment": "spawn", "predicate": "thin-child-brief",
          "payload": "hold", "line": "no"}], rule=RULE)
    assert any("spawn" in p and "hold" in p for p in problems), problems


# -- the runtime ------------------------------------------------------------------


def test_the_line_is_appended_as_an_engine_note_for_a_thin_brief(tmp_path):
    fired = _assist()
    loop = _loop(tmp_path, [fired])
    assist.at_spawn(loop, _start_row("continue the analysis"))
    notes = [p for k, p in loop.ctx.transcript.events if k == "user_injection"]
    assert len(notes) == 1
    assert RULE in notes[0]["text"]
    # the assist's OWN key, never a hand-spelled one: the record is what a resumed leg rebuilds
    # its once-per-run guard from (engine/guardscope.py), so the two must be the same string
    assert notes[0]["assists"] == [fired.key]
    assert len(loop.messages) == 1
    assert loop.messages[0]["content"].startswith("ENGINE NOTE:")


def test_a_self_contained_brief_fires_nothing(tmp_path):
    loop = _loop(tmp_path, [_assist()])
    assist.at_spawn(loop, _start_row(GOOD_BRIEF))
    assert loop.ctx.transcript.events == []
    assert loop.messages == []


# -- the fire site ------------------------------------------------------------------


class _Manager:
    def __init__(self, subs: dict) -> None:
        self.subruns = subs


def _sub(n: int, brief: str):
    return SimpleNamespace(mode="parallel", n=n, label=f"child{n}", workflow="general-task",
                           announced_start=False, loop=SimpleNamespace(instruction=brief))


def test_the_point_fires_once_per_child_however_many_boundaries_pass(tmp_path, monkeypatch):
    """The boundary runs every turn while the child keeps running, so a fire site that did not
    mark the child would re-evaluate the point on every turn of a long child.

    Counted at the POINT, not in the messages: the once-per-run assist budget caps the notes
    whatever the site does, so an assertion about message count passes with the marking removed
    and proves nothing about this property (measured — it did).
    """
    fires: list[dict] = []
    monkeypatch.setattr(control.assist, "at_spawn", lambda _loop, row: fires.append(row))
    loop = _loop(tmp_path, [_assist()])
    loop.subruns = _Manager({1: _sub(1, "fix the test")})
    for _ in range(3):
        control.announce_started_subruns(loop)
    assert len(fires) == 1, f"the spawn point fired {len(fires)} times for one child"
    assert loop.subruns.subruns[1].announced_start is True


def test_every_child_of_a_batch_reaches_the_point_exactly_once(tmp_path, monkeypatch):
    """Once per CHILD, not once per boundary: three children spawned in one reply are three
    fires at the next boundary, each carrying its own row — and none at the boundary after.
    """
    fires: list[dict] = []
    monkeypatch.setattr(control.assist, "at_spawn", lambda _loop, row: fires.append(row))
    loop = _loop(tmp_path, [_assist()])
    loop.subruns = _Manager({n: _sub(n, "fix the test") for n in (1, 2, 3)})
    control.announce_started_subruns(loop)
    control.announce_started_subruns(loop)
    assert [row["n"] for row in fires] == [1, 2, 3]
    assert all(row["brief"] == "fix the test" for row in fires)


def test_the_fire_site_appends_nothing_of_its_own(tmp_path):
    """The run already has the spawn OBSERVATION in its prompt — that is what the point is
    about. A second engine-authored copy of a row the model just read would be noise, so with
    no rule at this moment the boundary is silent.
    """
    loop = _loop(tmp_path, [])
    loop.subruns = _Manager({1: _sub(1, "fix the test")})
    control.announce_started_subruns(loop)
    assert loop.messages == []
    assert loop.ctx.transcript.events == []
    assert loop.subruns.subruns[1].announced_start is True


def test_the_once_per_run_guard_covers_a_whole_batch(tmp_path):
    """A parent that spawns three children at once must not read one rule's line three times:
    the once-per-run assist budget covers this moment like every other.
    """
    loop = _loop(tmp_path, [_assist()])
    loop.subruns = _Manager({n: _sub(n, "fix the test") for n in (1, 2, 3)})
    control.announce_started_subruns(loop)
    notes = [p for k, p in loop.ctx.transcript.events if k == "user_injection"]
    assert len(notes) == 1, f"one line per child instead of one per run: {len(notes)}"
