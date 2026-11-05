"""No finding or decision self-audit's report.json ever carried can vanish (rsched/item_records).

The specimen: F216, F217, F322, F355 and F445 left report.json while open or in_progress, no
changelog row named them, and they disappeared from every surface — found 2026-11-05 by walking
all 239 committed versions of the file.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from rsched import item_records
from rsched.daemon.item_record_watch import ItemRecordWatch
from rsched.readmodels import items as items_model


def _home(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "routines"
    audit = home / "self-audit" / "audit"
    audit.mkdir(parents=True)
    (home / ".control").mkdir()
    return home, home / "self-audit"


def _report(routine_dir: Path, findings: list[dict], decisions: list[dict] | None = None,
            run: str = "self-audit:20261105-003000") -> dict:
    report = {"schema": 1, "run_id": run, "generated": "2026-11-05T01:00:00+01:00",
              "since": {"commit": "abc"}, "summary": "", "findings": findings,
              "decisions": decisions or []}
    (routine_dir / "audit" / "report.json").write_text(json.dumps(report))
    return report


def _items(routine_dir: Path, home: Path) -> dict[str, dict]:
    return {i["id"]: i for i in items_model._build(routine_dir, home)["items"]}


def test_a_row_dropped_while_live_stays_live_in_the_index(tmp_path):
    home, rd = _home(tmp_path)
    item_records.observe(home, _report(rd, [
        {"id": "F216", "status": "open", "owner": "scheduler-builder",
         "title": "multiline run input", "detail": "the run page's message field…"},
        {"id": "F217", "status": "settled", "title": "copy buttons", "detail": "done"}]))
    dropped = item_records.observe(home, _report(rd, [
        {"id": "F900", "status": "open", "title": "a new one", "detail": ""}]))
    assert dropped == ["F216"], "only the LIVE departure is a loss"
    got = _items(rd, home)
    assert got["F216"]["status"] == "open"
    assert got["F216"]["title"] == "multiline run input", "the prose survives with it"
    assert got["F216"]["owner"] == "scheduler-builder"
    assert got["F216"]["left_report"] and not got["F216"]["archive_only"]
    assert got["F217"]["status"] == "settled"


def test_a_release_after_it_left_addresses_it_and_one_before_does_not(tmp_path):
    home, rd = _home(tmp_path)
    item_records.observe(home, _report(rd, [{"id": "F1", "status": "in_progress"},
                                            {"id": "F2", "status": "in_progress"}]),
                         at="2026-11-01T00:00:00+00:00")
    item_records.observe(home, _report(rd, [{"id": "F3", "status": "open"}]),
                         at="2026-11-02T00:00:00+00:00")
    rows = [{"ts": "2026-11-03T00:00:00+00:00", "commit": "c1", "summary": "x",
             "items": ["F1"]},
            {"ts": "2026-10-30T00:00:00+00:00", "commit": "c0", "summary": "y",
             "items": ["F2"]}]
    (rd / "audit" / "changelog.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    got = _items(rd, home)
    assert got["F1"]["status"] == "addressed"
    assert got["F2"]["status"] == "in_progress", "a release from before it left is in its status"


def test_an_empty_or_rowless_report_records_no_departures(tmp_path):
    home, rd = _home(tmp_path)
    item_records.observe(home, _report(rd, [{"id": "F1", "status": "open"}]))
    assert item_records.observe(home, _report(rd, [])) == []
    assert item_records.read_records(home)["F1"]["left"] == ""


def test_a_row_that_returns_clears_its_departure(tmp_path):
    home, rd = _home(tmp_path)
    item_records.observe(home, _report(rd, [{"id": "D7", "status": "open"}]))
    item_records.observe(home, _report(rd, [{"id": "D8", "status": "open"}]))
    item_records.observe(home, _report(rd, [], [{"id": "D7", "status": "settled",
                                                 "options": ["a", "b"]}]))
    rec = item_records.read_records(home)["D7"]
    assert rec["left"] == "" and rec["kind"] == "decision"
    assert rec["entry"]["status"] == "settled"


def test_the_watch_observes_on_change_and_files_the_live_loss(tmp_path, monkeypatch):
    home, rd = _home(tmp_path)
    filed: list[tuple[str, dict]] = []
    monkeypatch.setattr("rsched.daemon.item_record_watch.log_health_event",
                        lambda _h, event, **kw: filed.append((event, kw)))
    watch = ItemRecordWatch(SimpleNamespace(routines_home=home))
    assert watch.check() == [], "no report yet: nothing to observe"
    _report(rd, [{"id": "F445", "status": "in_progress"}])
    assert watch.check() == []
    assert watch.check() == [], "unchanged stat: not read again"
    _report(rd, [{"id": "F447", "status": "open"}, {"id": "F448", "status": "open"}])
    assert watch.check() == ["F445"]
    assert filed and filed[0][0] == "item_dropped_live"
    assert filed[0][1]["items"] == "F445"


def test_the_records_store_invalidates_the_index_memo(tmp_path):
    home, rd = _home(tmp_path)
    assert item_records.records_path(home) in items_model.source_paths(rd, home)
