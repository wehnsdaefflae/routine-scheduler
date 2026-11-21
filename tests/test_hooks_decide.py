"""D152 step 7: the ledger read back AS hooks, and the first DECISION taken from the stream."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rsched.hooks.reportinv import hook_of_row, open_threads_now, still_owed_now
from rsched.hooks.reportproj import rows_for
from rsched.report_disposal import Disposal
from rsched.reports import file_report, read_reports, reports_path

LEDGER = Path("/home/mark/routines/.control/reports.jsonl")

ROWS = [
    {"id": "R1", "ts": "2026-07-19T21:20:06+02:00", "routine": "a", "run_id": "a:1",
     "title": "t", "detail": "d"},
    {"id": "R2", "ts": "2026-07-20T10:00:00+02:00", "routine": "a", "run_id": "a:2",
     "title": "t", "detail": "d", "target": "b"},
    {"id": "R3", "ts": "2026-07-21T10:00:00+02:00", "routine": "a", "run_id": "a:3",
     "title": "t", "detail": "d", "target": "b", "answers": "R2", "closes": True},
    {"id": "R4", "ts": "2026-07-22T10:00:00+02:00", "routine": "a", "run_id": "a:4",
     "title": "t", "detail": "d", "supersedes": ["R1"], "settles": ["R1"]},
    {"id": "R2", "event": "delivered", "ts": "2026-07-23T10:00:00+02:00", "run_id": "b:1"},
    {"id": "R1", "event": "superseded", "ts": "2026-07-24T10:00:00+02:00", "by": "R4",
     "to": "b"},
    {"id": "R5", "event": "retracted", "ts": "2026-07-25T10:00:00+02:00"},
    # The one live shape today's Disposal REFUSES (D196): closes with nothing to complete.
    # History must read back as what it says, so the inverse may not route through Disposal.
    {"id": "R1062", "ts": "2026-08-29T13:39:33+02:00", "routine": "a", "run_id": "a:9",
     "title": "t", "detail": "d", "closes": True},
]


@pytest.mark.parametrize("row", ROWS, ids=[f"{r['id']}-{r.get('event') or 'filing'}"
                                           for r in ROWS])
def test_every_ledger_row_shape_survives_the_round_trip(row):
    """`rows_for(hook_of_row(row)) == row` — the projection is invertible, which is what lets
    the pre-stream history be carried rather than reconstructed.
    """
    assert rows_for(hook_of_row(row)) == row


def test_the_refused_closes_shape_reads_back_without_raising():
    """A pre-D196 row (`closes` with neither answers nor settles) is history and must read, so
    the inverse assembles the payload directly instead of through `Disposal`, whose refusal
    belongs on the write path.
    """
    with pytest.raises(ValueError):
        Disposal(closes=True)                       # the write path still refuses it
    hook = hook_of_row({"id": "R1062", "ts": "t", "title": "x", "detail": "y",
                        "closes": True})
    assert hook.payload["closes"] is True


def test_a_row_without_an_id_yields_a_hook_the_backfill_skips():
    assert hook_of_row({"ts": "t", "title": "x"}).id == ""
    assert hook_of_row("not a dict").id == ""


@pytest.mark.skipif(not LEDGER.is_file(), reason="no live ledger on this host")
def test_report_hooks_roundtrip_live():
    """Every row of the LIVE ledger survives the round trip. This is the measurement the
    backfill rests on — it ran at 5,016 rows with zero mismatches.
    """
    rows = [json.loads(line) for line in LEDGER.read_text().splitlines() if line.strip()]
    bad = [r for r in rows if rows_for(hook_of_row(r)) != r]
    assert bad == [], f"{len(bad)} of {len(rows)} live ledger rows do not round-trip"


def _file(home: Path, routine: str, target: str, n: int) -> str:
    """One filed report through the LIVE write path — which writes the hook first and the
    ledger row from it, so both stores are populated the way production populates them.
    """
    out = file_report(home, routine=routine, run_id=f"{routine}:{n}",
                      title=f"t{n}", detail="d", target=target)
    assert out is not None, "the filing was refused"
    return out[1]


def test_the_thread_cap_now_decides_from_the_hook_stream(tmp_path, monkeypatch):
    """The cap's count comes from `open_threads_now`, and with both stores written by the live
    path the stream and the ledger agree — so no divergence event is filed.
    """
    home = tmp_path / "routines"
    (home / ".control").mkdir(parents=True, exist_ok=True)
    seen: list[dict] = []
    monkeypatch.setattr("rsched.hooks.reportinv.log_health_event",
                        lambda *a, **k: seen.append(k))
    for n in range(3):
        _file(home, "a", "b", n)
    rows = read_reports(reports_path(home))
    assert open_threads_now(home, rows, routine="a", target="b") == ["R1", "R2", "R3"]
    assert seen == [], "the stores agree, so nothing should have been filed"


def test_folding_the_stream_is_what_makes_the_two_agree(tmp_path, monkeypatch):
    """A thread DISPOSED OF by a lifecycle fact must read the same on both sides."""
    home = tmp_path / "routines"
    (home / ".control").mkdir(parents=True, exist_ok=True)
    seen: list[dict] = []
    monkeypatch.setattr("rsched.hooks.reportinv.log_health_event",
                        lambda *a, **k: seen.append(k))
    _file(home, "a", "b", 1)
    _file(home, "a", "b", 2)
    out = file_report(home, routine="a", run_id="a:3", title="t3", detail="d", target="b",
                      supersedes=("R1", "R2"))
    assert out is not None and sorted(out[2]) == ["R1", "R2"], out
    rows = read_reports(reports_path(home))
    assert open_threads_now(home, rows, routine="a", target="b") == ["R3"]
    assert seen == [], f"the fold was not applied to the stream: {seen}"


def test_a_divergence_acts_on_the_stream_and_files_the_event(tmp_path, monkeypatch):
    """When the two stores disagree about a thread, the STREAM's answer is acted on and
    `hook_stream_diverged` is filed.
    """
    home = tmp_path / "routines"
    seen: list[dict] = []
    monkeypatch.setattr("rsched.hooks.reportinv.log_health_event",
                        lambda *a, **k: seen.append(k))
    monkeypatch.setattr("rsched.hooks.reportread.projection", lambda _h: [])
    rows = [{"id": "R9", "ts": "t", "routine": "a", "run_id": "a:1", "title": "t",
             "detail": "d", "target": "b"}]
    assert open_threads_now(home, rows, routine="a", target="b") == ["R9"]
    assert len(seen) == 1 and seen[0]["stream"] == 1 and seen[0]["ledger"] == 0


def test_the_owed_reply_hold_agrees_with_the_ledger_over_the_stream(tmp_path, monkeypatch):
    """D152 step 8, the second decision on the stream: which handed-in reports a run still owes
    a reply to. The two stores must agree over a real filing-and-closure shape, with no event.
    """
    home = tmp_path / "routines"
    (home / ".control").mkdir(parents=True, exist_ok=True)
    seen: list[dict] = []
    monkeypatch.setattr("rsched.hooks.reportinv.log_health_event",
                        lambda *a, **k: seen.append(k))
    _file(home, "a", "b", 1)                        # R1: a hands work to b
    _file(home, "a", "b", 2)                        # R2: and another
    # b answers R1 and closes it — so only R2 is still owed
    assert file_report(home, routine="b", run_id="b:1", title="done", detail="d", target="a",
                       disposal=Disposal(answers="R1", closes=True)) is not None
    rows = read_reports(reports_path(home))
    assert still_owed_now(home, rows, ["R1", "R2"], routine="b") == ["R2"]
    assert seen == [], f"the stream and the ledger disagreed: {seen}"
    # …and an empty owed list asks nothing of either store
    assert still_owed_now(home, rows, [], routine="b") == []


def test_an_owed_reply_divergence_acts_on_the_stream_and_files_the_event(tmp_path,
                                                                        monkeypatch):
    """The hold is placed on the STREAM's answer since step 9 — it is the reading every report
    surface shows, so holding a finish on the projection's would ask a run for a reply against
    a count nobody can see.
    """
    home = tmp_path / "routines"
    seen: list[dict] = []
    monkeypatch.setattr("rsched.hooks.reportinv.log_health_event",
                        lambda *a, **k: seen.append(k))
    monkeypatch.setattr("rsched.hooks.reportread.projection", lambda _h: [])
    rows = [{"id": "R9", "ts": "t", "routine": "a", "run_id": "a:1", "title": "t",
             "detail": "d", "target": "b"}]
    assert still_owed_now(home, rows, ["R9"], routine="b") == ["R9"]
    assert len(seen) == 1 and seen[0]["stream"] == 1 and seen[0]["ledger"] == 0
    assert "still owes a reply" in seen[0]["detail"]


def test_the_finish_hold_predicate_reads_the_stream(tmp_path, monkeypatch):
    """THE pin on step 8's decision actually moving — the counterpart of the filing-path pin
    below. `assist_predicates._unclosed_delivered_report` is what HOLDS a run's finish, and it
    must consult the stream rather than the ledger alone.
    """
    from types import SimpleNamespace

    from rsched.engine import assist_predicates

    home = tmp_path / "routines"
    (home / ".control").mkdir(parents=True, exist_ok=True)
    _file(home, "a", "b", 1)
    seen: list[dict] = []
    monkeypatch.setattr("rsched.hooks.reportinv.log_health_event",
                        lambda *a, **k: seen.append(k))
    monkeypatch.setattr("rsched.hooks.reportread.projection", lambda _h: [])
    situation = SimpleNamespace(ctx=SimpleNamespace(
        reports_open=["R1"], routine=SimpleNamespace(slug="b"),
        server=SimpleNamespace(routines_home=home)), loop=SimpleNamespace())

    assert assist_predicates._unclosed_delivered_report(situation) is True
    assert len(seen) == 1, "the finish-hold predicate did not compare the two stores"
    assert seen[0]["routine"] == "b" and seen[0]["stream"] == 1 and seen[0]["ledger"] == 0


def test_the_live_filing_path_reads_the_stream(tmp_path, monkeypatch):
    """THE pin on the decision actually moving: `file_report` — the engine's write path — must
    consult the stream, not just the ledger.
    """
    home = tmp_path / "routines"
    (home / ".control").mkdir(parents=True, exist_ok=True)
    _file(home, "a", "b", 1)
    seen: list[dict] = []
    monkeypatch.setattr("rsched.hooks.reportinv.log_health_event",
                        lambda *a, **k: seen.append(k))
    monkeypatch.setattr("rsched.hooks.reportread.projection", lambda _h: [])
    _file(home, "a", "b", 2)
    assert len(seen) == 1, "the filing path did not compare the two stores"
    assert seen[0]["stream"] == 1 and seen[0]["ledger"] == 0


def test_an_unreadable_store_never_breaks_the_cap(tmp_path, monkeypatch):
    """A comparison between the two stores must not become a new way for a filing to fail."""
    home = tmp_path / "routines"

    def boom(_home):
        raise OSError("ledger unreadable")

    monkeypatch.setattr("rsched.hooks.reportread.projection", boom)
    rows = [{"id": "R9", "ts": "t", "routine": "a", "run_id": "a:1", "title": "t",
             "detail": "d", "target": "b"}]
    assert open_threads_now(home, rows, routine="a", target="b") == ["R9"]
