"""The suite REPORTS ITS OWN SLOW TAIL — because its stated expectation was never checked.

`pyproject.toml`'s `timeout = 300` carries the expectation in its own words: *"the slowest
legitimate test (a jailed runner's 30 s SIGTERM grace) takes well under a minute. What it
catches is a test that will never finish"*. So between "well under a minute" and 300 s there is
a **299-second blind spot**, and F739 lived in it: `test_remind_global_resolution.py`'s two
parametrized cases filed a blocking approval nobody answered, `interact.handle_ask` polled all
the way to the test routine's inherited `ask_timeout_min: 1`, and the file went from ~130 s to
0.38 s — **~120 s off every release gate the fleet runs**, with no production code changed. The
deadline was never the defect; the test's own budget was. Nobody knows how long it had been
doing that, because nothing recorded it.

The aggregate the gate already captures (`quality 0.4 s / fast 179.1 s / ui 959.7 s`) cannot
localise anything: 179 s is the same number whether it is 2,150 tests at 80 ms or 2,148 at 25 ms
plus two at 60 s. pytest's own `--durations` is the universal first step for exactly that, and it
was off — `durations` appeared nowhere in `pyproject.toml`, `tests/` or `CLAUDE.md`.

This pins the instrument, not a budget. No test is given a deadline here and none is excused: a
flat per-test cap can never go green (the 30 s SIGTERM-grace test is legitimate) and "a gate red
on arrival is a gate someone deletes". What a measurement buys is the per-test numbers in every
gate log, from which a ratchet over the TAIL can later be derived from evidence instead of
chosen — the way `tests/size_ratchet.json` governs file length today.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

#: Report this many of the slowest items. Enough to cover a whole slow FILE's parametrized cases
#: (F739's was two of them) without turning the log into a listing of 2,150 unit tests.
EXPECT_COUNT = 25

#: Only items at or above this many seconds are listed. The expectation being checked is "well
#: under a minute", and the repo's own slow-test evidence (the 30 s SIGTERM grace, the
#: `time.sleep(30/60/120)` timeout fixtures) sits whole seconds above a unit test's milliseconds,
#: so a one-second floor separates the tail from the body with room to spare.
EXPECT_MIN_SECONDS = 1.0


def _addopts() -> list[str]:
    cfg = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())
    return list(cfg["tool"]["pytest"]["ini_options"]["addopts"])


def _value_after(flag: str) -> str:
    """The value pytest would read for `flag`, in either of the two forms addopts may use."""
    opts = _addopts()
    for i, opt in enumerate(opts):
        if opt == flag:
            assert i + 1 < len(opts), f"{flag} is the last entry in addopts, so it has no value"
            return opts[i + 1]
        if opt.startswith(f"{flag}="):
            return opt.split("=", 1)[1]
    raise AssertionError(f"{flag} is not in addopts: {opts}")


def test_every_run_names_its_slowest_items() -> None:
    """Without this, the only durations the project records are per-STAGE aggregates."""
    assert int(_value_after("--durations")) >= EXPECT_COUNT, (
        "a shorter list cannot cover one slow file's parametrized cases: "
        f"{_addopts()}")


def test_the_tail_is_reported_and_not_the_whole_body() -> None:
    """pytest's default floor is 0.005 s, which would list hundreds of ordinary unit tests and
    bury the two items that matter — the failure mode that makes a measurement unread."""
    assert float(_value_after("--durations-min")) >= EXPECT_MIN_SECONDS, (
        "the floor must separate the slow tail from the suite's body: "
        f"{_addopts()}")


def test_the_timeout_comment_still_states_the_expectation_this_measures() -> None:
    """The 300 s deadline and this measurement are two halves of one claim: the deadline catches
    a test that will NEVER finish, the durations list catches one that takes 150× longer than
    the stated expectation and finishes. If that prose ever goes, this check is orphaned.
    """
    text = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text()
    assert "well under a minute" in text, (
        "pyproject.toml no longer states the expectation these durations measure against")
