"""The `run-end` HOOK POINT — the first point whose carrier reaches the NEXT run (D152 step 8).

`hooks/points.py` has carried `run-end` with an empty `fires` since the registry shipped in
0.458.0. Steps 6 and 7 wired the two RUN-TREE points (`child-exit`, `spawn`), both of which
carry an `engine_note`: the line reaches the model that is still running. `run-end` is the
first of the two remaining, and it is different in kind — its carrier is `prompt_section`, so
whatever fires there reaches the NEXT run and never this one. The run is over.

What these defend, beyond "it fires":

- **THE CARRIER IS NOT INVENTED.** The instance already has a next-run channel the model uses
  every run — `state/notes.md`, whose tail the state digest renders (`engine/composer.py`). A
  hook at `run-end` writes there, stamped like any other note. A new store would have needed a
  new reader in the prompt, and the one that exists is the one the next run already reads.
- **THE SITE.** It fires in `engine/loopend.finish_run`, after `result.md` is written and
  BEFORE the autocommit — so the line the next run reads is committed with the run that wrote
  it. `engine/rewind.py` rewrites `result.md` without going through `finish_run`, so a rewind
  cannot re-fire it.
- **THE PAYLOAD.** `note`, not `remind`: at this point there is no model left to remind. The
  assist vocabulary gains the name the point registry already used for exactly this.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from rsched import assists as lib
from rsched.engine import assist, notes
from rsched.engine.assist_runend import children_killed_at_end
from rsched.hooks import dispatch, points

RULE = "evidence-discipline"


def _assist(*, payload: str = "note", predicate: str = "children-killed-at-end",
            moment: str = "run-end") -> lib.Assist:
    block = [{"id": "on-children-killed-at-run-end", "moment": moment, "predicate": predicate,
              "payload": payload, "line": "their work is on disk; read it before re-issuing"}]
    got, problems = lib.normalize_assists(block, rule=RULE)
    assert problems == [], problems
    return got[0]


def _loop(tmp_path, assists_list: list[lib.Assist], *, depth: int = 0):
    ctx = SimpleNamespace(routine=SimpleNamespace(dir=tmp_path, rules=[RULE]),
                          run_ts="20261030-140004", run_id="r1", turn=9, phase=None,
                          depth=depth, job_dir=None)
    return SimpleNamespace(ctx=ctx, assists=assists_list, assists_fired=set(), messages=[])


def _ending(killed: int = 2, status: str = "ok") -> dict:
    return {"status": status, "killed": killed, "run_id": "r1"}


# -- the predicate ------------------------------------------------------------------


@pytest.mark.parametrize(("killed", "expected"), [(0, False), (1, True), (4, True)])
def test_the_predicate_fires_only_when_a_child_was_still_running_at_the_end(killed, expected):
    """A child terminated at its parent's finish leaves finished work nobody reported: it is
    in `runs/<id>/sub/<n>/` and in whatever tree it was building. The engine's own resume note
    said the opposite ("results lost") until F778, so the next run discards it by believing a
    sentence. Nothing is wrong when no child was running — that is every ordinary run.
    """
    assert children_killed_at_end(SimpleNamespace(ending=_ending(killed))) is expected


def test_the_predicate_is_inert_without_an_ending():
    """So the same predicate declared at a wrong moment is a lint error, not a mystery fire."""
    assert children_killed_at_end(SimpleNamespace(ending=None)) is False
    assert children_killed_at_end(SimpleNamespace(ending="2")) is False


# -- the registry ------------------------------------------------------------------


def test_run_end_is_a_live_point_that_names_its_seam():
    point = points.point("run-end")
    assert point is not None
    assert point.live is True
    assert point.fires == ("rule",)
    assert "finish_run" in point.site
    assert dispatch.live("run-end") is True
    gaps = {n for n in points.POINT_NAMES if not points.point(n).live}
    assert gaps == set()            # step 9 wired `restart`, which was the last


def test_run_end_carries_a_note_and_cannot_hold():
    """`note` is the payload whose carrier is not an observation and not a held turn. At this
    point the model is gone, so `remind` would be a name for something that cannot happen.
    """
    assert dispatch.holds("run-end") is False
    assert points.allows("run-end", "note") is True
    assert points.allows("run-end", "action") is True
    assert points.allows("run-end", "hold") is False
    assert "note" in lib.PAYLOADS
    assert lib.MOMENT_PAYLOADS["run-end"] == ("note",)
    assert "run-end" in lib.MOMENTS


# -- the delivery ------------------------------------------------------------------


def test_a_fired_assist_lands_in_the_notes_file_the_next_run_reads(tmp_path):
    """The whole point of this point: the line is in the channel the NEXT run's digest renders
    (`engine/composer.py` reads `notes.tail`), not in a prompt nobody will compose again.
    """
    loop = _loop(tmp_path, [_assist()])
    assist.at_run_end(loop, _ending())
    carried = notes.tail(tmp_path)
    assert "their work is on disk" in carried
    assert f"RULE {RULE}" in carried
    assert "run-end" in carried


def test_nothing_is_written_when_no_assist_fires(tmp_path):
    loop = _loop(tmp_path, [_assist()])
    assist.at_run_end(loop, _ending(killed=0))
    assert notes.tail(tmp_path) == ""
    assert not (tmp_path / "state" / "notes.md").exists()


def test_the_once_per_run_guard_covers_this_moment_like_every_other(tmp_path):
    """One ending, one line — and a resumed leg that ends twice must not write it twice."""
    one = _assist()
    loop = _loop(tmp_path, [one])
    assist.at_run_end(loop, _ending())
    assist.at_run_end(loop, _ending())
    assert notes.tail(tmp_path, lines=50).count("their work is on disk") == 1


def test_a_run_with_no_assists_at_all_writes_nothing(tmp_path):
    loop = _loop(tmp_path, [])
    assist.at_run_end(loop, _ending())
    assert notes.tail(tmp_path) == ""


def test_a_predicate_that_raises_cannot_break_the_ending(tmp_path, monkeypatch):
    """The ending is the one moment where a raising hook would be worst: the run is finished
    and the exception would surface as a failed run that had already succeeded.
    """
    from rsched.engine import assist_predicates

    boom = assist_predicates.Predicate(
        moment="run-end", check=lambda _s: 1 / 0, describes="always explodes")
    monkeypatch.setitem(assist_predicates.PREDICATES, "children-killed-at-end", boom)
    loop = _loop(tmp_path, [_assist()])
    assist.at_run_end(loop, _ending())
    assert notes.tail(tmp_path) == ""
