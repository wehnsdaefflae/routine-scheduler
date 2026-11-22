"""The hook stream's two readings: what fires next, and who spoke into which run. Every row
here is written through the REAL writers (`hooks/scheduleproj`, `hooks/store`), so the fold is
tested against the shapes the system actually appends rather than against a shape this file
invented."""

from datetime import UTC, datetime, timedelta

from conftest import authed_client, make_test_server
from helpers import tmp_server
from rsched.hooks import scheduleproj, store
from rsched.readmodels import hook_activity, memo


def _request(hook_id: str, *, hours: float, reason: str = "re-check the seat",
             requested_by: str = "ui") -> dict:
    """One armed one-shot's spool record, as `schedule_once` writes it."""
    fire = datetime.now(UTC) + timedelta(hours=hours)
    return {"id": hook_id, "fire_at": fire.isoformat(), "reason": reason,
            "requested_by": requested_by, "created": datetime.now(UTC).isoformat()}


def _arm(server, hook_id, *, slug="alpha", hours=2.0, **over) -> None:
    scheduleproj.record_armed(server.routines_home, _request(hook_id, hours=hours, **over), slug)
    memo.reset()


def _end(server, hook_id, event) -> None:
    scheduleproj.record_event(server.routines_home, hook_id=hook_id, event=event)
    memo.reset()


def _inject(server, *, via, text="look at the gate", run_id="alpha:20261122-090000",
            turn=4) -> None:
    store.record_injection(server.routines_home, run_id=run_id, turn=turn, via=via, text=text,
                           routine="alpha")
    memo.reset()


def _runtree(server, *, delivered, addressee="parent", title="the fold is green",
             run_id="alpha:20261122-090000", turn=7) -> None:
    store.record_runtree(server.routines_home, hook_id=f"rt-{turn}", routine="alpha",
                         run_id=run_id, turn=turn, addressee=addressee, title=title,
                         delivered=delivered)
    memo.reset()


# ---- next_fires: the PENDING fold comes first, the ordering second ------------------------

def test_an_armed_then_fired_one_shot_is_not_what_fires_next(tmp_path):
    """The ending is a LATER row about the same id, never a change to the arming one, so a fold
    over raw rows would list every one-shot that ever fired as overdue."""
    server = tmp_server(tmp_path)
    _arm(server, "so-fired", hours=-3)
    _arm(server, "so-live", hours=2)
    _end(server, "so-fired", "fired")

    rows = hook_activity.next_fires(server)

    assert [r["hook"] for r in rows] == ["so-live"]


def test_an_armed_then_cancelled_one_shot_is_not_what_fires_next(tmp_path):
    server = tmp_server(tmp_path)
    _arm(server, "so-gone", hours=1)
    _arm(server, "so-live", hours=2)
    _end(server, "so-gone", "cancelled")

    assert [r["hook"] for r in hook_activity.next_fires(server)] == ["so-live"]


def test_an_armed_one_shot_past_its_instant_is_shown_as_overdue_never_dropped(tmp_path):
    """A pending row whose instant has passed is a one-shot the daemon has not consumed yet."""
    server = tmp_server(tmp_path)
    _arm(server, "so-late", hours=-5)

    [row] = hook_activity.next_fires(server)

    assert row["hook"] == "so-late" and row["overdue"] is True


def test_the_pending_one_shots_come_soonest_first_with_their_provenance(tmp_path):
    server = tmp_server(tmp_path)
    _arm(server, "so-later", hours=10, reason="the weekly re-read", requested_by="self-audit")
    _arm(server, "so-sooner", hours=1, reason="re-check the seat", requested_by="ui")

    rows = hook_activity.next_fires(server)

    assert [r["hook"] for r in rows] == ["so-sooner", "so-later"]
    assert rows[0]["routine"] == "alpha" and rows[0]["reason"] == "re-check the seat"
    assert rows[0]["requested_by"] == "ui" and rows[0]["overdue"] is False
    assert rows[1]["fires_at"] > rows[0]["fires_at"]


def test_a_one_shot_beyond_the_horizon_is_not_in_the_window(tmp_path):
    server = tmp_server(tmp_path)
    _arm(server, "so-far", hours=72)

    assert hook_activity.next_fires(server) == []
    assert [r["hook"] for r in hook_activity.next_fires(server, horizon_h=96)] == ["so-far"]


def test_a_skipped_one_shot_ends_it_like_a_fire(tmp_path):
    """All three of `scheduleproj.TERMINAL_EVENTS` end a one-shot: it is consumed whichever way
    it ends, so no row about it is ever owed a later fact."""
    server = tmp_server(tmp_path)
    _arm(server, "so-missed", hours=-1)
    _end(server, "so-missed", "skipped")

    assert hook_activity.next_fires(server) == []


# ---- deliveries: who spoke into which run ------------------------------------------------

def test_an_injections_via_is_carried_verbatim_and_rendered_in_words(tmp_path):
    server = tmp_server(tmp_path)
    _inject(server, via="web-converse", text="stop and read the gate\nsecond line")

    [row] = hook_activity.deliveries(server)

    assert row["via"] == "web-converse"
    assert row["who"] == "you, from the run view's converse box"
    assert row["person"] is True
    assert row["run_id"] == "alpha:20261122-090000" and row["turn"] == 4
    assert row["text"] == "stop and read the gate"


def test_a_machine_channel_is_not_a_person_speaking(tmp_path):
    """`channels.user_authored` is the one answer for every consumer — a report, a hand-back or
    an escalation rung is not the operator typing."""
    server = tmp_server(tmp_path)
    _inject(server, via="oversight", text="you are off the brief")

    [row] = hook_activity.deliveries(server)

    assert row["via"] == "oversight" and row["who"] == "an escalation rung"
    assert row["person"] is False


def test_an_unknown_channel_still_reads_rather_than_rendering_blank(tmp_path):
    """The channel set grows; a row written by a newer build must still read."""
    server = tmp_server(tmp_path)
    _inject(server, via="a-newer-channel")

    [row] = hook_activity.deliveries(server)

    assert row["via"] == "a-newer-channel" and row["who"] == "a-newer-channel"


def test_a_run_tree_row_with_an_open_disposition_reads_as_undelivered(tmp_path):
    """`store.record_runtree` writes `open` when the inbox write RAISED: the freight never
    arrived, and this row is the only record of that anywhere, since a run-tree hook gets no
    `R<n>` to carry it."""
    server = tmp_server(tmp_path)
    _runtree(server, delivered=False, title="the fold is red")

    [row] = hook_activity.deliveries(server)

    assert row["source"] == "runtree"
    assert row["undelivered"] is True
    assert row["undelivered_note"] == "not delivered — the write failed"
    assert row["text"] == "the fold is red"


def test_a_delivered_run_tree_row_is_not_undelivered_and_names_which_end_spoke(tmp_path):
    server = tmp_server(tmp_path)
    _runtree(server, delivered=True, addressee="child:D305", turn=8)
    _runtree(server, delivered=True, addressee="parent", turn=9)

    rows = hook_activity.deliveries(server)

    assert [r["who"] for r in rows] == ["parent run", "child D305"]
    assert [r["undelivered"] for r in rows] == [False, False]
    assert [r["person"] for r in rows] == [False, False]


def test_the_deliveries_are_newest_first_with_the_streams_order_breaking_a_tie(tmp_path):
    """`ids.now_iso` writes to the SECOND, so these two rows carry the same stamp — a plain
    stable sort would put the older one first in a list whose claim is "newest first", and the
    later row in an append-only stream is the later fact."""
    server = tmp_server(tmp_path)
    _inject(server, via="web", text="first", turn=1)
    _runtree(server, delivered=True, title="then this", turn=2)

    rows = hook_activity.deliveries(server)

    assert [r["text"] for r in rows] == ["then this", "first"]
    assert [r["source"] for r in rows] == ["runtree", "injection"]


def test_a_one_shot_row_is_never_a_delivery_and_a_delivery_never_a_fire(tmp_path):
    """The two folds read the same parse and must not bleed into each other: an armed one-shot
    was not spoken into a run, and an injection does not fire."""
    server = tmp_server(tmp_path)
    _arm(server, "so-live", hours=1)
    _inject(server, via="web")

    assert [r["source"] for r in hook_activity.deliveries(server)] == ["injection"]
    assert [r["hook"] for r in hook_activity.next_fires(server)] == ["so-live"]


def test_an_empty_stream_is_an_empty_reading(tmp_path):
    server = tmp_server(tmp_path)
    assert hook_activity.activity(server) == {"horizon_h": 24, "since_h": 24,
                                              "next_fires": [], "deliveries": []}


# ---- the route ---------------------------------------------------------------------------

def test_the_route_serves_both_lists(tmp_path):
    server = tmp_server(tmp_path)
    _arm(server, "so-live", hours=1)
    _inject(server, via="report", text="R12 is yours")
    with authed_client(make_test_server(tmp_path)) as client:
        got = client.get("/api/hook-activity?horizon_h=24&since_h=24")

    assert got.status_code == 200
    payload = got.json()
    assert [r["hook"] for r in payload["next_fires"]] == ["so-live"]
    assert [r["who"] for r in payload["deliveries"]] == ["a report"]
    assert payload["horizon_h"] == 24 and payload["since_h"] == 24


def test_the_route_is_the_operators_not_a_runs(tmp_path):
    """The deliveries list carries the first line of everything typed into every run on the
    instance — the same cross-routine reading `ROUTINE_TOKEN_DENIED_READS` keeps out of a run's
    reach."""
    from fastapi.testclient import TestClient

    from rsched.web.app import create_app

    server = make_test_server(tmp_path, routine_token="routine-tok")
    with TestClient(create_app(server, with_scheduler=False)) as client:
        denied = client.get("/api/hook-activity",
                            headers={"Authorization": "Bearer routine-tok"})
    assert denied.status_code == 403
