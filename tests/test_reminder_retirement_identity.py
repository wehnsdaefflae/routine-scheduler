"""F908 — a retired reminder stops firing at once, and its id is never handed out again."""

from __future__ import annotations

import json

from conftest import finish, write_file
from helpers import prompt_text, server_for, set_capabilities
from rsched import remind_dead
from rsched import reminders as store
from rsched.engine.runtime import run_routine
from test_reminders import TS

FIRST = f"rem-{TS}-1"


def _rem(rid, regex="^util:a", **kw):
    base = {"id": rid, "regex": regex, "description": "a caution", "scope": "local",
            "created_run": "r:1", "kind": "action", "owner": "", "disposition": "keep",
            "muted_run": "", "stats": store.blank_stats()}
    base.update(kw)
    return store.Reminder(**base)


def test_a_retired_reminder_stops_holding_actions_in_the_same_run(make_routine, scripted):
    """The 16m58s window, closed: the row leaves the live set the moment it is retired."""
    d = make_routine(slug="retire")
    set_capabilities(d, reminders="global")   # a `dead` op is the curator's grant, even self-targeted
    ep = scripted([
        {**write_file("state/a.txt", content="a"),
         "remind": {"op": "add", "regex": "^write_file path=state/b.txt",
                    "description": "it overwrites the file the next step reads"}},
        {**write_file("state/c.txt", content="c"),
         "remind": {"op": "dead", "id": FIRST, "target": "retire",
                    "description": "the predicate was fixed, so the caution is withdrawn"}},
        write_file("state/b.txt", content="b"),      # the pattern the RETIRED row matches
        finish()])
    status, _run_dir = run_routine(d, server_for(d), run_ts=TS)

    text = prompt_text(ep)
    assert "marked DEAD" in text or "fires no more" in text, "the retirement did not take"
    assert "ACTION HELD" not in text.split("fires no more")[-1], (
        "a reminder retired earlier in this run still HELD a later action (F908)")
    assert (d / "state" / "b.txt").read_text() == "b", "the held action never ran"
    assert FIRST in remind_dead.read_dead(d), "the annotation is not in the store"
    assert status == "ok"


def test_a_retired_id_is_never_allocated_again(make_routine):
    """The id-reuse half, driven at the store so it is about allocation and not about a run."""
    d = make_routine(slug="reuse")
    (d / "state").mkdir(exist_ok=True)
    retired = _rem(f"rem-{TS}-1", regex="^script:review_reduce")
    store.save_local(d, [retired], {})
    remind_dead.annotate(d, retired.id, by="reuse", reason="the predicate was fixed",
                         at="2026-11-17T07:29:29+01:00")

    live = remind_dead.live(d, store.load_local(d)[0])
    assert live == [], "the dead filter did not remove the retired row"
    # the defect: the live set is what the old allocator reserved against
    assert store.new_id(TS, {r.id for r in live}) == retired.id

    reserved = remind_dead.reserved_ids(d, {r.id for r in live})
    assert retired.id in reserved, "a retired id was not reserved"
    fresh = store.new_id(TS, reserved)
    assert fresh != retired.id, (
        "the allocator handed out a RETIRED id — the new caution inherits its death marker "
        "and is suppressed on every fresh load (F908)")
    assert fresh == f"rem-{TS}-2"

    # …and a row that is only MUTED keeps its id reserved too: its definition is still there
    muted = _rem(f"rem-{TS}-2", regex="^util:muted", disposition="mute", muted_run="r:9")
    store.save_local(d, [muted], {})
    assert muted.id in remind_dead.reserved_ids(d, set())


def test_reserving_ids_survives_a_store_it_cannot_read(make_routine):
    """Reached while a run is being constructed, so an unreadable store must not raise — it
    reserves what it could read. Reserving too much costs a higher number; too little is the
    defect above."""
    d = make_routine(slug="broken")
    (d / "state").mkdir(exist_ok=True)
    (d / "state" / "reminders.json").write_text("{not json at all")

    assert remind_dead.reserved_ids(d, {"rem-x-1"}) == {"rem-x-1"}
    assert json.loads(json.dumps(sorted(remind_dead.reserved_ids(d, set())))) == []
