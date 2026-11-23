"""A child that silently got the builtin harness is a fleet incident, not an aside (F939). The
parent's observation has always carried the note, but the child RUNS, so nothing fails and the
one line goes by unread: scheduler-builder spawned a `maintain-a-codebase` child, got the
generic pattern because the library's copy does not parse, and found out only by re-reading the
observation. One unparseable pattern degrades every spawn that names it, fleet-wide, for as long
as the library stays broken — so the drop belongs where the fleet's incidents are read. The
fixtures come from `test_loop` deliberately: these assertions have to hold over the REAL engine
loop that materializes a child, not over a direct call to the function under test.
"""
from __future__ import annotations

import json
from pathlib import Path

from helpers import tmp_server
from rsched import health_vocab
from test_loop import PARENT, _run, finish, subtask, wait_

EVENT = "child_recipe_unavailable"


def _events(routines_home: Path) -> list[dict]:
    path = Path(routines_home) / ".control" / "health-events.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_a_child_that_fell_back_to_the_builtin_pattern_files_a_health_event(
        make_routine, scripted):
    """The reading this exists for: which routine asked for which pattern, and why it failed."""
    d, _ep, status, _run_dir, _events_log = _run(make_routine, scripted, [
        (PARENT, subtask("CHILD-N: one step.", label="step", workflow="no-such-pattern")),
        ("CHILD-N", finish(summary="did the step")),
        (PARENT, wait_(n=1)),
        (PARENT, finish(summary="done")),
    ], slug="degradeevent")
    assert status == "ok"
    rows = [r for r in _events(Path(d).parent) if r.get("event") == EVENT]
    assert rows, f"no {EVENT} row: {[r.get('event') for r in _events(Path(d).parent)]}"
    row = rows[-1]
    assert row["routine"] == "degradeevent", row          # WHO asked
    assert row.get("recipe") == "no-such-pattern", row    # WHICH pattern
    assert "no-such-pattern" in row["detail"], row        # and the cause, in the detail
    assert row.get("run_id"), "the row must name the parent run that asked"


def test_a_pattern_that_materializes_files_nothing(tmp_path, monkeypatch):
    """The event is an incident, so it may not fire on the ordinary case — a stream that logs
    every successful spawn is one nobody reads. A unit check rather than a loop run: the loop
    fixtures stand up no library at all, so EVERY pattern degrades there and the negative case is
    unprovable through them, while the materializer is the shared seam both callers go through.
    """
    from rsched.engine import childrun
    from rsched.workflows import adapt

    server = tmp_server(tmp_path)
    (Path(server.routines_home) / ".control").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(adapt, "materialize", lambda home, slug: ("# the pattern\n", {}))
    sub = tmp_path / "sub" / "1"
    sub.mkdir(parents=True)

    slug, note = childrun.materialize_to_disk(server, "general-task", sub, "do the thing",
                                              parent_slug="nodegrade", parent_run_id="r1")
    assert (slug, note) == ("general-task", ""), "a pattern that materialized is not a degrade"
    assert "# the pattern" in (sub / "main.md").read_text(encoding="utf-8")
    assert not [r for r in _events(server.routines_home) if r.get("event") == EVENT]


def test_the_event_is_in_the_documented_vocabulary():
    """Every reader filters on `health_vocab.EVENTS`; an undocumented event is written and read
    by nobody."""
    assert EVENT in health_vocab.EVENTS
    assert EVENT in (health_vocab.__doc__ or ""), "the vocabulary entry says what the row means"
