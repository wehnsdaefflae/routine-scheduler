"""D152 step 9: every report surface READS the hook stream, and `reports.jsonl` is its
projection — written for readers outside the engine, never read by it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rsched.hooks.reportproj import stream_rows
from rsched.paths import read_jsonl
from rsched.reports import file_report, fold_rows, read_reports, reports_path

LIVE = Path("/home/mark/routines")


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "routines"
    (home / ".control").mkdir(parents=True, exist_ok=True)
    return home


@pytest.mark.skipif(not (LIVE / ".control" / "reports.jsonl").is_file(),
                    reason="no live stores on this host")
def test_the_live_stream_carries_every_live_report_in_the_ledgers_order():
    """Over the real stores: the stream names every report the ledger does, in its order."""
    ledger = fold_rows(read_jsonl(reports_path(LIVE)))
    stream = fold_rows(stream_rows(LIVE))
    assert [r["id"] for r in stream] == [r["id"] for r in ledger], "the ids or their order"


def test_a_report_only_the_ledger_has_is_not_served(tmp_path):
    """The stream is the ONLY read source: a row hand-written into `reports.jsonl` with no hook
    behind it is not a report any surface shows, and the read does not consult the file.
    """
    home = _home(tmp_path)
    assert file_report(home, routine="a", run_id="a:1", title="t1", detail="d") is not None
    with reports_path(home).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"id": "R9", "ts": "t", "routine": "a", "run_id": "a:9",
                             "title": "hand-written, no hook", "detail": "d"}) + "\n")
    assert [r["id"] for r in read_reports(reports_path(home))] == ["R1"]


def test_the_read_never_opens_the_ledger(tmp_path, monkeypatch):
    """A cache miss folds the stream alone — it used to parse both stores only to compare."""
    home = _home(tmp_path)
    assert file_report(home, routine="a", run_id="a:1", title="t1", detail="d") is not None
    ledger = reports_path(home)
    opened: list[str] = []
    real = Path.read_bytes

    def spy(self: Path) -> bytes:
        opened.append(self.name)
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", spy)
    assert [r["id"] for r in read_reports(ledger)] == ["R1"]
    assert ledger.name not in opened, opened


def test_a_filing_is_visible_to_the_very_next_read(tmp_path):
    """The memoized read must not serve a fold from before the newest filing — the regression a
    fingerprint over the ledger alone would risk, since the hook is written first.
    """
    home = _home(tmp_path)
    assert file_report(home, routine="a", run_id="a:1", title="t1", detail="d") is not None
    assert [r["id"] for r in read_reports(reports_path(home))] == ["R1"]
    assert file_report(home, routine="a", run_id="a:2", title="t2", detail="d") is not None
    assert [r["id"] for r in read_reports(reports_path(home))] == ["R1", "R2"]


def test_the_served_read_comes_from_the_stream_not_the_file(tmp_path):
    """The claim of step 9, made falsifiable: a report the STREAM has and the projection does
    not IS served.
    """
    home = _home(tmp_path)
    assert file_report(home, routine="a", run_id="a:1", title="t1", detail="d") is not None
    assert file_report(home, routine="a", run_id="a:2", title="t2", detail="d") is not None
    kept = [r for r in read_jsonl(reports_path(home)) if r.get("id") != "R2"]
    reports_path(home).write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in kept), encoding="utf-8")
    assert [r["id"] for r in read_reports(reports_path(home))] == ["R1", "R2"], \
        "the stream's own report was not served"
