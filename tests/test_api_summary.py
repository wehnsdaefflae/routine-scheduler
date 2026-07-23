"""/api/summary — each routine's latest finish message plus the per-routine read-marker, in
the shape static/views/summary.js consumes. Registry read-model math is pinned in
test_registry.py; this pins the route surface + the read-state persistence round-trip."""

from conftest import mk_run
from rsched.paths import read_json


def _mk_run(routine_dir, ts, state, *, summary="", outcome=None):
    mk_run(routine_dir, ts, state, usage={"in": 10, "out": 4}, elapsed_s=30, updated=ts,
           outcome=outcome, summary=summary)


def test_summary_lists_latest_finish_message_per_routine(api_client, make_routine):
    c, _tmp = api_client
    a = make_routine(slug="alpha")
    _mk_run(a, "20260712-070000", "finished", summary="older run", outcome="ok")
    _mk_run(a, "20260713-070000", "finished", summary="**newest** message", outcome="partial")
    make_routine(slug="beta")   # no runs → excluded from the list

    rows = c.get("/api/summary").json()
    assert isinstance(rows, list) and len(rows) == 1   # beta has no runs, so no row
    row = rows[0]
    assert row["slug"] == "alpha"
    assert row["title"] == "Test alpha"
    assert row["run_id"] == "alpha:20260713-070000"    # newest run that carries a summary
    assert row["summary"] == "**newest** message"
    assert row["outcome"] == "partial"
    assert row["read"] is False


def test_summary_skips_summaryless_runs_but_falls_back(api_client, make_routine):
    """A newest run with no finish message yet (still-running / summary-less) falls back to
    the newest run that DOES carry one — so the row shows a real message, not a blank."""
    c, _tmp = api_client
    a = make_routine(slug="alpha")
    _mk_run(a, "20260712-070000", "finished", summary="the message", outcome="ok")
    _mk_run(a, "20260713-070000", "running")   # newer, but no summary
    rows = c.get("/api/summary").json()
    assert rows[0]["run_id"] == "alpha:20260712-070000"
    assert rows[0]["summary"] == "the message"


def test_summary_read_marker_round_trip(api_client, make_routine):
    c, tmp = api_client
    a = make_routine(slug="alpha")
    _mk_run(a, "20260713-070000", "finished", summary="hi", outcome="ok")

    r = c.post("/api/summary/alpha/read",
               json={"run_id": "alpha:20260713-070000", "read": True})
    assert r.status_code == 200 and r.json()["read"] is True
    assert c.get("/api/summary").json()[0]["read"] is True

    # persisted under routines_home/.control/summary-read.json
    stored = read_json(tmp / "routines" / ".control" / "summary-read.json")
    assert stored["alpha"] == "alpha:20260713-070000"

    # a newer run automatically resurfaces the item (marker no longer matches the latest)
    _mk_run(a, "20260714-070000", "finished", summary="newer", outcome="ok")
    assert c.get("/api/summary").json()[0]["read"] is False

    # un-mark drops the marker entirely
    c.post("/api/summary/alpha/read", json={"run_id": "alpha:20260714-070000", "read": False})
    assert read_json(tmp / "routines" / ".control" / "summary-read.json") == {}


def test_summary_read_unknown_routine_404(api_client):
    c, _tmp = api_client
    r = c.post("/api/summary/nope/read", json={"run_id": "x", "read": True})
    assert r.status_code == 404


def test_summary_requires_auth(api_client):
    c, _tmp = api_client
    assert c.get("/api/summary", headers={"Authorization": ""}).status_code == 401
