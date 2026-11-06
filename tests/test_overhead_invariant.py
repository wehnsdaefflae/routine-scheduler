"""The process-overhead INVARIANT: each run's measured split (engine/overhead.py) read back as a
routine's median over its last runs (readmodels/overhead_reading.py), and the four places that
reading reaches — the health event (engine/overhead_alarm.py), the setup surface's row, `rsched
validate` through that row, and the boot digest's FINISH block — plus the one-shot backfill."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from helpers import health_events, measured_run, write_usage_stream
from rsched import cli
from rsched.engine import finish_digest, overhead, overhead_alarm
from rsched.migrate_overhead import RECORD, run_migration
from rsched.readmodels import memo, overhead_reading
from rsched.readmodels.surface_overhead import _overhead_nodes
from rsched.readmodels.usage_stream import stream_path


def _row(i: int, share: float | None, *, slug: str = "r", depth: int = 0) -> dict:
    rec = measured_run(i, slug=slug)
    rec["depth"] = depth
    if share is not None:
        rec["quality"]["overhead"] = {"task": 1, "process": 1, "friction": 0,
                                      "process_share": share}
    return rec


def _stream(home, shares, **kw) -> None:
    write_usage_stream(home, [_row(i + 1, s, **kw) for i, s in enumerate(shares)])


def test_the_reading_is_the_median_of_the_newest_measured_depth0_runs(tmp_path):
    home = tmp_path / "routines"
    rows = [_row(i + 1, s) for i, s in enumerate([0.9, 0.9, 0.1, 0.4, None, 0.5, 0.3, 0.2])]
    rows.append(_row(20, 0.99, depth=1))                     # a child's row is not the routine's
    write_usage_stream(home, rows)
    r = overhead_reading.reading(home, "r")
    # the window is the five newest MEASURED runs; the unmeasured one is left out, never a zero
    assert [x["process_share"] for x in r["runs"]] == [0.1, 0.4, 0.5, 0.3, 0.2]
    assert (r["median"], r["over"], r["judged"]) == (0.3, True, True)
    assert overhead_reading.reading(home, "other") is None


def test_too_few_measured_runs_judge_nothing(tmp_path):
    home = tmp_path / "routines"
    _stream(home, [0.9] * (overhead.MIN_RUNS - 1))
    r = overhead_reading.reading(home, "r")
    assert (r["judged"], r["over"]) == (False, False)


def test_a_routine_under_the_bar_is_not_over(tmp_path):
    home = tmp_path / "routines"
    _stream(home, [0.1, 0.2, 0.15])
    assert overhead_reading.reading(home, "r")["over"] is False


def test_the_alarm_files_once_a_day_and_a_restart_does_not_refile(tmp_path):
    home = tmp_path / "routines"
    _stream(home, [0.5, 0.6, 0.4])
    assert overhead_alarm.file_if_high(home, "r", "r:1") is True
    memo.reset()                       # a new process: nothing remembered but the stream itself
    assert overhead_alarm.file_if_high(home, "r", "r:2") is False
    [ev] = health_events(home, event="process_overhead_high")
    assert (ev["routine"], ev["median"], ev["threshold"]) == ("r", 0.5, overhead.THRESHOLD)
    assert ev["runs"] == ["r:20261001-050000", "r:20261002-050000", "r:20261003-050000"]
    later = datetime.now(UTC) + timedelta(hours=overhead_alarm.LATCH_HOURS + 1)
    assert overhead_alarm.filed_recently(home, "r", now=later) is False


def test_the_alarm_is_silent_under_the_bar(tmp_path):
    home = tmp_path / "routines"
    _stream(home, [0.1, 0.1, 0.1])
    assert overhead_alarm.file_if_high(home, "r", "r:1") is False
    assert health_events(home, event="process_overhead_high") == []


def _cfg(tmp_path, *, enabled: bool = True):
    return SimpleNamespace(slug="r", dir=tmp_path / "routines" / "r", enabled=enabled)


def test_the_surface_row_names_the_figure_and_the_recipe_remedy(tmp_path):
    home = tmp_path / "routines"
    _stream(home, [0.5, 0.6, 0.4])
    server = SimpleNamespace(routines_home=home)
    [node] = _overhead_nodes(server, _cfg(tmp_path))
    assert (node["id"], node["severity"], node["source"]) == (
        "recipe:overhead", "note", {"recipe": "overhead"})
    assert node["fix"] == {"kind": "revise_recipe", "share": "50%"}
    assert "50%" in node["why"] and "27%" in node["why"]
    assert _overhead_nodes(server, _cfg(tmp_path, enabled=False)) == []


def test_validate_prints_a_note_and_stays_green(cli_server, make_routine, capsys):
    from helpers import declare_outcome

    d = make_routine(slug="testr")
    declare_outcome(d)
    _stream(cli_server.routines_home, [0.5, 0.6, 0.4], slug="testr")
    assert cli.cmd_validate(SimpleNamespace(routine="testr")) == 0
    out = capsys.readouterr().out
    assert "NOTE  recipe:overhead: its runs are dominated by its own process" in out
    assert "fix: revise its recipe so its runs stop spending 50%" in out


def test_the_finish_block_says_it_beside_the_recipe_ask(tmp_path):
    home = tmp_path / "routines"
    d = home / "r"
    d.mkdir(parents=True)
    _stream(home, [0.5, 0.6, 0.4])
    block = finish_digest.digest_section(d)
    assert block.index("RECIPE REVISIONS:") < block.index("PROCESS OVERHEAD: for this routine,")
    assert "spent 50% of their actions" in block
    assert "PROCESS OVERHEAD" not in finish_digest.digest_section(d, recipe_ask=False)
    _stream(home, [0.1, 0.1, 0.1, 0.1])          # a different size: the memo sees the change
    assert "PROCESS OVERHEAD" not in finish_digest.digest_section(d)


def test_the_backfill_measures_the_newest_leg_of_each_kept_run_once(tmp_path):
    home = tmp_path / "routines"
    run_dir = home / "digest" / "runs" / "20261001-050000"
    run_dir.mkdir(parents=True)
    (run_dir / "transcript.jsonl").write_text(json.dumps(
        {"type": "assistant_action", "payload": {"kind": "memory_read", "name": "n"}}) + "\n",
        encoding="utf-8")
    leg1, leg2, gone = measured_run(1), measured_run(1), measured_run(2)
    write_usage_stream(home, [leg1, leg2, gone])
    record = run_migration(SimpleNamespace(routines_home=home))
    assert (record["runs"], record["counted"], record["unknown"]) == (2, 1, 1)
    rows = [json.loads(ln) for ln in stream_path(home).read_text(encoding="utf-8").splitlines()]
    assert "overhead" not in rows[0]["quality"]
    assert rows[1]["quality"]["overhead"] == {"task": 0, "process": 1, "friction": 0,
                                              "process_share": 1.0}
    assert "overhead" not in rows[2]["quality"]           # its dir is gone: unmeasured
    assert (home / RECORD).exists()
    assert run_migration(SimpleNamespace(routines_home=home)) == {}
