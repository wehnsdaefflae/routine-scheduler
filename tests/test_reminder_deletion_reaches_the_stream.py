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


def test_the_consoles_delete_reaches_the_stream_too(api_client, make_routine):
    """The CONSOLE's delete — the user's own lever — must reach the stream as well.

    The run's `remind op=delete` was the half D152 step 3 fixed; `DELETE
    /api/routines/{slug}/reminders/{rid}` wrote the raw store and nothing else. Now that the
    reminder half SERVES definitions from the stream, that is not cosmetic drift: the next read
    folds a definition the user deleted and the pattern keeps holding actions, under a
    `{"ok": true}` the user already saw. A reminder decides whether an action is HELD, so a
    deletion missing from the stream changes what binds a run.

    The endpoint has no run `loop`, so it appends through `hooks.store.record_reminder`
    directly, with the routine as origin and no run id — the honest record of "the console did
    this". What this pins is the PROPERTY, not the mechanism: the comparator reads empty.
    """
    c, _tmp = api_client
    d = make_routine(slug="consoledel")
    (d / "state").mkdir(exist_ok=True)
    kept = store.Reminder(id="rem-20260101-010101-2", regex="^util:b", description="stays",
                          scope="local", created_run="r:1", stats=store.blank_stats())
    doomed = store.Reminder(id="rem-20260101-010101-1", regex="^shell: rm -rf",
                            description="it removes a tree nothing restores", scope="local",
                            created_run="r:1", stats=store.blank_stats())
    store.save_local(d, [doomed, kept], {})
    # the write's own row, as the run that set it would have left on the stream
    hookstore.record_reminder(d.parent, record=doomed.as_record(), routine="consoledel",
                              run_id="consoledel:20260101-010101")
    hookstore.record_reminder(d.parent, record=kept.as_record(), routine="consoledel",
                              run_id="consoledel:20260101-010101")

    assert c.delete(f"/api/routines/consoledel/reminders/{doomed.id}").json() == {
        "ok": True, "remaining": 1}

    assert [r.id for r in store.load_local(d)[0]] == [kept.id]
    assert reminderproj.drift(d.parent, d) == {}, (
        "the console's delete did not reach the hook stream — a stream-served read would "
        "re-raise a reminder the user deleted")
    rows = [h for h in hookstore.read_hooks(d.parent)
            if h.source == "reminder" and h.id == doomed.id]
    assert rows[-1].disposition == "remove", [(h.id, h.disposition) for h in rows]
    assert rows[0].disposition != "remove", "the write's own row was rewritten, not added to"
    # the reminder that stayed is untouched on both sides
    assert [h.disposition for h in hookstore.read_hooks(d.parent)
            if h.source == "reminder" and h.id == kept.id] == ["keep"]


def test_the_live_set_a_run_holds_is_served_from_the_stream(make_routine):
    """D152/b4, the load-bearing cutover: what HOLDS a run comes from the hook stream.

    The health view is a page; THIS is the set `engine/remind` matches every action against, so
    a definition read from the file meant any writer outside the one write path could decide
    whether an action is held. Both stores agree in every fixture written through that path, so
    the test makes them DISAGREE on purpose and asserts the live set follows the STREAM while
    the tally stays the store's.

    It also pins the materialization the mutation paths depend on: a stream-only definition is
    in the live set AND has gained a real row by the time `active` returns, so `record`,
    `dispose` and `set_for` can reach it (hazard 3).
    """
    d = make_routine(slug="holds")
    (d / "state").mkdir(exist_ok=True)
    rid = "rem-20260101-010101-9"
    stale = store.Reminder(id=rid, regex="^util:STALE", description="the stored wording",
                           scope="local", kind="action", created_run="r:1",
                           stats={"fires": 4, "could_not": 0, "would_have": 3, "did": 0,
                                  "didnt": 0})
    store.save_local(d, [stale], {})
    hookstore.record_reminder(
        d.parent, record={**stale.as_record(), "regex": "^shell: rm -rf",
                          "description": "the stream's wording"},
        routine="holds", run_id="holds:20260101-010101")
    # …and one the stream knows about that the store has never seen
    only = store.Reminder(id="rem-20260101-010101-10", regex="^util:fresh",
                          description="stream only", scope="local", kind="action",
                          created_run="r:2", stats=store.blank_stats())
    hookstore.record_reminder(d.parent, record=only.as_record(), routine="holds",
                              run_id="holds:20260101-010101")

    # the POSITIONAL signature every existing caller uses, deliberately: this must fail on an
    # uncut tree because of WHERE the definitions came from, not because a keyword is unknown
    live = store.active(d, d.parent / "_library" / "reminders", "local", (), "holds:20260102")

    held = {r.id: r for r in live}
    assert held[rid].regex == "^shell: rm -rf", (
        "the live set still reads its definitions from the store — a writer outside the hook "
        "path could decide what holds a run")
    assert held[rid].stats["fires"] == 4 and held[rid].stats["would_have"] == 3, (
        "the stored tally was lost; the stream carries no counter to replace it")
    assert only.id in held, "a stream-only definition is not in the live set"
    # hazard 3: it has a real row now, so the raw tally/dispose/cap paths reach it
    assert store.record(d, held[only.id], "fires")["fires"] == 1
    assert store.dispose(d, only.id, "mute", "holds:20260102") == "mute"


def test_the_health_view_serves_the_streams_definition_with_the_stored_tally(make_routine):
    """D152/b4: the health view is CUT OVER — local definitions come from the hook stream while
    the tally stays the store's.

    A cutover that only keeps the old tests green proves nothing: both sides agree in every
    fixture where the store was written through the write path. So this makes them DISAGREE on
    purpose — the stream carries the current pattern, the store an older one with a real tally —
    and asserts the payload serves the stream's regex and the store's count. Reading the
    definition from the file was what let any writer outside the one path decide what holds a
    run; the tally cannot come from the stream at all, because `record_reminder` deliberately
    does not mirror a counter.
    """
    from rsched.config import ServerConfig
    from rsched.readmodels import run_health

    d = make_routine(slug="served")
    (d / "state").mkdir(exist_ok=True)
    rid = "rem-20260101-010101-5"
    stale = store.Reminder(id=rid, regex="^util:OLD", description="the stored wording",
                           scope="local", kind="action", created_run="r:1",
                           stats={"fires": 7, "could_not": 1, "would_have": 2, "did": 0,
                                  "didnt": 0})
    store.save_local(d, [stale], {})
    current = store.Reminder(**{**stale.as_record(), "regex": "^util:CURRENT",
                                "description": "the stream's wording"})
    hookstore.record_reminder(d.parent, record=current.as_record(), routine="served",
                              run_id="served:20260101-010101")

    payload = run_health.cautions(ServerConfig(routines_home=d.parent), d)

    row = next(r for r in payload["reminders"] if r["id"] == rid)
    assert row["regex"] == "^util:CURRENT", (
        "the health view still reads the definition from the store, not from the stream")
    assert row["description"] == "the stream's wording"
    assert row["stats"]["fires"] == 7 and row["stats"]["would_have"] == 2, (
        "the stored tally was lost — the stream carries no counter to replace it")


def test_a_holders_disposition_does_not_take_over_a_foreign_hooks_ownership(make_routine):
    """D152/b4 cutover hazard 2: the HOLDER's say over a foreign hook must not make it the
    OWNER of that hook.

    `hookmirror.reminder_disposed` records the target's keep/mute/remove with `routine` AND
    `target` both the holder — correctly, because the holder is who acted — while
    `reminderproj.definitions` derives `owner` from the row's ORIGIN. So the later row read as
    "the holder owns this", overwriting the sibling that set it. `owner` is what decides who
    may REMOVE a hook, and the store still records the real setter, so the projection both
    transferred ownership and made `drift` report a disagreement that was not one.

    Driven at the stream writer rather than through a scripted cross-routine run: the holder's
    feedback path is covered by `test_reminder_crossroutine*`, and what is pinned here is the
    DERIVATION, which is the thing that was wrong.
    """
    d = make_routine(slug="holder")
    foreign = store.Reminder(id="rem-20260101-010101-7", regex="^util:send",
                             description="it speaks as him to a real person", scope="global",
                             owner="sibling", kind="action", created_run="sibling:1",
                             stats=store.blank_stats())
    # the sibling SETS the hook on `holder` — addressed to the holder, origin the sibling
    hookstore.record_reminder(d.parent, record=foreign.as_record(), routine="sibling",
                              run_id="sibling:20260101-010101", target="holder")
    first = reminderproj.definitions(d.parent, routine="holder")[foreign.id]
    assert first["owner"] == "sibling", first

    # the HOLDER then mutes it for the rest of its run — its own say, not a transfer of title
    hookstore.record_reminder(d.parent, record=foreign.as_record(), routine="holder",
                              run_id="holder:20260101-020202", target="holder",
                              disposition="mute")

    after = reminderproj.definitions(d.parent, routine="holder")[foreign.id]
    assert after["disposition"] == "mute", after
    assert after["owner"] == "sibling", (
        "the holder's disposition took over the hook's ownership — `owner` decides who may "
        "remove it, and the sibling that set it is still its owner")


def test_only_a_local_deletion_is_mirrored_as_this_routines_own(make_routine):
    """A curated reminder lives in the library, which every routine reads, so its deletion is
    recorded where that store's history is — the library git commit `remind_write.commit`
    makes. A `self`-addressed hook row for it would make one routine's view of a SHARED store
    read as its own local fact.

    Driven at the write function rather than through a scripted run: a global op goes through
    the approval ladder, and scripting that dialogue would test the ladder, not this.
    """
    from types import SimpleNamespace

    from rsched.engine import remind_write

    d = make_routine(slug="curator")
    glob = store.Reminder(id="rem-20260101-010101-9", regex="^shell: curl",
                          description="it reaches the network from inside a jail",
                          scope="global", kind="action", owner="", created_run="c:1",
                          stats=store.blank_stats())
    reminders_home = d.parent / "_library" / "reminders"
    reminders_home.mkdir(parents=True, exist_ok=True)
    store.write_global(reminders_home, glob)
    loop = SimpleNamespace(
        reminders=[glob],
        ctx=SimpleNamespace(routine=SimpleNamespace(slug="curator", dir=d),
                            server=SimpleNamespace(routines_home=d.parent,
                                                   reminders_home=reminders_home),
                            run_id="curator:20260905-090000"))

    remind_write.remove(loop, glob)

    assert loop.reminders == []
    assert [h for h in hookstore.read_hooks(d.parent) if h.source == "reminder"] == [], (
        "a GLOBAL reminder's deletion was recorded as this routine's own local hook")
    assert reminderproj.drift(d.parent, d) == {}
