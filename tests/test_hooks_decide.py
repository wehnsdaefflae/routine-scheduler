"""D152 step 7: the ledger read back AS hooks, and the DECISIONS taken from the stream."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rsched.hooks.reportinv import hook_of_row
from rsched.hooks.reportproj import rows_for
from rsched.report_disposal import Disposal
from rsched.report_threads import ThreadCapError
from rsched.reports import file_report, reports_path

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


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "routines"
    (home / ".control").mkdir(parents=True, exist_ok=True)
    return home


def _blank_ledger(home: Path) -> None:
    """Empty `reports.jsonl` — so whatever is still decided below came from the stream."""
    reports_path(home).write_text("", encoding="utf-8")


def test_the_thread_cap_decides_from_the_hook_stream(tmp_path):
    """The cap counts the stream's threads: with the ledger file emptied, a fourth parallel
    report to one owner is still refused, naming the three the stream holds open.
    """
    home = _home(tmp_path)
    for n in range(3):
        _file(home, "a", "b", n)
    _blank_ledger(home)
    with pytest.raises(ThreadCapError) as refused:
        file_report(home, routine="a", run_id="a:9", title="t9", detail="d", target="b")
    assert refused.value.open_ids == ["R1", "R2", "R3"]


def test_a_thread_folded_by_a_lifecycle_fact_is_not_counted(tmp_path):
    """A thread DISPOSED OF by a lifecycle fact reads as closed — the fold is applied to the
    stream's rows (F847: unfolded, every superseded row read as still open).
    """
    home = _home(tmp_path)
    _file(home, "a", "b", 1)
    _file(home, "a", "b", 2)
    out = file_report(home, routine="a", run_id="a:3", title="t3", detail="d", target="b",
                      supersedes=("R1", "R2"))
    assert out is not None and sorted(out[2]) == ["R1", "R2"], out
    _file(home, "a", "b", 4)
    _file(home, "a", "b", 5)                        # R3, R4, R5 open: R1/R2 folded into R3
    with pytest.raises(ThreadCapError) as refused:
        _file(home, "a", "b", 6)
    assert refused.value.open_ids == ["R3", "R4", "R5"]


def test_the_finish_hold_predicate_reads_the_stream(tmp_path):
    """`assist_predicates._unclosed_delivered_report` HOLDS a run's finish, and what is still
    owed comes from the stream: with the ledger file emptied, the closed hand-off is not owed
    and the open one still is.
    """
    from types import SimpleNamespace

    from rsched.engine import assist_predicates
    from rsched.report_threads import still_owed
    from rsched.reports import read_reports

    home = _home(tmp_path)
    _file(home, "a", "b", 1)                        # R1: a hands work to b
    _file(home, "a", "b", 2)                        # R2: and another
    assert file_report(home, routine="b", run_id="b:1", title="done", detail="d", target="a",
                       disposal=Disposal(answers="R1", closes=True)) is not None
    _blank_ledger(home)
    assert still_owed(read_reports(reports_path(home)), ["R1", "R2"]) == ["R2"]
    situation = SimpleNamespace(ctx=SimpleNamespace(
        reports_open=["R1", "R2"], routine=SimpleNamespace(slug="b"),
        server=SimpleNamespace(routines_home=home)), loop=SimpleNamespace())
    assert assist_predicates._unclosed_delivered_report(situation) is True
    situation.ctx.reports_open = ["R1"]
    assert assist_predicates._unclosed_delivered_report(situation) is False
