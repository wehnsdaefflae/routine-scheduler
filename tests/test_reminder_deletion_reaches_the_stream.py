"""D152 step 3: a routine DELETING its own reminder reaches the hook stream."""

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
    """The property the cutover needs, end to end: add, then delete — and the stream, which
    is what the read serves, no longer names the reminder.
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
    assert FIRST_ID not in reminderproj.definitions(d.parent, routine="deleter"), (
        "the deletion did not reach the stream — the stream-served read would re-raise a "
        "reminder this routine deleted")
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
    """The CONSOLE's delete — the user's own lever — must reach the stream as well."""
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
    assert sorted(reminderproj.definitions(d.parent, routine="consoledel")) == [kept.id], (
        "the console's delete did not reach the hook stream — the stream-served read would "
        "re-raise a reminder the user deleted")
    rows = [h for h in hookstore.read_hooks(d.parent)
            if h.source == "reminder" and h.id == doomed.id]
    assert rows[-1].disposition == "remove", [(h.id, h.disposition) for h in rows]
    assert rows[0].disposition != "remove", "the write's own row was rewritten, not added to"
    # the reminder that stayed is untouched on both sides
    assert [h.disposition for h in hookstore.read_hooks(d.parent)
            if h.source == "reminder" and h.id == kept.id] == ["keep"]


def test_the_live_set_a_run_holds_is_served_from_the_stream(make_routine):
    """D152/b4, the load-bearing cutover: what HOLDS a run comes from the hook stream."""
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
