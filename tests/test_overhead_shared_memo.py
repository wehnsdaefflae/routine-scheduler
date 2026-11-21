"""The overhead fold is handed out SHARED: a reading copies nothing and changes nothing.

`readmodels/overhead_reading.reading` wants one routine's slice of a fleet-wide fold (907 runs
over 35 routines on the live instance), and `/api/health/blocked` asks it once per
`process_overhead_high` row. Memoized with a deep copy per hit, that was 2.6-4.1 ms of copying per
row per request under the GIL, and a slow-sample caught the request thread inside it. The fold is
now `memo.memoized_shared`; these tests pin the two halves of that contract — no copy on the hit,
and no reader ever writing into the shared value.
"""

from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace

from helpers import measured_run, write_usage_stream
from rsched.readmodels import memo, overhead_reading
from rsched.readmodels.usage_stream import stream_path


def _row(i: int, share: float, slug: str) -> dict:
    rec = measured_run(i, slug=slug)
    rec["depth"], rec["recipe_commit"] = 0, None
    process = round(share * 20)
    rec["quality"]["overhead"] = {"task": 20 - process, "process": process,
                                  "friction": 0, "process_share": share}
    return rec


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "routines"
    write_usage_stream(home, [_row(i, s, slug) for i, (s, slug) in enumerate(
        [(0.9, "a"), (0.8, "b"), (0.9, "a"), (0.1, "b"), (0.9, "a"), (0.2, "b")], start=1)])
    memo.reset()
    return home


def _shared(home: Path) -> dict:
    """The cached fold itself, through the same key `reading` uses."""
    path = stream_path(home)
    return memo.memoized_shared(f"overhead-measured:{path}", [path],
                                lambda: overhead_reading._measured(home))


def test_a_reading_from_a_warm_fold_deep_copies_nothing(tmp_path, monkeypatch):
    home = _home(tmp_path)
    assert overhead_reading.reading(home, "a") is not None      # warms the memo

    def no_copy(_value, *_a, **_kw):
        raise AssertionError("a reading deep-copied the fleet-wide fold")

    monkeypatch.setattr(memo, "copy", SimpleNamespace(deepcopy=no_copy))
    for slug in ("a", "b", "a", "missing"):
        overhead_reading.reading(home, slug)


def test_no_reading_writes_into_the_shared_fold(tmp_path):
    home = _home(tmp_path)
    before = copy.deepcopy(_shared(home))
    for slug in ("a", "b", "missing"):
        r = overhead_reading.reading(home, slug)
        if r is not None:                       # a caller may do what it likes with its reading
            r["runs"][0]["process_share"] = -1.0
            r["runs"].clear()
            r["over"] = None
    assert _shared(home) == before


def test_the_reading_still_agrees_after_its_caller_mangled_the_last_one(tmp_path):
    home = _home(tmp_path)
    first = overhead_reading.reading(home, "a")
    assert first is not None
    expected = copy.deepcopy(first)
    first["runs"][0]["process_share"] = -1.0
    assert overhead_reading.reading(home, "a") == expected
