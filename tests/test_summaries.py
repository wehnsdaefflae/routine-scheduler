"""A run's finish summary as an item on the Messages page (operator order 2026-09-05).

The Summary page was a read surface nothing linked to, next door to the page that already was
the index of everything the instance has to say. What these pin is that folding it in kept the
two behaviours the old page had earned — Unread by default (2026-08-05) and a bulk sweep (F303)
— while reusing the item vocabulary rather than forking it with a synonym.
"""

from __future__ import annotations

from rsched.config import ServerConfig
from rsched.readmodels import summaries

TS = "20260905-090000"


def _server(tmp_path) -> ServerConfig:
    s = ServerConfig()
    s.routines_home = tmp_path / "routines"
    s.routines_home.mkdir(parents=True, exist_ok=True)
    return s


def _run(routine_dir, ts, *, summary="", state="finished", outcome="ok"):
    from rsched.paths import atomic_write_json

    d = routine_dir / "runs" / ts
    d.mkdir(parents=True, exist_ok=True)
    atomic_write_json(d / "status.json", {
        "run_id": f"{routine_dir.name}:{ts}", "state": state, "outcome": outcome,
        "turn": 3, "usage": {"in": 10, "out": 4}, "started": ts,
        "updated": "2026-09-05T09:00:00+00:00"})
    if summary:
        (d / "result.md").write_text(summary, encoding="utf-8")
    return d


def test_one_row_per_routine_carrying_the_newest_run_with_a_summary(tmp_path, make_routine):
    server = _server(tmp_path)
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


def test_a_routine_that_never_ran_has_nothing_to_say(tmp_path, make_routine):
    server = _server(tmp_path)
    make_routine(slug="quiet")
    assert summaries.build(server) == []


def test_marking_read_is_a_watermark_a_newer_run_clears(tmp_path, make_routine):
    """The store is `{slug: newest run seen}`, so a newer run resurfaces on its own — nothing
    has to go back and clear the old marker."""
    server = _server(tmp_path)
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
    server = _server(tmp_path)
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


def test_summaries_are_served_even_without_self_audit(api_client, make_routine):
    """An instance with no maintenance record still has routines that tell it things — the
    `exists: False` branch used to return an empty page."""
    c, _tmp = api_client
    _run(make_routine(slug="talker"), "20260905-080000", summary="hello")
    got = c.get("/api/items").json()
    assert got["exists"] is False
    assert [i["id"] for i in got["items"]] == ["talker:20260905-080000"]
