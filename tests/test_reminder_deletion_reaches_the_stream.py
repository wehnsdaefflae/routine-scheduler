"""D152 step 3: a routine DELETING its own reminder reaches the hook stream.

The campaign's end state for the reminder half is the store SERVED from the stream, and that
rests on one property — `hooks/reminderproj.drift` reads EMPTY — which this is the missing half
of. Every other act on a reminder already reached the stream: the write (`remind_write.persist`),
the target's say over a foreign hook (`hookmirror.reminder_disposed`), the curator's disposal of
a row in another routine's store (`hookmirror.reminder_curated`, D231). A routine deleting its
OWN reminder wrote the store and nothing else.

MEASURED on the live instance 2026-11-14, over all 31 routines: **23 ids exist as a hook and in
no store**, five of them from November — and one of those five was deleted by the very run that
measured it. That is not cosmetic drift. A reminder decides whether an action is HELD, so a read
served from a stream that cannot see deletions would re-raise patterns their own routines
removed: it would change what binds a run, silently, which is the opposite of what the hook
store exists for.

The 95 ids that exist in a store with NO hook are a different class with a different fix (they
all predate the mirror — newest `rem-20261018-…`, the mirror landed 2026-10-19 — so they need a
one-shot backfill, not a write-path change) and are deliberately not what this pins.
"""

from __future__ import annotations

from conftest import finish, write_file
from helpers import prompt_text, server_for, set_capabilities
from rsched import reminders as store
from rsched.engine.runtime import run_routine
from rsched.hooks import reminderproj
from rsched.hooks import store as hookstore
from test_reminders import TS

#: What `reminders.new_id` produces for the first reminder a run of `TS` writes.
FIRST_ID = f"rem-{TS}-1"


def test_deleting_your_own_reminder_leaves_the_derived_view_empty(make_routine, scripted):
    """The property the cutover needs, end to end: add, then delete, then compare the two
    stores — the comparator must report NOTHING, because the deletion is on the stream too.
    """
    d = make_routine(slug="deleter")
    set_capabilities(d, reminders="local")
    ep = scripted([
        {**write_file("state/a.txt", content="a"),
         "remind": {"op": "add", "regex": "^shell: rm -rf",
                    "description": "it removes a tree nothing restores"}},
        {**write_file("state/b.txt", content="b"),
         "remind": {"op": "delete", "id": FIRST_ID}},
        finish()])
    status, _run_dir = run_routine(d, server_for(d), run_ts=TS)

    assert f"{FIRST_ID} deleted (local)" in prompt_text(ep)
    assert store.load_local(d)[0] == [], "the local store still holds the deleted reminder"
    assert reminderproj.drift(d.parent, d) == {}, (
        "the stream and the store disagree after a deletion — a stream-served read would "
        "re-raise a reminder this routine deleted")
    assert status == "ok"


def test_the_deletion_is_a_later_row_and_does_not_erase_the_write(make_routine, scripted):
    """The stream is append-only: a deletion is a later FACT about the id, so the definition's
    own row stays readable and "when was this pattern removed, and by whose run" stays
    answerable. A fix that rewrote the original row would lose exactly that.
    """
    d = make_routine(slug="appendonly")
    set_capabilities(d, reminders="local")
    scripted([
        {**write_file("state/a.txt", content="a"),
         "remind": {"op": "add", "regex": "^util:send",
                    "description": "it speaks as him to a real person"}},
        {**write_file("state/b.txt", content="b"),
         "remind": {"op": "delete", "id": FIRST_ID}},
        finish()])
    run_routine(d, server_for(d), run_ts=TS)

    rows = [h for h in hookstore.read_hooks(d.parent)
            if h.source == "reminder" and h.id == FIRST_ID]
    assert len(rows) == 2, [(h.id, h.disposition) for h in rows]
    assert rows[0].disposition != "remove", "the original write was rewritten, not added to"
    assert rows[-1].disposition == "remove", [(h.id, h.disposition) for h in rows]
    assert rows[0].trigger.get("regex") == "^util:send", (
        "the write's own row no longer carries the pattern it defined")


def test_a_global_reminders_deletion_is_not_claimed_as_a_local_fact(make_routine, scripted):
    """A curated reminder lives in the library, which every routine reads, and its deletion is
    recorded where that store's history is — the library git commit. A `self`-addressed row for
    it would make one routine's view of a shared store look like its own local fact, and
    `drift` (local scope only) must stay empty either way.
    """
    d = make_routine(slug="curator")
    set_capabilities(d, reminders="global")
    scripted([{**write_file("state/c.txt", content="c"),
               "remind": {"op": "add", "scope": "global", "regex": "^shell: curl",
                          "description": "it reaches the network from inside a jail"}},
              finish()])
    run_routine(d, server_for(d), run_ts=TS)

    assert reminderproj.drift(d.parent, d) == {}, (
        "a global reminder leaked into the LOCAL comparison")
