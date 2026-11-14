"""The process-overhead INVARIANT: each run's measured split (engine/overhead.py) read back as a
routine's median over its last runs (readmodels/overhead_reading.py), and the four places that
reading reaches — the health event (engine/overhead_alarm.py), the setup surface's row, `rsched
validate` through that row, and the boot digest's FINISH block — plus the one-shot backfill."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from helpers import health_events, measured_run, write_usage_stream
from rsched import cli
from rsched.engine import finish_digest, overhead, overhead_alarm
from rsched.migrate_overhead import RECORD, run_migration
from rsched.readmodels import memo, overhead_reading
from rsched.readmodels.surface_overhead import _overhead_nodes
from rsched.readmodels.usage_stream import stream_path


def _row(i: int, share: float | None, *, slug: str = "r", depth: int = 0,
         recipe: str | None = None, landed: int = 20) -> dict:
    """A measured row whose counts sum to `landed` — a real run's scale, not two actions. The
    reading reads the RECORDED `process_share` and never recomputes it, so `share` is verbatim;
    the counts carry the total `overhead.MIN_ACTIONS` is read against. `landed` under the floor
    makes a run too short to be evidence."""
    rec = measured_run(i, slug=slug)
    rec["depth"], rec["recipe_commit"] = depth, recipe     # a dir with no git: recipe None
    if share is not None:
        process = round(share * landed)
        rec["quality"]["overhead"] = {"task": landed - process, "process": process,
                                      "friction": 0, "process_share": share}
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
    _stream(cli_server.routines_home, [0.5, 0.6, 0.4], slug="testr",
            recipe=overhead_reading.current_recipe(d))
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


def _commit_recipe(d, text: str) -> str:
    def git(*args: str) -> str:
        return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                              cwd=d, check=True, capture_output=True, text=True).stdout.strip()
    if not (d / ".git").is_dir():
        git("init", "-q")
    (d / "main.md").write_text(text, encoding="utf-8")
    git("add", "main.md")
    git("commit", "-qm", "recipe")
    return git("log", "-1", "--format=%H")


def test_a_recipe_change_resets_the_window_and_silences_alarm_and_digest(tmp_path):
    """The 2026-11-06 sweep cut every recipe's bookkeeping in one go: the runs of the OLD
    recipe must not go on telling the repaired routine to revise bookkeeping it no longer has —
    nothing is judged until MIN_RUNS runs exist under the new recipe."""
    home = tmp_path / "routines"
    d = home / "r"
    d.mkdir(parents=True)
    old = _commit_recipe(d, "# old recipe, with a ledger stage\n")
    rows = [_row(i + 1, 0.6, recipe=old) for i in range(5)]
    write_usage_stream(home, rows)
    assert overhead_reading.reading(home, "r")["over"] is True
    new = _commit_recipe(d, "# repaired recipe\n")
    assert overhead_reading.reading(home, "r") is None             # no run of it yet
    assert "PROCESS OVERHEAD" not in finish_digest.digest_section(d)
    assert overhead_alarm.file_if_high(home, "r", "r:x", d) is False
    for n in range(overhead.MIN_RUNS):
        rows.append(_row(10 + n, 0.6, recipe=new))
        write_usage_stream(home, rows)
        r = overhead_reading.reading(home, "r")
        assert [x["run_id"] for x in r["runs"]] == [x["run_id"] for x in rows[5:]]
        judged = n + 1 >= overhead.MIN_RUNS
        assert (r["judged"], r["over"]) == (judged, judged)
        assert ("PROCESS OVERHEAD" in finish_digest.digest_section(d)) is judged
    assert overhead_alarm.file_if_high(home, "r", "r:y", d) is True


def test_a_run_too_short_to_be_evidence_is_set_aside_not_judged(tmp_path):
    """`library-sync`'s measured shape (R3390 via R3404): FOUR landed actions — two task utils,
    the outcome script and the finish — reading 0.5 for doing everything right. Two actions of a
    correct run are process by rule, so its bar is unreachable; such a run is not evidence."""
    home = tmp_path / "routines"
    write_usage_stream(home, [_row(i + 1, 0.5, landed=4) for i in range(5)])
    assert overhead_reading.reading(home, "r") is None
    assert overhead_alarm.file_if_high(home, "r", "r:1") is False
    assert health_events(home, event="process_overhead_high") == []
    server = SimpleNamespace(routines_home=home)
    assert _overhead_nodes(server, _cfg(tmp_path)) == []


def test_short_runs_leave_the_window_and_the_long_ones_still_judge(tmp_path):
    home = tmp_path / "routines"
    rows = [_row(1, 0.5, landed=4), _row(2, 0.5, landed=4)]                 # set aside
    rows += [_row(i + 3, s, landed=20) for i, s in enumerate([0.1, 0.2, 0.15])]
    write_usage_stream(home, rows)
    r = overhead_reading.reading(home, "r")
    assert [x["process_share"] for x in r["runs"]] == [0.1, 0.2, 0.15]
    assert (r["too_short"], r["min_actions"]) == (2, overhead.MIN_ACTIONS)
    assert (r["judged"], r["over"]) == (True, False)


def test_the_floor_is_derived_from_the_bar_and_the_two_unavoidable_actions():
    """Not a chosen number: the outcome script and the finish are process in every routine, so
    the floor under any correct run's share is 2/total. MIN_ACTIONS is the smallest total at
    which that floor alone cannot clear the bar — one lower would flag a run that did everything
    right, one higher would stop judging real runs."""
    unavoidable = 2
    assert unavoidable / overhead.MIN_ACTIONS < overhead.THRESHOLD
    assert unavoidable / (overhead.MIN_ACTIONS - 1) >= overhead.THRESHOLD
