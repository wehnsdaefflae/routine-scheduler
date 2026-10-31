"""The `restart` HOOK POINT — the last dead one, and the only one fired OUTSIDE a run (step 9).

`hooks/points.py` has carried `restart` with an empty `fires` since the registry shipped in
0.458.0. Steps 6-8 wired the three that had a turn loop to fire them (`child-exit`, `spawn`,
`run-end`); this one has none — the daemon is exiting, the engine is a separate process about
to be killed, and the party that knows a restart is happening is the one on its way out.

What these defend, beyond "it fires":

- **THE SITE IS THE ONE SEAM EVERY GRACEFUL EXIT REACHES.** `web/appwiring`'s lifespan shutdown
  half, which uvicorn runs on any SIGTERM it handles — a deploy, `systemctl restart`, a
  container replacement — and where `restart.mark_graceful_exit` already writes F746's
  breadcrumb. `restart.trigger_shutdown` (the daemon's own self-update path) runs it too.
- **IT IS NOT A SECOND COPY OF THE RESUME NOTE.** `daemon/reap_resume` files an inbox message
  and is the better channel where it applies — but only for a run the next boot RE-OPENS. A leg
  whose resume is refused (cap spent, routine paused, cause never established) gets nothing,
  and those are exactly the cases where nothing records what happened.
- **A PARKED RUN IS NOT BEING CUT.** A run waiting on a person was not working, so telling its
  next leg that work was cut would be a claim about something that did not happen.
- **THE EXIT IS NEVER DELAYED OR FAILED BY IT.** A diagnostic written on the way out that can
  raise would be worse than no diagnostic: it would turn a clean shutdown into a hang or a
  traceback, in the one moment nothing is left to catch it.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import yaml

from rsched import assists as lib
from rsched.daemon import restart_hook
from rsched.engine import notes
from rsched.engine.assist_restart import work_cut_by_daemon_exit
from rsched.hooks import dispatch, points

RULE = "evidence-discipline"
LINE = "the daemon went down while this run was mid-work"


def _assist(*, payload: str = "note", predicate: str = "work-cut-by-daemon-exit",
            moment: str = "restart") -> lib.Assist:
    block = [{"id": "on-work-cut-by-a-daemon-exit", "moment": moment, "predicate": predicate,
              "payload": payload, "line": LINE}]
    got, problems = lib.normalize_assists(block, rule=RULE)
    assert problems == [], problems
    return got[0]


def _library(tmp_path: Path, *, block: bool = True) -> Path:
    """A rules library whose `evidence-discipline` declares the restart assist."""
    home = tmp_path / "library"
    (home / "rules").mkdir(parents=True)
    assists = ("assists:\n"
               "  - id: on-work-cut-by-a-daemon-exit\n"
               "    moment: restart\n"
               "    predicate: work-cut-by-daemon-exit\n"
               "    payload: note\n"
               f"    line: {LINE}\n") if block else ""
    (home / "rules" / f"{RULE}.md").write_text(
        f"---\n{assists}tags: [reporting]\n---\n# rule: {RULE} — claims trace to observations\n",
        encoding="utf-8")
    return home


def _run(routine_dir: Path, *, state: str = "running", turn: int = 7) -> SimpleNamespace:
    """A live run as the runner tracks it, with the status file the site reads."""
    run_dir = routine_dir / "runs" / "20261031-140002"
    run_dir.mkdir(parents=True)
    (run_dir / "status.json").write_text(json.dumps({"state": state, "turn": turn}),
                                         encoding="utf-8")
    return SimpleNamespace(slug=routine_dir.name, run_id=f"{routine_dir.name}:20261031-140002",
                           run_ts="20261031-140002", run_dir=run_dir)


def _server(library: Path) -> SimpleNamespace:
    return SimpleNamespace(rules_home=library / "rules", libraries_home=library,
                           routines_home=library.parent / "routines")


def _hold(routine_dir: Path, rules: list[str]) -> None:
    """Make the routine HOLD the rules — an assist is part of a rule the routine practises."""
    path = routine_dir / "routine.yaml"
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    cfg["rules"] = rules
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")


# -- the predicate ------------------------------------------------------------------


def test_the_predicate_fires_for_a_run_that_was_working():
    assert work_cut_by_daemon_exit(
        SimpleNamespace(restart={"state": "running", "reason": "SIGTERM"})) is True
    assert work_cut_by_daemon_exit(
        SimpleNamespace(restart={"state": "starting", "reason": "SIGTERM"})) is True


def test_the_predicate_declines_a_run_that_was_not_working():
    """A run parked on a question was waiting on a person. `daemon/restart.PARKED` draws the
    same line for the opposite question — whether a self-update restart may fire at all.
    """
    for state in ("waiting_user", "paused", "queued"):
        assert work_cut_by_daemon_exit(SimpleNamespace(restart={"state": state})) is False


def test_the_predicate_is_inert_without_a_restart():
    """So the same predicate declared at a wrong moment is a lint error, not a mystery fire."""
    assert work_cut_by_daemon_exit(SimpleNamespace(restart=None)) is False
    assert work_cut_by_daemon_exit(SimpleNamespace(restart="running")) is False


# -- the registry ------------------------------------------------------------------


def test_restart_is_a_live_point_that_names_its_seam():
    point = points.point("restart")
    assert point is not None
    assert point.live is True
    assert point.fires == ("rule",)
    assert "restart_hook" in point.site
    assert dispatch.live("restart") is True


def test_no_point_in_the_registry_is_dead_any_more():
    """The answer to the operator's question of 2026-10-26 ("are hooks fully implemented?")
    changes shape here: the registry carried dead points deliberately, so the gap stayed
    readable, and after this step there is no gap to read.
    """
    assert [n for n in points.POINT_NAMES if not points.point(n).live] == []
    for name in points.POINT_NAMES:
        assert points.point(name).site, f"{name} fires but names no engine seam"


def test_restart_carries_a_note_and_cannot_hold():
    assert dispatch.holds("restart") is False
    assert points.allows("restart", "note") is True
    assert points.allows("restart", "hold") is False
    assert lib.MOMENT_PAYLOADS["restart"] == ("note",)
    assert "restart" in lib.MOMENTS
    assert points.ASSIST_MOMENTS["restart"] == "restart"


# -- the delivery ------------------------------------------------------------------


def test_the_line_lands_in_the_notes_file_the_next_leg_reads(make_routine, tmp_path):
    """The carrier is `run-end`'s: `state/notes.md`, whose tail the next state digest renders.
    A run killed by a restart has no next prompt of its own — the line is for whichever leg
    reads that file next, the automatic resume or the next scheduled run.
    """
    routine_dir = make_routine("cutr")
    _hold(routine_dir, [RULE])
    library = _library(tmp_path)
    written = restart_hook.at_daemon_exit(_server(library), [_run(routine_dir)], "SIGTERM")
    assert written == 1
    carried = notes.tail(routine_dir)
    assert LINE in carried
    assert f"RULE {RULE}" in carried
    assert "hook restart" in carried          # a reader can tell a hook's line from the run's


def test_a_parked_run_is_told_nothing(make_routine, tmp_path):
    routine_dir = make_routine("parkedr")
    _hold(routine_dir, [RULE])
    library = _library(tmp_path)
    run = _run(routine_dir, state="waiting_user")
    assert restart_hook.at_daemon_exit(_server(library), [run], "SIGTERM") == 0
    assert notes.tail(routine_dir) == ""


def test_a_routine_that_does_not_hold_the_rule_is_told_nothing(make_routine, tmp_path):
    """An assist is part of a rule the routine PRACTISES — `assists.for_rules`. Nothing here
    can reach a routine the user did not bind the rule to.
    """
    routine_dir = make_routine("otherr")
    _hold(routine_dir, ["git-checkpoint"])
    library = _library(tmp_path)
    assert restart_hook.at_daemon_exit(_server(library), [_run(routine_dir)], "SIGTERM") == 0
    assert notes.tail(routine_dir) == ""


def test_a_library_rule_declaring_nothing_fires_nothing(make_routine, tmp_path):
    """The gap this campaign step also closes: a WIRED point whose rule declares no assist has
    no candidates, and a point with no candidates is indistinguishable from a dead one.
    """
    routine_dir = make_routine("barer")
    _hold(routine_dir, [RULE])
    library = _library(tmp_path, block=False)
    assert restart_hook.at_daemon_exit(_server(library), [_run(routine_dir)], "SIGTERM") == 0


def test_the_fire_is_recorded_in_the_routines_own_tally(make_routine, tmp_path):
    """`assists.record_fire` is how a dead assist shows up as never having fired."""
    routine_dir = make_routine("tallyr")
    _hold(routine_dir, [RULE])
    library = _library(tmp_path)
    restart_hook.at_daemon_exit(_server(library), [_run(routine_dir)], "SIGTERM")
    tally = json.loads(lib.state_path(routine_dir).read_text(encoding="utf-8"))
    assert any("on-work-cut-by-a-daemon-exit" in key for key in tally)


def test_a_raising_predicate_cannot_fail_the_shutdown(make_routine, tmp_path, monkeypatch):
    """The worst moment for an exception: nothing is left to catch it, and a hang or a
    traceback here turns a clean shutdown into an unclean one.
    """
    from rsched.engine import assist_predicates

    boom = assist_predicates.Predicate(
        moment="restart", check=lambda _s: 1 / 0, describes="always explodes")
    monkeypatch.setitem(assist_predicates.PREDICATES, "work-cut-by-daemon-exit", boom)
    routine_dir = make_routine("boomr")
    _hold(routine_dir, [RULE])
    library = _library(tmp_path)
    assert restart_hook.at_daemon_exit(_server(library), [_run(routine_dir)], "SIGTERM") == 0
    assert notes.tail(routine_dir) == ""


def test_an_unreadable_run_cannot_stop_the_others(make_routine, tmp_path):
    """One run's broken state must not cost the rest their line: the exit happens once."""
    good = make_routine("goodr")
    _hold(good, [RULE])
    library = _library(tmp_path)
    broken = SimpleNamespace(slug="x", run_id="x:1", run_ts="20261031-140002",
                             run_dir=tmp_path / "nope" / "runs" / "20261031-140002")
    written = restart_hook.at_daemon_exit(_server(library), [broken, _run(good)], "SIGTERM")
    assert written == 1
    assert LINE in notes.tail(good)


def test_every_live_run_gets_its_own_line(make_routine, tmp_path):
    one, two = make_routine("oner"), make_routine("twor")
    for d in (one, two):
        _hold(d, [RULE])
    library = _library(tmp_path)
    written = restart_hook.at_daemon_exit(_server(library),
                                          [_run(one), _run(two)], "SIGTERM")
    assert written == 2
    assert LINE in notes.tail(one)
    assert LINE in notes.tail(two)
