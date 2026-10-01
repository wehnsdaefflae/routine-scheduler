"""Per-routine output-compression roll-up (rsched.readmodels.compression_stats): the
stream is the source, records without the tally are OUTSIDE the window (never a zero) and
children count as themselves.
"""


import yaml

from helpers import stats_server, write_usage_stream
from rsched.readmodels.compression_stats import compression_stats


def _routine(server, slug):
    d = server.routines_home / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "routine.yaml").write_text(yaml.safe_dump(
        {"name": slug, "slug": slug, "enabled": True, "description": "t"}), encoding="utf-8")


def _rec(slug, ts, compression, *, depth=0):
    return {"routine": slug, "run_id": f"{slug}:{ts}", "depth": depth, "status": "ok",
            "turns": 3, "tokens": 900, "ts": ts, "compression": compression}


def test_rolls_up_per_routine_and_orders_by_saving(tmp_path):
    server = stats_server(tmp_path)
    _routine(server, "alpha")
    _routine(server, "beta")
    write_usage_stream(server.routines_home, [
        _rec("alpha", "2026-09-11T07:00:00+00:00",
             {"applied": 2, "skipped": 40, "fallback": 1, "tokens_saved": 500, "ms": 120.5}),
        # a CHILD counts as itself: the parent folds nothing in
        _rec("alpha", "2026-09-11T07:05:00+00:00",
             {"applied": 1, "unchanged": 2, "tokens_saved": 100, "ms": 80.0}, depth=1),
        _rec("beta", "2026-09-12T07:00:00+00:00",
             {"unchanged": 3, "skipped": 7, "tokens_saved": 0, "ms": 40.0}),
    ])

    out = compression_stats(server)
    assert [r["routine"] for r in out["rows"]] == ["alpha", "beta"]   # by saving, desc
    alpha = out["rows"][0]
    assert alpha["runs"] == 2
    assert alpha["applied"] == 3
    assert alpha["tokens_saved"] == 600
    assert alpha["fallback"] == 1
    assert alpha["unchanged"] == 2
    assert alpha["skipped"] == 40
    assert alpha["candidates"] == 46     # every status
    assert alpha["attempts"] == 6        # the compressor actually ran
    assert alpha["seconds"] == 0.2
    assert alpha["first"] == "2026-09-11T07:00:00+00:00"
    assert alpha["last"] == "2026-09-11T07:05:00+00:00"
    assert out["rows"][1]["unchanged"] == 3
    assert out["totals"]["tokens_saved"] == 600
    assert out["totals"]["applied"] == 3
    assert out["totals"]["candidates"] == 56
    assert out["records"] == 3
    assert out["since"] == "2026-09-11T07:00:00+00:00"


def test_a_continued_run_is_one_run_with_each_legs_tally_summed(tmp_path):
    """Each leg of a continued run files its own record carrying its OWN tally. Read per
    record, one run read as two runs."""
    server = stats_server(tmp_path)
    _routine(server, "alpha")
    first = _rec("alpha", "2026-09-11T07:00:00+00:00",
                 {"applied": 2, "tokens_saved": 300, "ms": 100.0})
    second = {**_rec("alpha", "2026-09-11T09:00:00+00:00",
                     {"applied": 1, "tokens_saved": 100, "ms": 100.0}),
              "run_id": first["run_id"]}
    write_usage_stream(server.routines_home, [first, second])
    out = compression_stats(server)
    row = out["rows"][0]
    assert (row["runs"], row["applied"], row["tokens_saved"], row["seconds"]) == (1, 3, 400, 0.2)
    assert out["records"] == 1


def test_records_without_the_tally_are_outside_the_window(tmp_path):
    """A pre-counter record is not a routine that compressed nothing — it is a run the
    counter never saw. It must not appear as a zero row or move `since`.
    """
    server = stats_server(tmp_path)
    _routine(server, "alpha")
    write_usage_stream(server.routines_home, [
        {"routine": "alpha", "run_id": "alpha:1", "depth": 0, "status": "ok",
         "turns": 1, "tokens": 10, "ts": "2026-09-01T07:00:00+00:00"},
        _rec("alpha", "2026-09-11T07:00:00+00:00", {"applied": 1, "tokens_saved": 9}),
    ])
    out = compression_stats(server)
    assert out["records"] == 1
    assert out["since"] == "2026-09-11T07:00:00+00:00"
    assert out["rows"][0]["runs"] == 1


def test_a_deleted_routine_stays_readable(tmp_path):
    """A slug with no directory any more keeps its row: the history is still true."""
    server = stats_server(tmp_path)
    write_usage_stream(server.routines_home,
                       [_rec("gone", "2026-09-11T08:00:00+00:00", {"skipped": 3, "ms": 0.0})])
    rows = {r["routine"]: r for r in compression_stats(server)["rows"]}
    assert rows["gone"]["skipped"] == 3


def test_malformed_counts_never_raise(tmp_path):
    server = stats_server(tmp_path)
    _routine(server, "alpha")
    write_usage_stream(server.routines_home, [
        _rec("alpha", "2026-09-11T07:00:00+00:00",
             {"applied": "two", "tokens_saved": None, "ms": {"nope": 1}}),
    ])
    row = compression_stats(server)["rows"][0]
    assert row["applied"] == 0
    assert row["tokens_saved"] == 0
    assert row["seconds"] == 0.0


def test_empty_instance_reports_an_empty_window(tmp_path):
    out = compression_stats(stats_server(tmp_path))
    assert out == {"rows": [], "totals": out["totals"], "since": None, "records": 0}
    assert out["totals"]["tokens_saved"] == 0
