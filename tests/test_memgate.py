"""A test session waits for room, runs no more workers than fit, and is ended over its cap
(tests/memgate.py, 2026-11-05; collapsed into one module by C7, 2026-11-21)."""

from __future__ import annotations

import signal
import types

import pytest

import memgate


def _fake(readings):
    it = iter(readings)
    clock = {"t": 0.0}

    def read():
        return next(it)

    def sleep(s):
        clock["t"] += s

    return read, sleep, lambda: clock["t"]


# These two hold the WAIT LOOP itself against the HOST reading, so they name a host-bound
# selection. They used to say "browser", which stopped being one on 2026-11-19 (b9): the browser
# stage runs in the uitest container and is read against that container's cgroup alone, so a host
# figure can no longer refuse it and the loop would never be entered. The subject is unchanged.
def test_it_starts_at_once_when_the_host_has_the_memory(monkeypatch):
    monkeypatch.delenv("RSCHED_TEST_MEM_WAIT", raising=False)
    read, sleep, clock = _fake([9000])
    memgate.wait_for_memory("serial", read=read, read_cgroup=lambda: None, sleep=sleep,
                            clock=clock)
    assert clock() == 0


def test_it_waits_until_memory_frees(monkeypatch):
    monkeypatch.delenv("RSCHED_TEST_MEM_WAIT", raising=False)
    read, sleep, clock = _fake([1000, 1200, 4000])
    memgate.wait_for_memory("serial", read=read, read_cgroup=lambda: None, sleep=sleep,
                            clock=clock)
    assert clock() == 30, "two waits of 15 s, then it started"


def test_past_the_deadline_it_starts_under_a_bound_instead_of_refusing(monkeypatch, capsys):
    """D295 (the operator, 2026-11-19): refusing guaranteed no verdict — measured three times on
    2026-11-18, each gate exiting 3 after 1,201.6 s with ZERO tests, and again on 2026-11-19
    when the browser stage spent 1,202 s waiting for 3,584 MB on a box holding 3,008 MB. A
    bounded run risks the same empty outcome and can also succeed, and what the wait protects is
    unchanged: over its bound the watchdog ends the session, so the kernel never chooses a live
    routine run instead.
    """
    monkeypatch.delenv("RSCHED_TEST_MEM_WAIT", raising=False)
    monkeypatch.setenv("RSCHED_TEST_MEM_WAIT_S", "30")
    read, sleep, clock = _fake([500] * 10)
    bound = memgate.wait_for_memory("fast", read=read, read_cgroup=lambda: None, sleep=sleep,
                                    clock=clock, read_hold=lambda: 1264)
    assert bound == 1764, (
        "the bound is in the unit the watchdog READS — the whole cgroup's hold — so what is "
        "still free is added to what the cgroup already holds. Bounding at the free 500 MB raw "
        "would sit below the daemon's own 1,264 MB and kill the session at collection")
    said = capsys.readouterr().err
    assert "STARTING IT ANYWAY" in said and "3584 MB" in said


def test_the_memory_that_was_there_is_what_the_session_may_hold(monkeypatch):
    """The ordinary path returns the CHARGE, not the free headroom: a fast stage legitimately
    holds 3.08-3.29 GB on a box that often has 2.8 GB available, so capping at what happened to
    be free would kill a healthy release gate.
    """
    monkeypatch.delenv("RSCHED_TEST_MEM_WAIT", raising=False)
    read, sleep, clock = _fake([9000])
    assert memgate.wait_for_memory("fast", read=read, read_cgroup=lambda: None, sleep=sleep,
                                   clock=clock) == memgate.NEED_MB["fast"]


def test_the_containers_own_ceiling_holds_the_gate_back_on_a_quiet_host(monkeypatch):
    """Accepted proposal q-20261107-020004-53: host headroom and cgroup headroom are independent
    numbers, and waiting on the first cannot change the second."""
    monkeypatch.delenv("RSCHED_TEST_MEM_WAIT", raising=False)
    _host, sleep, clock = _fake([])
    cgroup = iter([1000, 4000])      # the second reading must clear NEED_MB["fast"]
    memgate.wait_for_memory("fast", read=lambda: 9000, read_cgroup=lambda: next(cgroup),
                            sleep=sleep, clock=clock)
    assert clock() == 15, "one wait on the cgroup, then it started"


def test_the_bound_names_the_resource_that_was_short(monkeypatch, capsys):
    """Whichever of the two numbers was short has to be NAMED: a message nobody can read is what
    sent R3376's author hunting a memory leak in a candidate that had none.
    """
    monkeypatch.delenv("RSCHED_TEST_MEM_WAIT", raising=False)
    monkeypatch.setenv("RSCHED_TEST_MEM_WAIT_S", "30")
    _host, sleep, clock = _fake([])
    bound = memgate.wait_for_memory("fast", read=lambda: 9000, read_cgroup=lambda: 500,
                                    sleep=sleep, clock=clock, read_hold=lambda: 0)
    assert bound == 500
    assert "this container's cgroup still has only 500 MB" in capsys.readouterr().err


def test_cgroup_headroom_counts_what_is_held_never_the_cache(tmp_path):
    """`memory.current` includes page cache the kernel drops at the limit; the headroom is the
    ceiling minus anon and swapped pages, and there is none to read without a numeric ceiling."""
    (tmp_path / "memory.max").write_text("5368709120\n")
    (tmp_path / "memory.stat").write_text("anon 1073741824\nfile 4000000000\n")
    (tmp_path / "memory.swap.current").write_text("536870912\n")
    assert memgate.cgroup_headroom_mb(tmp_path) == 3584
    (tmp_path / "memory.max").write_text("max\n")
    assert memgate.cgroup_headroom_mb(tmp_path) is None
    assert memgate.cgroup_headroom_mb(tmp_path / "missing") is None


def test_an_unreadable_meminfo_waits_for_nothing(tmp_path):
    assert memgate.available_mb(tmp_path / "missing") is None
    (tmp_path / "meminfo").write_text("MemTotal: 100 kB\nMemAvailable:  4194304 kB\n")
    assert memgate.available_mb(tmp_path / "meminfo") == 4096


#: What a SERIAL selection was measured to HOLD, in MB — `held_peak_bytes` at its worst across
#: every `-n 0` stage the gate has sampled whose log is still on disk to prove it was serial
#: (21 rows, 876-2700 MB, median 1965; plus 5 `fast-serial` re-runs at 851-1317). The two 4 GB
#: rows in the same `tests` label are 2-worker whole-suite runs (6,927 items) and are NOT
#: serial, which is why the worker count is read from the log rather than assumed.
MEASURED_SERIAL_HOLD_MB = 2700


def test_a_serial_selection_is_not_charged_the_four_worker_price():
    """R3376: every non-ui selection was charged `fast`, so a two-file `-n 0` call was gated on
    3,584 MB — about four times its own measured hold — and on 2026-11-12 that refused ~12 gate
    calls, including a release gate's whole FAST stage and three child runs that handed back
    built items with no test evidence at all.
    """
    serial = types.SimpleNamespace(option=types.SimpleNamespace(dist="no", numprocesses=0))
    fast = types.SimpleNamespace(option=types.SimpleNamespace(dist="worksteal", numprocesses=4))
    assert memgate.selection_for(serial, "not ui") == "serial"
    assert memgate.selection_for(fast, "not ui") == "fast"
    assert memgate.selection_for(serial, "") == "browser", "markers decide the browser price"
    assert memgate.selection_for(fast, "ui") == "browser"
    assert memgate.NEED_MB["serial"] < memgate.NEED_MB["fast"], (
        "a selection that starts no xdist worker must not be charged for four of them")


def test_the_serial_budget_covers_what_a_serial_selection_was_measured_to_hold():
    """The same floor the fast budget has, for the same reason: a budget below the measured hold
    is a guard that reports safety it never checked. R3376 proposed 1280 MB from this module's
    older n=27 table (0.79-1.20 GB); with 119 rows the serial worst case is 2,700 MB, so the
    cheaper figure would have admitted a selection more than twice what it waited for.
    """
    assert memgate.NEED_MB["serial"] >= MEASURED_SERIAL_HOLD_MB, (
        f"memgate's serial budget is {memgate.NEED_MB['serial']} MB but a serial selection was "
        f"measured holding {MEASURED_SERIAL_HOLD_MB} MB (held_peak_bytes) — raise it")


def test_the_message_says_which_selection_it_priced(monkeypatch, capsys):
    """R3376's second ask, unchanged: "the fast suite needs 3584 MB" printed on a two-file `-n 0`
    call read as a memory leak in the candidate and sent its author looking for one. Since D295
    the line starts the session under a bound rather than refusing it, and still has to name
    what it priced."""
    monkeypatch.delenv("RSCHED_TEST_MEM_WAIT", raising=False)
    monkeypatch.setenv("RSCHED_TEST_MEM_WAIT_S", "0")
    assert memgate.wait_for_memory("serial", read=lambda: 9000, read_cgroup=lambda: 500,
                                   sleep=lambda _s: None, clock=lambda: 1,
                                   read_hold=lambda: 0) == 500
    said = capsys.readouterr().err
    assert "no xdist workers" in said, said
    assert "2816 MB" in said
    assert set(memgate.SELECTION_PROSE) == set(memgate.NEED_MB), (
        "every priced selection needs prose for its message")


#: What the fast stage was measured to HOLD, in MB — `held_peak_bytes` (anon + swapped pages)
#: at its worst across every fast stage the gate has sampled (3.29 GB of the 5 GiB cgroup; the
#: median is 3.16 GB) — the figure an OOM is decided on, unlike `peak_pct_of_limit`.
MEASURED_FAST_HOLD_MB = 3367


def test_the_fast_budget_covers_its_workers_and_its_measured_hold_and_fits_the_cgroup():
    """The checks whose absence let the budget be wrong for a week, silently: it must cover
    FAST_WORKERS at ~600 MB each (measured, four-fold the ~250 MB once assumed) and the stage's
    measured hold, and still fit the 5 GiB engine cgroup with room for the daemon — a budget
    above the ceiling would wait out `RSCHED_TEST_MEM_WAIT_S` on an idle box, forever.
    """
    import browsersource

    fast = memgate.NEED_MB["fast"]
    assert fast >= browsersource.FAST_WORKERS * 600, "raise NEED_MB['fast'] or lower FAST_WORKERS"
    assert fast >= MEASURED_FAST_HOLD_MB, "the gate would wait for less than the stage takes"
    assert fast < 5 * 1024, "the fast budget must fit inside the engine cgroup"
    assert memgate.NEED_MB["browser"] >= browsersource.BROWSER_WORKERS * 250


# ---- who may block: the controller only (2026-11-10, gate 1794268634309389266-release)
#
# `wait_for_memory` is correct and these tests do not touch it. What was wrong is WHO reached
# it: the admission check was `not hasattr(config, "workerinput")`, which is true of a real
# controller AND of the stub a unit test hands the hook directly. Two such nodes blocked for
# the full 300 s pytest timeout inside the fast stage, were rerun, and the gate never reported
# — while holding the shared gate lock, so every other gate on the box queued behind it.


def test_a_worker_is_admitted_without_waiting():
    config = types.SimpleNamespace(option=types.SimpleNamespace(markexpr=""),
                                   workerinput={}, pluginmanager=object())
    assert memgate.should_wait(config) is False


def test_a_real_controller_waits():
    config = types.SimpleNamespace(option=types.SimpleNamespace(markexpr=""),
                                   pluginmanager=object())
    assert memgate.should_wait(config) is True


def test_a_unit_tests_config_stub_never_enters_the_wait():
    """The regression itself, driven through the REAL hook with the REAL stub shape."""
    import conftest as root_conftest

    entered: list[str] = []
    original = memgate.wait_for_memory
    memgate.wait_for_memory = lambda selection, **kw: entered.append(selection)
    try:
        stub = types.SimpleNamespace(
            option=types.SimpleNamespace(dist="no", markexpr="", loadgroup=False))
        root_conftest.pytest_configure(stub)
    finally:
        memgate.wait_for_memory = original
    assert entered == [], (
        f"a config stub reached the memory wait for {entered} — on a loaded box that blocks "
        "for the pytest timeout and the gate holds the shared lock while it does")


# ---- the browser charge and the cgroup it is read against (b9, the operator 2026-11-19) ----

#: the uitest container's ceiling (docker-compose.override.yml `mem_limit: 4g`)
UITEST_MB = 4096
#: what the browser stage really peaked at, per `submit.py`, over the five stages before the fix
MEASURED_BROWSER_PEAKS_MB = (1876, 1831, 1840, 1811, 1797)


def test_the_browser_charge_is_the_measured_peak_not_the_containers_ceiling():
    """A charge equal to the ceiling can never be satisfied while anything else runs in that
    container, which is what refused a stage with 4,040 MB of headroom available."""
    charge = memgate.NEED_MB["browser"]
    assert max(MEASURED_BROWSER_PEAKS_MB) <= charge < UITEST_MB, charge
    assert charge - max(MEASURED_BROWSER_PEAKS_MB) <= 256, "within 256 MB of the worst peak"


def test_a_browser_session_is_read_against_the_container_it_runs_in(monkeypatch):
    """Measured 2026-11-19 inside `uitest`: cgroup headroom 4,040 MB, host 2,290-2,413 MB — so
    reading the host refused a stage the container had ample room for. Every other selection
    keeps being bound by BOTH, which is what D291's worker count needs."""
    readings = dict(memgate.readings_for("browser", lambda: 2413, lambda: 4040))
    assert readings == {"this container's cgroup": 4040}, readings
    for selection in ("fast", "serial"):
        both = dict(memgate.readings_for(selection, lambda: 2413, lambda: 4040))
        assert both == {"host memory": 2413, "this container's cgroup": 4040}, (selection, both)
    monkeypatch.delenv("RSCHED_TEST_MEM_WAIT", raising=False)
    admitted = memgate.wait_for_memory(
        "browser", read=lambda: 2290, read_cgroup=lambda: 4040,
        sleep=lambda _s: pytest.fail("it waited, so the host reading still refuses it"),
        clock=lambda: 0.0)
    assert admitted == memgate.NEED_MB["browser"]


def test_a_browser_stage_that_really_cannot_fit_still_waits(monkeypatch):
    """Not a rubber stamp: when the CONTAINER itself is short, the wait is still the answer."""
    monkeypatch.delenv("RSCHED_TEST_MEM_WAIT", raising=False)
    waits: list[float] = []
    bounded = memgate.wait_for_memory(
        "browser", read=lambda: 8000, read_cgroup=lambda: 500, read_hold=lambda: 100,
        sleep=waits.append, clock=lambda: 0.0 if not waits else 10_000.0)
    assert waits, "a container with 500 MB free took the browser stage without waiting"
    assert bounded == 600, bounded


# ---- the watchdog: an overrun ends the TEST RUN, never a live routine run (D295) ----------

def _watchdog(holds, cap=1000):
    """A watchdog over a scripted sequence of readings, recording what it would have killed."""
    readings = list(holds)
    killed: list[tuple[int, int]] = []
    dog = memgate.Watchdog(cap=cap, pid=4242,
                           read=lambda _pid: readings.pop(0) if readings else 0.0,
                           kill=lambda pid, sig: killed.append((pid, sig)),
                           warn=lambda _line: None)
    return dog, killed


def test_a_session_inside_its_cap_is_never_touched_and_a_zero_reading_never_ends_one():
    """Where there is no cgroup, `cgroup_hold_mb` answers 0.0 — below every cap by construction,
    so the watchdog is inert rather than trigger-happy."""
    dog, killed = _watchdog([100, 400, 999, 0.0], cap=1000)
    for _ in range(4):
        dog.sample()
    assert not killed and dog.killed_at_mb is None


def test_a_session_over_its_cap_is_interrupted_once_however_long_it_lasts():
    """SIGINT, so pytest stops its workers and reports; once, because a session held over its
    cap for a minute must not be signalled every 0.25 s."""
    dog, killed = _watchdog([500, 1500, 1600, 1700], cap=1000)
    dog.sample()
    assert not killed, "the first reading is inside the cap"
    for _ in range(3):
        dog.sample()
    assert killed == [(4242, signal.SIGINT)]
    assert dog.killed_at_mb == 1500


def test_the_hold_is_read_in_the_same_unit_the_charges_were_measured_in(tmp_path):
    """The unit that cost gate 1795100129134213629-release its fast stage: anon + swap, and NOT
    the page cache in `file` that the kernel drops at the limit."""
    (tmp_path / "memory.stat").write_text("anon 2097152000\nfile 4000000000\n")
    (tmp_path / "memory.swap.current").write_text("1048576000\n")
    assert memgate.cgroup_hold_mb(0, tmp_path) == 3000.0
    assert memgate.cgroup_hold_mb(0, tmp_path / "missing") == 0.0, "no cgroup, no bound"


def test_the_cap_is_the_charge_plus_slack_not_the_memory_that_happens_to_be_free(monkeypatch):
    """This box had 2,802 MB available while the fast stage is measured holding 3.08-3.29 GB, so a
    headroom-derived cap would kill a legitimate release gate. A deadline admission smaller than
    the charge never lowers the cap either (1,056 MB stage killed under a 1,024 MB cap)."""
    caps: list[int] = []
    monkeypatch.setattr(memgate.Watchdog, "start", lambda self: caps.append(self.cap) or self)
    fast = types.SimpleNamespace(option=types.SimpleNamespace(dist="worksteal", numprocesses=4))
    for admitted in (memgate.need_mb("fast", 4), 128):
        monkeypatch.setattr(memgate, "wait_for_memory", lambda *_a, _m=admitted, **_k: _m)
        memgate.wait_for_config(fast, "not ui")
    assert caps == [3584 + memgate.SLACK_MB] * 2, caps


def test_the_switch_that_turns_off_the_wait_turns_off_the_watchdog(monkeypatch):
    """"No admission" must mean "nothing is killed": a cap from nothing bounds by no measurement."""
    monkeypatch.setenv("RSCHED_TEST_MEM_WAIT", "0")
    assert memgate.wait_for_memory("browser", read=lambda: 1, read_cgroup=lambda: 1,
                                   sleep=lambda _s: pytest.fail("it waited"), clock=lambda: 0) is None
    monkeypatch.setattr(memgate.Watchdog, "start", lambda self: pytest.fail("it was armed"))
    serial = types.SimpleNamespace(option=types.SimpleNamespace(dist="no", numprocesses=0))
    memgate.wait_for_config(serial, "not ui")
