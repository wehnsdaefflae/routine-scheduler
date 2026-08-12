"""The shared spool mechanic (F286) — chrono naming carries F298's queue-order contract."""

from pathlib import Path

import pytest

from rsched import spool


def test_same_second_burst_sorts_in_write_order(tmp_path: Path):
    # F298: a burst inside one second must replay in queue order — the nanosecond sample
    # in the name makes the sort strict (random hex alone shuffled it).
    written = [spool.write(tmp_path, "pending-edits", "r", {"n": i}, prefix="pe")
               for i in range(12)]
    assert spool.pending(tmp_path, "pending-edits", "r", "pe") == written


def test_write_takes_exactly_one_of_prefix_and_name(tmp_path: Path):
    with pytest.raises(ValueError):
        spool.write(tmp_path, "triggers", "r", {}, prefix="evt", name="evt-x.json")
    with pytest.raises(ValueError):
        spool.write(tmp_path, "triggers", "r", {})


def test_id_addressed_write_uses_the_verbatim_name(tmp_path: Path):
    p = spool.write(tmp_path, "schedule-once", "r", {"id": "so-1"}, name="req-so-1.json")
    assert p.name == "req-so-1.json"
    assert spool.pending(tmp_path, "schedule-once", "r", "req") == [p]


def test_pending_filters_by_prefix_and_survives_missing_dir(tmp_path: Path):
    assert spool.pending(tmp_path, "triggers", "nope", "evt") == []
    spool.write(tmp_path, "triggers", "r", {}, prefix="evt")
    (spool.spool_dir(tmp_path, "triggers", "r") / "state.json").write_text("{}")
    assert len(spool.pending(tmp_path, "triggers", "r", "evt")) == 1
