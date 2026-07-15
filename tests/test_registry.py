"""Derived catalog, run index, cron math, catch-up, retention."""

import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from rsched.config import ServerConfig, load_routine
from rsched.daemon import registry
from rsched.engine.transcript import read_events
from rsched.paths import atomic_write_json

BERLIN = ZoneInfo("Europe/Berlin")


def _server(tmp_path) -> ServerConfig:
    s = ServerConfig()
    s.routines_home = tmp_path / "routines"
    return s


def _mk_run(d, ts, state, summary=""):
    run_dir = d / "runs" / ts
    run_dir.mkdir(parents=True)
    atomic_write_json(run_dir / "status.json", {"run_id": f"{d.name}:{ts}", "state": state,
                                                "pid": 999999, "turn": 3,
                                                "usage": {"in": 5, "out": 2}})
    if summary:
        (run_dir / "result.md").write_text(summary)
    (run_dir / "transcript.jsonl").write_text(json.dumps({"type": "header"}) + "\n")
    return run_dir


def test_scan_catalog(make_routine, tmp_path):
    d = make_routine(slug="alpha")
    make_routine(slug="beta")
    (tmp_path / "routines" / ".wizard-x").mkdir()          # dot dir: hidden
    (tmp_path / "routines" / "not-a-routine").mkdir()      # no routine.yaml: skipped
    (tmp_path / "routines" / "broken").mkdir()
    (tmp_path / "routines" / "broken" / "routine.yaml").write_text(":::not yaml{{{")
    _mk_run(d, "20260706-070000", "finished", "did the thing")
    _mk_run(d, "20260707-070000", "running")

    catalog = registry.scan(_server(tmp_path))
    assert set(catalog) == {"alpha", "beta", "broken"}
    assert catalog["broken"].cfg.enabled is False and catalog["broken"].problems
    alpha = catalog["alpha"]
    assert [r.ts for r in alpha.runs] == ["20260707-070000", "20260706-070000"]
    assert alpha.active_run and alpha.active_run.state == "running"
    assert alpha.runs[1].summary == "did the thing"


def test_next_and_missed_fire(make_routine):
    d = make_routine(slug="cronny")
    cfg, _ = load_routine(d)  # cron "0 7 * * 1" Europe/Berlin
    tue = datetime(2026, 7, 7, 12, 0, tzinfo=BERLIN)  # Tuesday
    nf = registry.next_fire(cfg, tue)
    assert nf == datetime(2026, 7, 13, 7, 0, tzinfo=BERLIN)  # next Monday 07:00
    cfg.enabled = False
    assert registry.next_fire(cfg, tue) is None
    cfg.enabled = True

    # catch-up: policy skip → never; run_once → due fire when no run covered it
    now = datetime(2026, 7, 7, 12, 0, tzinfo=BERLIN)
    assert registry.missed_fire(cfg, [], now) is None  # default catchup: skip
    cfg.catchup = "run_once"
    missed = registry.missed_fire(cfg, [], now)
    assert missed == datetime(2026, 7, 6, 7, 0, tzinfo=BERLIN)  # this week's Monday
    covered = registry.RunInfo(run_id="cronny:20260706-070001", ts="20260706-070001",
                               dir=d, state="finished")
    assert registry.missed_fire(cfg, [covered], now) is None
    stale = registry.RunInfo(run_id="cronny:20260629-070000", ts="20260629-070000",
                             dir=d, state="finished")
    assert registry.missed_fire(cfg, [stale], now) == missed


def test_apply_retention(make_routine):
    d = make_routine(slug="retain")
    for i in range(1, 9):  # 8 runs, oldest first
        _mk_run(d, f"2026070{i}-070000", "finished", f"run {i}")
    registry.apply_retention(d, "retain", keep_runs=6)
    left = sorted(p.name for p in (d / "runs").iterdir())
    assert len(left) == 6 and left[0] == "20260703-070000"  # two oldest deleted
    # newest 5 keep plain transcripts; the 6th-newest is gzipped but still readable
    assert (d / "runs" / "20260703-070000" / "transcript.jsonl.gz").exists()
    assert not (d / "runs" / "20260703-070000" / "transcript.jsonl").exists()
    assert (d / "runs" / "20260704-070000" / "transcript.jsonl").exists()
    assert (d / "runs" / "20260708-070000" / "transcript.jsonl").exists()
    events, _ = read_events(d / "runs" / "20260703-070000" / "transcript.jsonl")
    assert events and events[0]["type"] == "header"


def test_retention_spares_alive_runs(make_routine):
    d = make_routine(slug="alive")
    for i in range(1, 4):
        _mk_run(d, f"2026070{i}-070000", "finished")
    _mk_run(d, "20260630-070000", "running")  # oldest but alive
    registry.apply_retention(d, "alive", keep_runs=2)
    assert (d / "runs" / "20260630-070000").exists()


def test_run_ts_is_always_utc():
    from rsched.ids import run_ts
    # an aware Berlin time (03:00:03 = 01:00:03 UTC) is recorded in UTC, not server-local
    assert run_ts(datetime(2026, 7, 15, 3, 0, 3, tzinfo=BERLIN)) == "20260715-010003"
    # a plain UTC time is unchanged
    assert run_ts(datetime(2026, 1, 2, 12, 0, 0, tzinfo=UTC)) == "20260102-120000"


def test_parse_run_ts_reads_utc():
    # run-ts is ALWAYS UTC (ids.run_ts) — parsed back as an aware UTC instant
    d = registry.parse_run_ts("20260715-030003")
    assert d is not None and d.utcoffset() == timedelta(0)
    assert (d.year, d.hour, d.minute, d.second) == (2026, 3, 0, 3)
    assert registry.parse_run_ts("not-a-ts") is None


# ---- the stat-validated memo: freshness beats reuse, callers get copies ----------------


def test_scan_memo_freshness_and_isolation(make_routine, tmp_path):
    d = make_routine(slug="memo")
    run_dir = _mk_run(d, "20260707-070000", "running")
    server = _server(tmp_path)

    first = registry.scan(server)["memo"]
    # returned objects are copies — mutating them must not poison later scans
    first.runs[0].usage["out"] = 999_999
    first.cfg.name = "clobbered"
    again = registry.scan(server)["memo"]
    assert again.runs[0].usage == {"in": 5, "out": 2}
    assert again.cfg.name == "Test memo"

    # an atomic rewrite (tmp+rename, the engine's write path) shows up on the very next scan
    atomic_write_json(run_dir / "status.json",
                      {"run_id": "memo:20260707-070000", "state": "finished",
                       "turn": 9, "usage": {"in": 7, "out": 3}})
    (run_dir / "result.md").write_text("done")
    updated = registry.scan(server)["memo"].runs[0]
    assert (updated.state, updated.turn, updated.summary) == ("finished", 9, "done")


def test_scan_memo_sees_config_and_question_changes(make_routine, tmp_path):
    d = make_routine(slug="memoq")
    server = _server(tmp_path)
    assert registry.scan(server)["memoq"].open_questions == []

    # a question appears → listed; its answer arrives in inbox/ → flagged answered
    qdir = d / "questions" / "pending"
    qdir.mkdir(parents=True)
    atomic_write_json(qdir / "q-1.json", {"qid": "q-1", "question": "carry on?"})
    assert registry.scan(server)["memoq"].open_questions[0]["qid"] == "q-1"
    atomic_write_json(d / "inbox" / "answer-q-1.json", {"answer": "yes"})
    assert registry.scan(server)["memoq"].open_questions[0]["answered"] is True

    # routine.yaml edited → the catalog reflects it next scan
    raw = (d / "routine.yaml").read_text(encoding="utf-8")
    (d / "routine.yaml").write_text(raw.replace("enabled: true", "enabled: false"),
                                    encoding="utf-8")
    assert registry.scan(server)["memoq"].cfg.enabled is False


def test_scan_memo_prunes_deleted_dirs(make_routine, tmp_path):
    import shutil as sh

    d = make_routine(slug="gone")
    _mk_run(d, "20260707-070000", "finished")
    server = _server(tmp_path)
    registry.scan(server)
    assert str(d) in registry._cfg_memo
    sh.rmtree(d)
    assert "gone" not in registry.scan(server)
    assert str(d) not in registry._cfg_memo
    assert all(not k.startswith(str(d)) for k in registry._run_memo)
