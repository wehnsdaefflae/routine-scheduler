"""The PRE-MIRROR reminder backfill (D152 step 3) — and what it must refuse to touch.

Three classes drift between a routine's reminder store and the hook stream, and only one of
them is a gap a backfill may close. The tests here are the three distinctions, because getting
them wrong is worse than the drift: resurrecting a deleted reminder changes what HOLDS a run,
and inventing an `owner` changes who may remove it.
"""

from __future__ import annotations

import json

from rsched import reminders as store
from rsched.hooks import reminderproj
from rsched.hooks import store as hookstore
from rsched.migrate_premirror_reminders import RECORD, missing_hooks, routine_dirs, run_migration


def _rem(rid, *, regex="^util:danger", desc="it deletes the target", owner=""):
    return store.Reminder(id=rid, regex=regex, description=desc, scope="local", kind="action",
                          owner=owner, created_run="probe:20261001-010101",
                          stats=store.blank_stats())


def _server(home):
    from types import SimpleNamespace

    return SimpleNamespace(routines_home=home)


def test_a_store_only_definition_is_what_the_backfill_closes(make_routine):
    """The 95-id class: a definition written before the mirror existed, so no hook was ever
    appended. After the migration the comparator must read EMPTY for that routine.
    """
    d = make_routine(slug="premirror")
    store.save_local(d, [_rem("rem-20260901-010101-1")], {})
    assert [r["id"] for r in missing_hooks(d.parent, d)] == ["rem-20260901-010101-1"]

    record = run_migration(_server(d.parent))

    assert record["filled"] == {"premirror": ["rem-20260901-010101-1"]}
    assert reminderproj.drift(d.parent, d) == {}, (
        "the backfill ran and the store is still not a derived view")


def test_a_deleted_reminders_stream_row_is_never_resurrected(make_routine):
    """The 23-id `stream-only` class, and the one that would do real harm. A hook with no store
    entry is a DELETION the stream recorded (append-only: the absence is the fact). Appending a
    definition for it would re-raise a pattern its own routine removed — and a reminder decides
    whether an action is HELD, so that is a change to what binds a run.
    """
    d = make_routine(slug="deleted")
    hookstore.record_reminder(d.parent, record=_rem("rem-20261101-010101-1").as_record(),
                              routine="deleted", run_id="deleted:20261101-010101")
    before = reminderproj.drift(d.parent, d)
    assert list(before) == ["rem-20261101-010101-1"]
    assert missing_hooks(d.parent, d) == [], "a stream-only id was offered to the backfill"

    run_migration(_server(d.parent))

    assert reminderproj.drift(d.parent, d) == before, (
        "the backfill touched a stream-only id — a deletion must stay deleted")
    assert store.load_local(d)[0] == [], "a deleted reminder came back into the store"


def test_a_foreign_hook_is_recorded_with_its_owner_as_the_origin(make_routine):
    """`owner` decides who may REMOVE a hook, and the projection reads it off the hook's ORIGIN
    while reading the HOLDER off its addressee. Recording a sibling's hook as the holder's own
    would turn a store-only id into a `differs` one — the comparator made worse by its repair.
    """
    d = make_routine(slug="holder")
    store.save_local(d, [_rem("rem-20260915-010101-1", owner="sibling")], {})

    run_migration(_server(d.parent))

    assert reminderproj.drift(d.parent, d) == {}, (
        "the backfilled hook disagrees with the store it was built from")
    derived = reminderproj.definitions(d.parent, routine="holder")
    assert derived["rem-20260915-010101-1"]["owner"] == "sibling"


def test_the_migration_runs_once_and_says_what_it_did(make_routine):
    """A one-shot that re-ran would append a second hook for every id it already filled — and
    the fold being last-wins would hide that, so the record is the only guard.
    """
    d = make_routine(slug="once")
    store.save_local(d, [_rem("rem-20260902-010101-1")], {})
    first = run_migration(_server(d.parent))
    assert first["filled_total"] == 1

    rows_after_first = len(hookstore.read_hooks(d.parent))
    assert run_migration(_server(d.parent)) == {}, "the migration ran twice"
    assert len(hookstore.read_hooks(d.parent)) == rows_after_first

    written = json.loads((d.parent / RECORD).read_text())
    assert written["filled_total"] == 1 and written["at"]


def test_a_disabled_routines_store_is_backfilled_too(make_routine):
    """The comparator compares every installed routine, so skipping a disabled one would leave
    the property false for a reason nobody could see from the number.
    """
    d = make_routine(slug="asleep")
    cfg = (d / "routine.yaml").read_text().replace("enabled: true", "enabled: false")
    (d / "routine.yaml").write_text(cfg)
    store.save_local(d, [_rem("rem-20260903-010101-1")], {})

    assert d in routine_dirs(d.parent)
    assert run_migration(_server(d.parent))["filled_total"] == 1
    assert reminderproj.drift(d.parent, d) == {}
