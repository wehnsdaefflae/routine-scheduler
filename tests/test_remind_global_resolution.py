"""Curating a GLOBAL reminder this routine does not hold (D186 / R2530).

The live set is "every curated reminder that REACHES this routine, plus its own". A
`reach: listed` global row is in no non-listing routine's live set by construction — so a
curator that does not list it could never name an id `delete` would accept, and the row was
permanent unless the operator removed the file by hand. `rules-review`'s recipe assigns it
exactly that act ("a dead shared one you delete yourself"), so the recipe and the engine's
lookup contradicted each other for every listed shared row.

The fix resolves `rid` against the SHARED STORE when the op names `scope: global` and the live
set misses. Nothing about AUTHORITY moves: the scope-mismatch guard, `grants.reminder_denial`,
the global approval step and the library commit all still run on the resolved target — which is
what the last two tests here pin.
"""

import json

import pytest

from conftest import finish, write_file
from helpers import prompt_text, reminder, server_for, set_capabilities
from rsched import reminders as store
from rsched.engine.actions import validate_action
from rsched.engine.runtime import run_routine
from rsched.policyload import load_policy

TS = "20260905-090000"

#: A curated row with `reach: listed` that the routine under test does NOT list, so it can
#: never appear in that run's live set — the shape the old lookup could not reach.
UNLISTED = "rem-20261002-013125-2"


def _curated(d, *, rid=UNLISTED, reach="listed", regex="^util:web-request .*--out ",
             desc="it overwrites the destination without asking"):
    home = server_for(d).reminders_home
    store.write_global(home, reminder(rid=rid, regex=regex, desc=desc, scope="global",
                                      reach=reach))
    return home


def test_a_curator_deletes_a_listed_global_row_it_does_not_itself_hold(make_routine, scripted):
    """The whole point: the reach gate decides when a row FIRES, not who may curate it."""
    d = make_routine(slug="remcur")
    home = _curated(d)
    set_capabilities(d, reminders="global", remind_confirm="never")
    ep = scripted([{**write_file("state/a.txt"),
                    "remind": {"op": "delete", "scope": "global", "id": UNLISTED}},
                   finish()])
    status, _run_dir = run_routine(d, server_for(d), run_ts=TS)
    assert store.records(home) == []                       # the row is GONE from the library
    assert not store.global_path(home, UNLISTED).exists()
    shown = prompt_text(ep)
    assert f"{UNLISTED} deleted (global)" in shown
    assert status == "ok"


def test_a_curator_revises_a_listed_global_row_it_does_not_itself_hold(make_routine, scripted):
    """`revise` travels the same lookup, so it was unreachable for the same reason."""
    d = make_routine(slug="remcur")
    home = _curated(d)
    set_capabilities(d, reminders="global", remind_confirm="never")
    scripted([{**write_file("state/a.txt"),
               "remind": {"op": "revise", "scope": "global", "id": UNLISTED,
                          "description": "the util refuses this now — see R2390"}},
              finish()])
    status, _run_dir = run_routine(d, server_for(d), run_ts=TS)
    rec = json.loads(store.global_path(home, UNLISTED).read_text(encoding="utf-8"))
    assert rec["description"] == "the util refuses this now — see R2390"
    assert rec["regex"] == "^util:web-request .*--out "     # unnamed fields keep their value
    assert rec["reach"] == "listed"
    assert status == "ok"


def test_the_two_misses_are_textually_distinguishable(make_routine, scripted):
    """A typo and a reach gate must not read the same. `scope: global` on an id the LIBRARY
    does not hold says so about the library; without `scope: global` the refusal is about this
    run's live set and names the way out."""
    d = make_routine(slug="remcur")
    _curated(d)
    set_capabilities(d, reminders="global", remind_confirm="never")
    ep = scripted([{**write_file("state/a.txt"),
                    "remind": {"op": "delete", "scope": "global", "id": "rem-20200101-000000-9"}},
                   {**write_file("state/b.txt"),
                    "remind": {"op": "delete", "id": UNLISTED}},
                   finish()])
    status, _run_dir = run_routine(d, server_for(d), run_ts=TS)
    shown = prompt_text(ep)
    assert "is in the shared store" in shown                # the library was consulted
    assert "is live for this run" in shown                  # the other miss, named differently
    assert "name `scope: global`" in shown                  # …with the way out
    assert status == "ok"


def test_the_capability_dial_refuses_the_op_before_resolution_is_reached(tmp_path):
    """AUTHORITY does not move with resolution, and it is asked EARLIER than the lookup: a
    `scope: global` op from a routine at `local` is a field problem, so it never becomes a turn
    and the shared store is never read. Naming the scope is what makes the library reachable —
    it is also what the dial gates."""
    local_only = load_policy(tmp_path, [], {"reminders": "local"})
    problems = validate_action({"say": "s", "kind": "report", "title": "t", "detail": "d",
                                "remind": {"op": "delete", "scope": "global", "id": UNLISTED}},
                               grants=local_only)
    assert problems
    assert "writing the shared store is the curator's setting" in problems[0]


@pytest.mark.parametrize("reach", ["listed", "universal"])
def test_resolution_from_the_shared_store_still_asks_for_approval(make_routine, scripted, reach):
    """The blast-radius gate is unchanged — including for a row the run never held.

    The note proves BOTH halves at once: the op got past resolution (an unresolved id would
    have come back "is in the shared store" / "is live for this run" instead) and then stopped
    at the approval, with the library untouched.
    """
    d = make_routine(slug="remcur")
    home = _curated(d, reach=reach)
    set_capabilities(d, reminders="global", remind_confirm="always")
    ep = scripted([{**write_file("state/a.txt"),
                    "remind": {"op": "delete", "scope": "global", "id": UNLISTED}},
                   finish()])
    status, _run_dir = run_routine(d, server_for(d), run_ts=TS)
    assert [r["id"] for r in store.records(home)] == [UNLISTED]      # nothing applied
    shown = prompt_text(ep)
    assert f"the approval for delete of global reminder {UNLISTED} is still open" in shown
    assert "the store is unchanged" in shown
    assert status == "ok"
