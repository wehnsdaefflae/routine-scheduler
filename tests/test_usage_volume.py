"""The one-shot volume backfill (migrate_usage_volume.py) — MIGRATION(expires=2026-12-15).

Every cost ranking now reads `volume`, so a past row without it would drop out of every
ranking. What its cache traffic was is recovered from what the run itself recorded, per LEG;
what nothing recorded stays UNMEASURED (null), never zero.
"""

from __future__ import annotations

import json

from helpers import tmp_server, write_usage_stream
from rsched import migrate_usage_volume as mig
from rsched.paths import atomic_write_json
from rsched.readmodels.usage_stream import stream_path


def _rows(server):
    return [json.loads(x) for x in stream_path(server.routines_home).read_text().splitlines()]


def test_cumulative_quality_becomes_each_legs_own_share(tmp_path):
    server = tmp_server(tmp_path)
    write_usage_stream(server.routines_home, [
        {"routine": "r", "run_id": "r:1", "depth": 0, "tokens": 10, "cost": 0.0,
         "quality": {"cache_read": 1_000, "cache_write": 100}},
        {"routine": "r", "run_id": "r:1", "depth": 0, "tokens": 5, "cost": 0.0,
         "quality": {"cache_read": 1_600, "cache_write": 150}},
    ])
    mig.run_migration(server)
    first, second = _rows(server)
    assert (first["cached_in"], first["cache_write"], first["volume"]) == (1_000, 100, 1_110)
    assert (second["cached_in"], second["cache_write"], second["volume"]) == (600, 50, 655)
    assert first["notional_cost"] is None          # what it cost THEN was never recorded


def test_a_row_nothing_recorded_stays_unmeasured_and_a_retained_status_is_read(tmp_path):
    server = tmp_server(tmp_path)
    run = server.routines_home / "r" / "runs" / "20260801-000000"
    run.mkdir(parents=True)
    atomic_write_json(run / "status.json", {"usage": {"in": 1, "out": 1, "cached_in": 70}})
    write_usage_stream(server.routines_home, [
        {"routine": "r", "run_id": "r:20260801-000000", "depth": 0, "tokens": 2, "cost": 0.0},
        {"routine": "gone", "run_id": "gone:20260701-000000", "depth": 0, "tokens": 9,
         "cost": 0.0},
    ])
    record = mig.run_migration(server)
    kept, lost = _rows(server)
    assert kept["volume"] == 72 and kept["cache_write"] == 0
    assert lost["volume"] is None and lost["cached_in"] is None
    assert record["measured"] == 1 and record["unmeasured"] == 1
    assert mig.run_migration(server) == {}         # one-shot: the record stops a second pass


def test_a_child_rows_usage_comes_from_its_parents_transcript(tmp_path):
    server = tmp_server(tmp_path)
    run = server.routines_home / "r" / "runs" / "20260901-000000"
    run.mkdir(parents=True)
    (run / "transcript.jsonl").write_text(json.dumps(
        {"type": "subrun_end", "payload": {"n": 2, "usage": {"in": 1, "out": 1,
                                                            "cached_in": 40,
                                                            "cache_write": 5}}}) + "\n")
    write_usage_stream(server.routines_home, [
        {"routine": "r", "run_id": "r:20260901-000000#sub2", "depth": 1, "tokens": 2,
         "cost": 0.0}])
    mig.run_migration(server)
    assert _rows(server)[0]["volume"] == 47
