"""The suite REPORTS ITS OWN SLOW TAIL — because its stated expectation was never checked."""

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
