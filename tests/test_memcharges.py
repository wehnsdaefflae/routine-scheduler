"""The charges, and which reading may refuse a selection (b9, the operator 2026-11-19)."""
from __future__ import annotations

import pytest

import memcharges
import memgate

#: the uitest container's ceiling (docker-compose.override.yml `mem_limit: 4g`)
UITEST_MB = 4096
#: what the browser stage really peaked at, per `submit.py`, over the five stages before the fix
MEASURED_PEAKS_MB = (1876, 1831, 1840, 1811, 1797)


def test_the_browser_charge_is_the_measured_peak_not_the_containers_ceiling():
    """The charge must cover the worst hold ever measured and must not be the ceiling itself: a
    charge equal to the ceiling can never be satisfied while anything else runs in that
    container, which is what made it refuse a stage with 4,040 MB of headroom available."""
    charge = memcharges.NEED_MB["browser"]
    assert charge >= max(MEASURED_PEAKS_MB), (
        f"{charge} MB does not cover the worst measured browser hold "
        f"{max(MEASURED_PEAKS_MB)} MB")
    assert charge < UITEST_MB, (
        f"{charge} MB is the uitest container's own ceiling ({UITEST_MB}), not a hold — the "
        "defect this replaced")
    # …and not so generous that the measurement was pointless: within 256 MB of the worst peak
    assert charge - max(MEASURED_PEAKS_MB) <= 256, charge


def test_a_browser_session_is_read_against_the_container_it_runs_in():
    """`submit.py` hands the ui stage to the `uitest` container, so this module executes INSIDE
    it: `/sys/fs/cgroup` is that container's own 4 GiB one and `/proc/meminfo` is still the whole
    host's. Measured 2026-11-19 inside it — cgroup headroom 4,040 MB, host 2,413 MB — so reading
    the host refused a stage the container had ample room for."""
    readings = dict(memcharges.readings_for("browser", lambda: 2413, lambda: 4040))
    assert readings == {"this container's cgroup": 4040}, readings

    # every other selection keeps being bound by BOTH, which is what D291's wiring needs
    for selection in ("fast", "serial"):
        both = dict(memcharges.readings_for(selection, lambda: 2413, lambda: 4040))
        assert both == {"host memory": 2413, "this container's cgroup": 4040}, (selection, both)


def test_the_host_being_short_no_longer_refuses_the_browser_stage(monkeypatch):
    """The whole point, end to end: the exact numbers of 2026-11-19 — host 2,290 MB, uitest
    cgroup with room — must now ADMIT, where they produced a 1,202 s wait and zero tests."""
    monkeypatch.delenv("RSCHED_TEST_MEM_WAIT", raising=False)
    admitted = memgate.wait_for_memory(
        "browser",
        read=lambda: 2290,                      # the host, as it read at 17:47
        read_cgroup=lambda: 4040,               # the uitest container, as it read the same minute
        sleep=lambda _s: pytest.fail("it waited, so the host reading still refuses it"),
        clock=lambda: 0.0)
    assert admitted == memcharges.NEED_MB["browser"]


def test_a_browser_stage_that_really_cannot_fit_still_waits(monkeypatch):
    """The guard must not become a rubber stamp: when the CONTAINER itself is short — another
    job in it, or a ceiling lowered — the wait is still the right answer."""
    monkeypatch.delenv("RSCHED_TEST_MEM_WAIT", raising=False)
    waits: list[float] = []

    def clock() -> float:                       # one wait, then the deadline
        return 0.0 if not waits else 10_000.0

    bounded = memgate.wait_for_memory(
        "browser",
        read=lambda: 8000,                      # the host is fine
        read_cgroup=lambda: 500,                # the container is not
        read_hold=lambda: 100,
        sleep=waits.append,
        clock=clock)
    assert waits, "a container with 500 MB free took the browser stage without waiting"
    # and past the deadline it starts under a bound rather than refusing (D295)
    assert bounded == 600, bounded
