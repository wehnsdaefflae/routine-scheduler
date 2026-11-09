"""A one-shot IS a `schedule`-triggered hook — D152 gap 5, the last unused trigger.

`hooks.TRIGGERS` has named four things that make a hook fire since step 1 and three had users;
`schedule` carried a note instead: *"a time. Nothing uses this yet; the field costs nothing and
naming it keeps the next mechanism from being a fourth store."* That next mechanism already
existed AS a fourth store — the one-shot request spool — so the question the campaign exists to
make answerable, *what hooks exist in this instance*, could not be answered for anything
time-fired.

What is pinned here: the projection and its EXACT inverse (a projection whose inverse nobody
wrote is a mirror whatever it is called — `reportinv`'s lesson, applied in the same change this
time), the four lifecycle facts, and that the arming paths really write the row. The spool stays
the SOURCE: the daemon reads only it, consumption is what guarantees a one-shot never repeats,
and a stream write that fails must never stop a one-shot being armed.
"""

import json
from datetime import UTC, datetime, timedelta

from helpers import tmp_server
from rsched import schedule_once
from rsched.hooks import TRIGGERS, scheduleproj
from rsched.hooks import store as hookstore


def _record(fire_at=None, **over):
    """One armed-request record. `fire_at` takes a datetime OR an already-formatted string —
    an earlier version called `.isoformat()` on whatever it was handed, which meant a string
    argument crashed and a default argument produced a DIFFERENT instant on every call, so two
    assertions compared two microsecond-apart stamps.
    """
    if fire_at is None:
        fire_at = datetime.now(UTC) + timedelta(hours=3)
    rec = {"id": "so-abc12345",
           "fire_at": fire_at if isinstance(fire_at, str) else fire_at.isoformat(),
           "reason": "re-check the funder page", "requested_by": "freelance-hunter:20260714",
           "created": "2026-07-14T07:00:00+02:00"}
    rec.update(over)
    return rec


def test_the_schedule_trigger_finally_has_a_user():
    """The gap in one assertion: `schedule` was declared and unused, and this is the hook that
    uses it.
    """
    assert "schedule" in TRIGGERS
    rec = _record()

    hook = scheduleproj.hook_of_request(rec, "freelance-hunter")

    assert hook.trigger["kind"] == "schedule"
    assert hook.trigger["at"] == rec["fire_at"]
    assert hook.source == "schedule_once"
    assert hook.delivery == "fire"


def test_the_addressee_is_the_routine_that_will_run_not_the_one_that_armed_it():
    """A one-shot may target ANOTHER routine (the `schedule_run` action honours that). Reading
    `requested_by` as the addressee would make a cross-armed one-shot look like a message back
    to the arming routine.
    """
    hook = scheduleproj.hook_of_request(
        _record(requested_by="scheduler-builder:20261109"), "do-my-taxes")

    assert hook.addressee == "routine:do-my-taxes"
    assert hook.payload["requested_by"] == "scheduler-builder:20261109"
    assert hook.origin["routine"] == "scheduler-builder:20261109"


def test_the_projection_inverts_exactly():
    """Total over what the projection emits, key for key — what the campaign means by a
    projection rather than a mirror. A row this cannot invert is a row the stream cannot be
    trusted for.
    """
    rec = _record()

    slug, back = scheduleproj.request_of_hook(
        scheduleproj.hook_of_request(rec, "freelance-hunter"))

    assert slug == "freelance-hunter"
    assert back == rec


def test_a_request_the_spool_actually_wrote_round_trips(tmp_path):
    """Against `schedule_once.arm`'s real record, not a hand-built dict: the projection has to
    invert what the spool stores, including the keys a future field would add.
    """
    server = tmp_server(tmp_path)
    fire_at = datetime.now(UTC) + timedelta(days=2)
    rec = schedule_once.arm(server.routines_home, "uir", fire_at=fire_at,
                            reason="check the mirror", requested_by="ui")

    slug, back = scheduleproj.request_of_hook(scheduleproj.hook_of_request(rec, "uir"))

    assert slug == "uir"
    assert back == rec


def test_arming_puts_the_row_on_the_stream(tmp_path):
    """The wiring, not just the function: `record_armed` is what the two arming paths call.
    """
    server = tmp_server(tmp_path)
    rec = _record()

    hook_id = scheduleproj.record_armed(server.routines_home, rec, "uir")

    assert hook_id
    hooks = [h for h in hookstore.read_hooks(server.routines_home)
             if h.source == "schedule_once"]
    assert len(hooks) == 1
    assert hooks[0].trigger == {"kind": "schedule", "at": rec["fire_at"]}
    assert hooks[0].disposition == "open"


def test_each_ending_is_one_later_row_about_the_same_id(tmp_path):
    """An append-only stream says "this ended" with another row about the same id. All three
    endings are terminal, which is the mechanism's substance: a one-shot is consumed whichever
    way it ends, so no row about it is ever owed a later fact.
    """
    server = tmp_server(tmp_path)
    scheduleproj.record_armed(server.routines_home, _record(), "uir")

    scheduleproj.record_event(server.routines_home, hook_id="so-abc12345", event="fired",
                              detail="fired uir as uir:20260714-100000")

    rows = [h for h in hookstore.read_hooks(server.routines_home)
            if h.source == "schedule_once"]
    assert [r.id for r in rows] == ["so-abc12345", "so-abc12345"]
    assert rows[1].trigger["event"] == "fired"
    assert rows[1].disposition == "settled"
    assert scheduleproj.TERMINAL_EVENTS == ("fired", "cancelled", "skipped")


def test_an_event_outside_the_vocabulary_is_refused_not_written(tmp_path):
    """A vocabulary nothing checks is how `trigger_capped` went unread for weeks."""
    server = tmp_server(tmp_path)

    assert scheduleproj.record_event(server.routines_home, hook_id="so-1", event="exploded") == ""
    assert [h for h in hookstore.read_hooks(server.routines_home)
            if h.source == "schedule_once"] == []


def test_a_stream_that_cannot_be_written_never_stops_a_one_shot_being_armed(tmp_path,
                                                                            monkeypatch):
    """The spool is the SOURCE and this row is a diagnostic. The opposite choice from
    `reportproj`, which refuses loudly — there the hook IS the source and a lost row is lost
    work.
    """
    server = tmp_server(tmp_path)
    monkeypatch.setattr(hookstore, "append",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("read-only fs")))

    assert scheduleproj.record_armed(server.routines_home, _record(), "uir") == ""


def test_what_is_about_to_fire_is_answerable_from_the_stream_alone(tmp_path):
    """The question no store could answer before this: the spool answers it per routine (a
    listing then a read each), the stream now answers it for the INSTANCE in one fold.
    """
    server = tmp_server(tmp_path)
    now = datetime.now(UTC)
    for i, (slug, hours) in enumerate([("uir", 1), ("do-my-taxes", 30), ("digest", 5)]):
        scheduleproj.record_armed(
            server.routines_home,
            _record(fire_at=now + timedelta(hours=hours), id=f"so-{i}"), slug)

    armed = [h for h in hookstore.read_hooks(server.routines_home)
             if h.source == "schedule_once" and h.disposition == "open"]
    soon = scheduleproj.due_before(armed, now + timedelta(hours=24))

    assert [h.addressee for h in soon] == ["routine:uir", "routine:digest"], \
        "soonest first; the 30h one is outside the window"


def test_a_row_with_no_readable_instant_is_left_out_rather_than_read_as_due(tmp_path):
    """A store must not be able to break a reader, and an unreadable instant must never sort
    as imminent — the direction every stamp decision in this codebase takes.
    """
    server = tmp_server(tmp_path)
    scheduleproj.record_armed(server.routines_home, _record(id="so-ok"), "uir")
    scheduleproj.record_armed(server.routines_home,
                              {**_record(id="so-bad"), "fire_at": "whenever"}, "uir")

    armed = [h for h in hookstore.read_hooks(server.routines_home) if h.disposition == "open"]

    assert scheduleproj.fires_at(
        next(h for h in armed if h.id == "so-bad")) is None
    assert [h.id for h in scheduleproj.due_before(
        armed, datetime.now(UTC) + timedelta(days=400))] == ["so-ok"]


def test_the_instant_is_compared_as_an_instant_whatever_offset_wrote_it(tmp_path):
    """`ids.now_iso` writes the host's LOCAL time with its offset, so a string compare against
    a UTC cutoff moves the window's edge by that offset — the defect `health_stream`'s header
    records, not to be repeated here.
    """
    server = tmp_server(tmp_path)
    base = datetime.now(UTC) + timedelta(hours=2)
    scheduleproj.record_armed(
        server.routines_home,
        _record(id="so-tz", fire_at=base.astimezone(
            datetime.now().astimezone().tzinfo).isoformat()), "uir")

    armed = [h for h in hookstore.read_hooks(server.routines_home) if h.disposition == "open"]
    when = scheduleproj.fires_at(armed[0])

    assert when is not None
    assert abs((when - base).total_seconds()) < 1


def test_the_spool_file_is_untouched_by_the_projection(tmp_path):
    """"Wrap first, replace last": the daemon reads the spool and nothing else, so arming must
    write exactly the bytes it wrote before this module existed.
    """
    server = tmp_server(tmp_path)
    fire_at = datetime.now(UTC) + timedelta(hours=6)

    rec = schedule_once.arm(server.routines_home, "uir", fire_at=fire_at, reason="r",
                            requested_by="ui")
    path = schedule_once.request_path(server.routines_home, "uir", rec["id"])
    on_disk = json.loads(path.read_text(encoding="utf-8"))

    assert on_disk == rec
    assert set(on_disk) == {"id", "fire_at", "reason", "requested_by", "created"}
