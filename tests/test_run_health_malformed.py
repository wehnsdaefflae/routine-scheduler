"""A malformed number in the durable usage stream is NO READING — never a TypeError out of the
read model, and never a silent 0 (R3138, raised by scheduler-builder).

`regression_flag.profile` and `_by_version` both did `float(rec["volume"])` behind an
`is not None` guard, so a row whose `volume` was written as a dict or a stray string raised
`TypeError: float() argument must be a string or a real number` out of three production
surfaces: the routine page's development line (`recipe_regression` →
`api_changes.routine_summary`), `GET /routines/{slug}/health` (`routine_health`), and run-end
telemetry (`engine/runtime._log_cost_trend` → `recent_trend`). `fold_legs` sanitizes only runs
with SEVERAL legs — "a run with one leg keeps its raw values" — so a single-leg row arrives
exactly as written.

A 0 is not an acceptable stand-in either: it is itself a reading, and one of them drags a
median far enough to fire a regression flag on its own.
"""

from rsched.readmodels.run_health import recent_trend, regression_flag
from rsched.readmodels.usage_stream import reading


def _rec(volume=500_000, **extra):
    return {"status": "ok", "turns": 10, "volume": volume, **extra}


def test_a_malformed_volume_raises_nothing_and_counts_as_no_reading():
    """The dict is the shape measured on 2026-11-01; the median is the four good rows', not
    the five rows' with a 0 among them."""
    good = [_rec(volume=4_000_000) for _ in range(4)]
    verdict = regression_flag([_rec(volume=1_000_000) for _ in range(5)],
                              [*good, _rec(volume={"not": "a number"})])

    assert verdict["evaluated"]
    assert verdict["after"]["volume_median"] == 4_000_000      # not 0, and not dragged by one
    assert verdict["after"]["runs"] == 5                       # the RUN still counts


def test_every_unusable_shape_is_skipped_not_coerced():
    """A string, a list and a bool are all "no reading": `reading` is the stream's one
    definition of a number, so no reader disagrees with another about what one is."""
    assert reading(5) == 5.0 and reading(2.5) == 2.5
    for bad in ({"a": 1}, "9000", [1], True, False, None, object()):
        assert reading(bad) is None, bad


def test_a_whole_window_of_malformed_rows_is_no_median_rather_than_zero():
    """With nothing usable the median is the empty-list answer (0.0) and the flag cannot fire
    off it: the balloon test needs BOTH a ratio and an absolute floor."""
    bad = [_rec(volume={}) for _ in range(5)]
    verdict = regression_flag(bad, bad)
    assert verdict["evaluated"]
    assert verdict["before"]["volume_median"] == 0 and verdict["after"]["volume_median"] == 0
    assert not verdict["flagged"]


def test_run_end_telemetry_does_not_raise_on_a_malformed_row():
    """`recent_trend` is called at RUN END (`_log_cost_trend`) — a malformed row there raised
    inside the engine's own telemetry."""
    records = [_rec(run_id=f"r{i}") for i in range(10)]
    records[-1]["volume"] = {"not": "a number"}
    trend = recent_trend(records)
    assert trend["evaluated"] and trend["window"]


def test_a_malformed_turns_value_is_not_a_reading_either():
    """`float(rec.get("turns") or 0)` raised on the same shapes. Turns keep their established
    absent-is-0 contract — only the raise is gone."""
    verdict = regression_flag([_rec(turns=10) for _ in range(5)],
                              [_rec(turns={"a": 1}), *[_rec(turns=10) for _ in range(4)]])
    assert verdict["evaluated"] and verdict["after"]["turns_median"] == 10
