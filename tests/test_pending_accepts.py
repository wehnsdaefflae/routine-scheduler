"""D178 + F668: a settings accept refused mid-run is HELD, legible, and lands at run end.

The refusal itself stays — a multi-field accept during a live run would leave the routine with
neither its old settings nor its new ones. What was missing is the rest of the answer: the 409
said "try again after it ends" without saying WHEN, and nothing was left behind, so on
2026-10-11 the operator pressed the accept on `freelance-radar` three times in one second
78 minutes before that run's end and concluded the button was broken.
"""

from __future__ import annotations

import pytest
import yaml

from conftest import authed_client, make_test_server, mk_run
from rsched import pending_accepts


@pytest.fixture
def client(tmp_path, make_routine):
    make_routine(slug="apir")
    with authed_client(make_test_server(tmp_path)) as c:
        yield c, tmp_path


def _running(tmp_path, slug="apir", ts="20260708-090000"):
    """An active run of `slug`, started at `ts` — `make_routine` budgets 5 wall-clock min."""
    return mk_run(tmp_path / "routines" / slug, ts, "running", pid=4242, elapsed_s=60)


def _accept(c, slug="apir", **changes):
    return c.post(f"/api/routines/{slug}/settings", json={"changes": changes})


def test_a_refused_accept_says_when_it_can_be_retried(client):
    """The click gets an answer that names the active run's expected end, not just 'later'."""
    c, tmp = client
    _running(tmp)
    r = _accept(c, keep_runs=11)
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    # the WHEN a person reads, derived from the run's own wall-clock budget (09:00 + 5 min)
    assert "2026-07-08" in detail, detail
    assert "waiting" in detail.lower() or "held" in detail.lower(), detail


def test_a_refused_accept_persists_as_a_pending_accept(client):
    """The attempt is kept with the run's expected end, so the operator can see it waiting."""
    c, tmp = client
    _running(tmp)
    assert _accept(c, keep_runs=11).status_code == 409
    held = pending_accepts.read(tmp / "routines", "apir")
    assert held is not None
    assert held["changes"] == {"keep_runs": 11}
    assert held["run_id"] == "apir:20260708-090000"
    assert held["expected_end"] and "2026-07-08" in held["expected_end"]
    # the stored value the accept was computed against — what the expiry rule reads later
    assert held["base"]["keep_runs"] != 11
    # nothing was written to the routine while the run holds it
    raw = yaml.safe_load((tmp / "routines" / "apir" / "routine.yaml").read_text())
    assert (raw.get("retention") or {}).get("keep_runs") != 11


def test_three_clicks_in_one_second_hold_one_accept(client):
    """The measured harm: three presses, one second. One record, carrying the newest values."""
    c, tmp = client
    _running(tmp)
    for keep in (11, 12, 13):
        assert _accept(c, keep_runs=keep).status_code == 409
    home = tmp / "routines"
    assert len(list((home / ".control" / "pending-accepts" / "apir").glob("pa-*.json"))) == 1
    assert pending_accepts.read(home, "apir")["changes"] == {"keep_runs": 13}


def test_the_settings_page_reads_the_held_accept(client):
    """Visible means a surface renders it: the settings payload the page reads carries it."""
    c, tmp = client
    _running(tmp)
    assert _accept(c, keep_runs=11).status_code == 409
    payload = c.get("/api/routines/apir/settings").json()
    assert payload["held_accept"], payload.keys()
    assert payload["held_accept"]["changes"] == {"keep_runs": 11}
    assert payload["held_accept"]["expected_end"]


def test_a_held_accept_applies_when_the_run_ends(client):
    """The operator's words: it applies when the run ends — through the same endpoint an
    idle accept takes, so held and immediate land identically."""
    from rsched.web import held_accepts

    c, tmp = client
    run_dir = _running(tmp)
    assert _accept(c, keep_runs=11).status_code == 409
    mk_run(tmp / "routines" / "apir", run_dir.name, "finished", outcome="ok")
    rows = held_accepts.drain_one(c.app, "apir")
    assert rows and rows[0]["ok"] and rows[0]["applied"] == ["keep_runs"], rows
    raw = yaml.safe_load((tmp / "routines" / "apir" / "routine.yaml").read_text())
    assert raw["retention"]["keep_runs"] == 11
    assert pending_accepts.read(tmp / "routines", "apir") is None   # cleared once it landed


def test_a_held_accept_expires_when_the_value_moved_under_it(client):
    """The expiry rule: a field somebody else decided while the run held the accept is NOT
    overwritten by the older click. The accept expires whole and says which field moved."""
    from rsched.web import held_accepts

    c, tmp = client
    run_dir = _running(tmp)
    assert _accept(c, keep_runs=11).status_code == 409
    # the value moves under the held accept (a decision applied elsewhere)
    path = tmp / "routines" / "apir" / "routine.yaml"
    raw = yaml.safe_load(path.read_text())
    raw.setdefault("retention", {})["keep_runs"] = 7
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    mk_run(tmp / "routines" / "apir", run_dir.name, "finished", outcome="ok")
    rows = held_accepts.drain_one(c.app, "apir")
    assert rows and rows[0]["expired"] == ["keep_runs"], rows
    assert yaml.safe_load(path.read_text())["retention"]["keep_runs"] == 7   # not overwritten
    held = pending_accepts.read(tmp / "routines", "apir")
    assert held and held["expired"] == ["keep_runs"]      # kept visible, not silently dropped


def test_an_idle_accept_is_never_held(client):
    """No active run: the accept applies at once and nothing is spooled."""
    c, tmp = client
    r = _accept(c, keep_runs=11)
    assert r.status_code == 200 and r.json()["applied"] == ["keep_runs"]
    assert pending_accepts.read(tmp / "routines", "apir") is None


def test_expected_end_is_none_when_the_wall_clock_is_unlimited():
    """-1 means no ceiling: the record says so rather than inventing a time."""
    assert pending_accepts.expected_end("20260708-090000", {"max_wall_clock_min": -1}) is None
    assert pending_accepts.expected_end("nonsense", {"max_wall_clock_min": 45}) is None
    assert pending_accepts.expected_end("20260708-090000", {"max_wall_clock_min": 45})
