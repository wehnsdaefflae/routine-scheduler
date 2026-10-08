"""Which routine a run belonged to (readmodels/incarnations.py): a slug archived and created
again names two routines, and every reader that compares or sums one routine's runs keys them by
the routine, never by the bare slug."""

from __future__ import annotations

from helpers import measured_run, server_for, write_usage_stream
from rsched import trials
from rsched.readmodels import memo, run_health
from rsched.readmodels.compression_stats import compression_stats
from rsched.readmodels.incarnations import live_runs, resolver
from rsched.readmodels.stats import monthly_spend

STAMP = "20261004-000000"           # `digest` archived on 10-04 and created again


def _home(tmp_path):
    home = tmp_path / "routines"
    for d in (home / "digest", home / ".archive" / f"digest-{STAMP}", home / ".archive" / "gone"):
        d.mkdir(parents=True)
        (d / "routine.yaml").write_text("slug: x\n", encoding="utf-8")
    runs = [{**measured_run(i, trial="t-1"), "compression": {"applied": 1, "tokens_saved": 10}}
            for i in (1, 2, 5, 6)]
    runs.append({**measured_run(3, slug="gone"), "compression": {"applied": 1}})
    runs.append(measured_run(4, slug="c-20261004-120000"))
    write_usage_stream(home, runs)
    memo.reset()
    return home, runs


def test_a_run_belongs_to_the_routine_that_held_the_slug_when_it_started(tmp_path):
    home, runs = _home(tmp_path)
    name_of = resolver(home)
    assert [name_of(r) for r in runs] == [
        f"digest-{STAMP}", f"digest-{STAMP}", "digest", "digest",
        "gone",                                   # an unstamped archive, no live successor
        "c-20261004-120000"]                      # a conversation: no routine claims it
    assert [r["run_id"] for r in live_runs(home, runs, "digest")] == [
        "digest:20261005-050000", "digest:20261006-050000"]


def test_the_readers_of_one_routines_runs_see_only_its_own(tmp_path):
    home, _runs = _home(tmp_path)
    server = server_for(home / "digest")
    assert [r["run_id"] for r in run_health._stream_runs(server, "digest")] == [
        "digest:20261005-050000", "digest:20261006-050000"]
    assert trials.recorded(home, "digest", "t-1") == 2
    spend = monthly_spend(server)["by_routine"]
    assert spend["digest"]["2026-10"]["runs"] == 2
    assert spend[f"digest-{STAMP}"]["2026-10"]["runs"] == 2
    rows = {r["routine"]: r["runs"] for r in compression_stats(server)["rows"]}
    assert rows == {"digest": 2, f"digest-{STAMP}": 2, "gone": 1}
