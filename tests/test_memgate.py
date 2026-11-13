"""A test session waits for the HOST to have the memory it needs (tests/memgate.py, 2026-11-05)."""

from __future__ import annotations

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


def test_it_starts_at_once_when_the_host_has_the_memory(monkeypatch):
    monkeypatch.delenv("RSCHED_TEST_MEM_WAIT", raising=False)
    read, sleep, clock = _fake([9000])
    memgate.wait_for_memory("browser", read=read, read_cgroup=lambda: None, sleep=sleep,
                            clock=clock)
    assert clock() == 0


def test_it_waits_until_memory_frees(monkeypatch):
    monkeypatch.delenv("RSCHED_TEST_MEM_WAIT", raising=False)
    read, sleep, clock = _fake([1000, 1200, 4000])
    memgate.wait_for_memory("browser", read=read, read_cgroup=lambda: None, sleep=sleep,
                            clock=clock)
    assert clock() == 30, "two waits of 15 s, then it started"


def test_it_refuses_to_start_past_the_deadline(monkeypatch):
    monkeypatch.delenv("RSCHED_TEST_MEM_WAIT", raising=False)
    monkeypatch.setenv("RSCHED_TEST_MEM_WAIT_S", "30")
    read, sleep, clock = _fake([500] * 10)
    with pytest.raises(pytest.exit.Exception) as exc:
        memgate.wait_for_memory("fast", read=read, read_cgroup=lambda: None, sleep=sleep,
                            clock=clock)
    assert "500 MB available" in str(exc.value) and "3584 MB" in str(exc.value)


def test_the_containers_own_ceiling_holds_the_gate_back_on_a_quiet_host(monkeypatch):
    """Accepted proposal q-20261107-020004-53: host headroom and cgroup headroom are independent
    numbers, and waiting on the first cannot change the second."""
    monkeypatch.delenv("RSCHED_TEST_MEM_WAIT", raising=False)
    _host, sleep, clock = _fake([])
    cgroup = iter([1000, 4000])      # the second reading must clear NEED_MB["fast"]
    memgate.wait_for_memory("fast", read=lambda: 9000, read_cgroup=lambda: next(cgroup),
                            sleep=sleep, clock=clock)
    assert clock() == 15, "one wait on the cgroup, then it started"


def test_the_refusal_names_the_resource_that_refused(monkeypatch):
    monkeypatch.delenv("RSCHED_TEST_MEM_WAIT", raising=False)
    monkeypatch.setenv("RSCHED_TEST_MEM_WAIT_S", "30")
    _host, sleep, clock = _fake([])
    with pytest.raises(pytest.exit.Exception) as exc:
        memgate.wait_for_memory("fast", read=lambda: 9000, read_cgroup=lambda: 500,
                                sleep=sleep, clock=clock)
    assert "this container's cgroup: 500 MB available" in str(exc.value)


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


def test_the_switch_turns_it_off(monkeypatch):
    monkeypatch.setenv("RSCHED_TEST_MEM_WAIT", "0")
    memgate.wait_for_memory("browser", read=lambda: 1, read_cgroup=lambda: 1,
                            sleep=lambda _s: None, clock=lambda: 0)


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

    `-n 0` leaves `dist == "no"` and `numprocesses == 0`; the default selection resolves to
    `worksteal` with 4 (measured on this box, pytest 9.1.1 / xdist 3.8.0). The browser suite is
    priced by its markers instead, because its cost is the Chromiums rather than the workers.
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


def test_the_refusal_says_which_selection_it_priced(monkeypatch):
    """R3376's second ask: "the fast suite needs 3584 MB" printed on a two-file `-n 0` call read
    as a memory leak in the candidate and sent its author looking for one."""
    monkeypatch.delenv("RSCHED_TEST_MEM_WAIT", raising=False)
    monkeypatch.setenv("RSCHED_TEST_MEM_WAIT_S", "0")
    with pytest.raises(pytest.exit.Exception) as exc:
        memgate.wait_for_memory("serial", read=lambda: 9000, read_cgroup=lambda: 500,
                                sleep=lambda _s: None, clock=lambda: 1)
    assert "no xdist workers" in str(exc.value), str(exc.value)
    assert "2816 MB" in str(exc.value)
    assert set(memgate.SELECTION_PROSE) == set(memgate.NEED_MB), (
        "every priced selection needs prose for its refusal")


def test_the_fast_budget_still_covers_the_worker_count_it_claims_to():
    """The two halves must move together — nothing has ever checked that they do.

    `memgate`'s own docstring says its needs ARE "the measured peaks of the selections: the fast
    suite at FAST_WORKERS workers (~250 MB each, plus the controller)". That makes `NEED_MB`
    arithmetic over a constant in another module, so raising `FAST_WORKERS` without raising
    `NEED_MB` leaves the guard describing a selection that no longer exists — silently, because
    a too-small budget does not fail, it merely stops waiting when it should have waited.

    THE FLOOR IS NOW THE MEASURED COST (2026-11-10, goal b8), and the old one was too weak to
    catch the violation that was live: at ~250 MB per worker the floor was 1,000 MB against a
    2048 MB budget, so it passed while the fast stage was really HOLDING 3,367 MB — the guard
    admitted a selection that could not fit in what it waited for. The gate's sampler now
    records `held_peak_bytes` (anon + swap, what an OOM is decided on), and over every stage
    carrying it the fast stage's increment above a bare controller is 2.25 GB median and 2.38 GB
    at its worst — about 600 MB per worker. `memgate.NEED_MB`'s own comment holds the table.

    So this pins the RELATION at a per-worker cost that is read rather than assumed: raising
    `FAST_WORKERS` without raising `NEED_MB` now fails, which is the whole point of the test —
    a too-small budget does not announce itself, it merely stops waiting when it should wait.
    Either number may still be re-tuned, but not alone.
    """
    import browsersource

    workers = browsersource.FAST_WORKERS
    #: MB a fast xdist worker really holds, from the gate's `held_peak_bytes` rows: the fast
    #: stage's increment over a `-n 0` controller, at its WORST, divided by the worker count.
    #: Four-fold the ~250 MB this test used to assume.
    per_worker = 600
    floor = workers * per_worker
    assert memgate.NEED_MB["fast"] >= floor, (
        f"memgate's fast budget is {memgate.NEED_MB['fast']} MB but FAST_WORKERS={workers} at "
        f"~{per_worker} MB each (measured, held_peak_bytes) needs at least {floor} MB — raise "
        "NEED_MB['fast'] or lower FAST_WORKERS")
    assert memgate.NEED_MB["browser"] >= browsersource.BROWSER_WORKERS * 250


#: What the fast stage was measured to HOLD, in MB — `held_peak_bytes` (anon + swapped pages)
#: at its worst across every fast stage the gate has sampled since the field shipped
#: (3.29 GB of the 5 GiB cgroup; the median is 3.16 GB). The figure an OOM is decided on, as
#: distinct from `peak_pct_of_limit`, which counts reclaimable page cache and read 96-100 % on
#: the same stages while `oom_kill` stayed 0.
MEASURED_FAST_HOLD_MB = 3367


def test_the_fast_budget_covers_what_the_stage_was_measured_to_hold():
    """The check whose absence let the budget be wrong for a week, silently.

    The worker-arithmetic test above pins `NEED_MB` against `FAST_WORKERS`, so it catches the
    two constants drifting apart — but both can be jointly too small, which is what happened:
    at the assumed ~250 MB per worker the floor was 1,000 MB, the budget was 2,048 MB, the
    relation held, and the stage was really holding **3.29 GB**. The gate then waited for 2 GB
    of headroom and admitted a selection needing 3.3 GB, which is a guard that reports safety
    it has not checked.

    So this asserts the budget against the MEASUREMENT rather than against the other constant.
    When a future gate records a higher hold, this is the line to raise — and raising it is a
    decision about `FAST_WORKERS` too, which is why the number lives here under its own name
    instead of inside the assertion.
    """
    assert memgate.NEED_MB["fast"] >= MEASURED_FAST_HOLD_MB, (
        f"memgate's fast budget is {memgate.NEED_MB['fast']} MB but the fast stage was measured "
        f"holding {MEASURED_FAST_HOLD_MB} MB (held_peak_bytes, the figure an OOM is decided on) "
        "— the gate would wait for less memory than the stage then takes")


def test_the_fast_budget_still_fits_inside_the_cgroup_it_waits_for():
    """A budget larger than the ceiling can never be satisfied: the gate would wait out
    `RSCHED_TEST_MEM_WAIT_S` and exit 3 on every run, on an idle box, forever. The engine
    container's cgroup is 5 GiB, so the budget must leave room for the daemon and the live runs
    that share it — the whole reason the cgroup half of this guard exists.
    """
    cgroup_mb = 5 * 1024
    assert memgate.NEED_MB["fast"] < cgroup_mb, (
        f"the fast budget ({memgate.NEED_MB['fast']} MB) must fit inside the engine cgroup "
        f"({cgroup_mb} MB) with room for the daemon beside it")


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
    """The regression itself, driven through the REAL hook with the REAL stub shape.

    `tests/test_ui_harness.py` pins the distribution logic by calling `pytest_configure` with a
    stub carrying only `option`. Asserting on `should_wait` alone would not have caught the
    defect — the defect was in the hook's call site — so this drives the hook and fails if the
    wait is entered at all, whatever the box's memory happens to be when the suite runs.
    """
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
