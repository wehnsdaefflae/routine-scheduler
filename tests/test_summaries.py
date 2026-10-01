"""A run's finish summary as an item on the Messages page (operator order 2026-09-05).

The Summary page was a read surface nothing linked to, next door to the page that already was
the index of everything the instance has to say. What these pin is that folding it in kept the
two behaviours the old page had earned — Unread by default (2026-08-05) and a bulk sweep (F303)
— while reusing the item vocabulary rather than forking it with a synonym.
"""

from __future__ import annotations

from conftest import mk_run
from helpers import tmp_server
from rsched.readmodels import summaries

TS = "20260905-090000"


def _run(routine_dir, ts, *, summary="", state="finished", outcome="ok"):
    return mk_run(routine_dir, ts, state, outcome=outcome, usage={"in": 10, "out": 4},
                  started=ts, updated="2026-09-05T09:00:00+00:00", summary=summary)


def test_one_row_per_routine_carrying_the_newest_run_with_a_summary(tmp_path, make_routine):
    server = tmp_server(tmp_path)
    d = make_routine(slug="talker")
    _run(d, "20260905-070000", summary="the older one")
    _run(d, "20260905-080000", summary="what it last told you")
    _run(d, "20260905-090000", state="running", summary="")     # no finish message yet

    rows = summaries.build(server)
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == "talker:20260905-080000"          # the run id IS the item id
    assert row["type"] == "summary" and row["status"] == "open"
    assert row["detail"] == "what it last told you"
    assert row["origin"]["routine"] == "talker"


def test_the_list_is_newest_first_by_instant_whatever_form_the_stamp_takes(tmp_path,
                                                                           make_routine):
    """A run that never wrote `updated` (no status.json — a boot that died) is dated by its
    run-ts. Sorted as text, a compact run-ts lands above every ISO stamp, so that routine sat at
    the top of the page however old its run was."""
    server = tmp_server(tmp_path)
    stale = make_routine(slug="stale")
    (stale / "runs" / "20260101-000000").mkdir(parents=True)    # no status.json, no `updated`
    _run(make_routine(slug="fresh"), "20260905-080000", summary="said this week")
    assert [r["origin"]["routine"] for r in summaries.build(server)] == ["fresh", "stale"]


def test_a_routine_that_never_ran_has_nothing_to_say(tmp_path, make_routine):
    server = tmp_server(tmp_path)
    make_routine(slug="quiet")
    assert summaries.build(server) == []


def test_marking_read_is_a_watermark_a_newer_run_clears(tmp_path, make_routine):
    """The store is `{slug: newest run seen}`, so a newer run resurfaces on its own — nothing
    has to go back and clear the old marker."""
    server = tmp_server(tmp_path)
    d = make_routine(slug="talker")
    _run(d, "20260905-080000", summary="first")

    summaries.mark_read(server.routines_home, "talker:20260905-080000", read=True)
    assert summaries.build(server)[0]["status"] == "settled"

    _run(d, "20260905-090000", summary="second")
    row = summaries.build(server)[0]
    assert row["id"] == "talker:20260905-090000" and row["status"] == "open"

    summaries.mark_read(server.routines_home, row["id"], read=False)
    assert summaries.build(server)[0]["status"] == "open"


def test_the_bulk_sweep_marks_every_shown_row(tmp_path, make_routine):
    """F303: with one row per routine and no bulk action, clearing a backlog was one click each."""
    server = tmp_server(tmp_path)
    for slug in ("a", "b", "c"):
        _run(make_routine(slug=slug), "20260905-080000", summary=f"{slug} says hi")
    assert summaries.mark_all_read(server.routines_home, server) == 3
    assert {r["status"] for r in summaries.build(server)} == {"settled"}
    assert summaries.mark_all_read(server.routines_home, server) == 0     # idempotent


def test_the_api_serves_summaries_beside_the_maintenance_items(api_client, make_routine):
    """One page, one filter vocabulary. `type=summary` is what the page defaults to, and the
    existing `open,in_progress` status default lands exactly on the unread ones."""
    c, _tmp = api_client
    d = make_routine(slug="talker")
    _run(d, "20260905-080000", summary="the report is published")

    got = c.get("/api/items?type=summary").json()
    ids = [i["id"] for i in got["items"]]
    assert ids == ["talker:20260905-080000"]
    assert got["counts"]["type"]["summary"] == 1
    # unread == open, which is what makes the page's own status default work unchanged
    assert c.get("/api/items?type=summary&status=open,in_progress").json()["total"] == 1

    r = c.post("/api/items/talker:20260905-080000/read", json={"read": True})
    assert r.status_code == 200 and r.json()["routine"] == "talker"
    assert c.get("/api/items?type=summary&status=open,in_progress").json()["total"] == 0
    assert c.get("/api/items?type=summary&status=settled").json()["total"] == 1


def test_a_maintenance_item_cannot_be_marked_read(api_client, make_routine):
    """Findings are settled by the work, not by being looked at — and `priorities.ITEM_ID_RE`
    rejects a run id by design, so the two channels stay apart."""
    c, _tmp = api_client
    make_routine(slug="talker")
    assert c.post("/api/items/F123/read", json={"read": True}).status_code == 400


def test_a_flagged_item_leads_the_page_ahead_of_every_summary(api_client, make_routine):
    """⚑ is the user's "work this first" and outranks recency (readmodels/items). The page
    prepended every run summary to the maintenance index, so the flagged item sat below one
    summary per routine — the float undone by the merge."""
    import json

    from rsched import priorities

    c, tmp = api_client
    make_routine(slug="self-audit")
    _run(make_routine(slug="talker"), "20260905-080000", summary="the report is published")
    home = tmp / "routines"
    (home / ".control").mkdir(exist_ok=True)
    rows = [{"id": "R1", "ts": "2026-07-19T21:20:06+02:00", "routine": "talker",
             "run_id": "talker:20260719-190013", "title": "a flagged bug", "detail": ""},
            {"id": "R2", "ts": "2026-07-20T08:00:00+02:00", "routine": "talker",
             "run_id": "talker:20260720-080000", "title": "an ordinary bug", "detail": ""}]
    (home / ".control" / "reports.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    priorities.set_priority(home, "R1", True)

    ids = [i["id"] for i in c.get("/api/items").json()["items"]]
    assert ids[0] == "R1", ids
    assert ids.index("talker:20260905-080000") < ids.index("R2"), "the rest keep their order"


def test_summaries_are_served_even_without_self_audit(api_client, make_routine):
    """An instance with no maintenance record still has routines that tell it things — the
    `exists: False` branch used to return an empty page."""
    c, _tmp = api_client
    _run(make_routine(slug="talker"), "20260905-080000", summary="hello")
    got = c.get("/api/items").json()
    assert got["exists"] is False
    assert [i["id"] for i in got["items"]] == ["talker:20260905-080000"]


def test_concurrent_mark_read_clicks_all_land(tmp_path):
    """Each "mark read" is a read-modify-write of one shared marker file on a worker thread;
    two at once each read the same map, and the second write resurrected the first card."""
    from conftest import hammer

    home = tmp_path / "routines"
    for _ in range(10):
        summaries.read_marker_path(home).unlink(missing_ok=True)
        assert hammer(lambda tag: summaries.mark_read(
            home, f"r{tag}:20260905-080000", read=True)) == []
        assert len(summaries._read_map(home)) == 6
