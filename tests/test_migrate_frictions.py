"""The one-shot friction backfill (migrate_frictions.py): the runs retention kept gain the
signatures their transcripts hold, on their newest leg only, and a run with no dir stays
unknown."""

from __future__ import annotations

import json
from types import SimpleNamespace

from helpers import measured_run, write_usage_stream
from rsched.migrate_frictions import RECORD, run_migration
from rsched.readmodels.usage_stream import stream_path


def test_the_newest_leg_of_a_kept_run_gains_its_frictions_once(tmp_path):
    home = tmp_path / "routines"
    run_dir = home / "digest" / "runs" / "20261001-050000"
    run_dir.mkdir(parents=True)
    (run_dir / "transcript.jsonl").write_text(
        json.dumps({"type": "error", "payload": {"where": "schema"}}) + "\n", encoding="utf-8")
    leg1, leg2, gone = measured_run(1), measured_run(1), measured_run(2)
    write_usage_stream(home, [leg1, leg2, gone])
    record = run_migration(SimpleNamespace(routines_home=home))
    assert (record["runs"], record["counted"], record["unknown"]) == (2, 1, 1)
    rows = [json.loads(ln) for ln in stream_path(home).read_text(encoding="utf-8").splitlines()]
    assert "frictions" not in rows[0]["quality"]
    assert rows[1]["quality"]["frictions"] == {"schema": 1}
    assert "frictions" not in rows[2]["quality"]          # its dir is gone: unknown
    assert (home / RECORD).exists()
    assert run_migration(SimpleNamespace(routines_home=home)) == {}
