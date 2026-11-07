"""A test session waits for the HOST to have the memory it needs (tests/memgate.py, 2026-11-05)."""

from __future__ import annotations

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
    memgate.wait_for_memory("browser", read=read, sleep=sleep, clock=clock)
    assert clock() == 0


def test_it_waits_until_memory_frees(monkeypatch):
    monkeypatch.delenv("RSCHED_TEST_MEM_WAIT", raising=False)
    read, sleep, clock = _fake([1000, 1200, 4000])
    memgate.wait_for_memory("browser", read=read, sleep=sleep, clock=clock)
    assert clock() == 30, "two waits of 15 s, then it started"


def test_it_refuses_to_start_past_the_deadline(monkeypatch):
    monkeypatch.delenv("RSCHED_TEST_MEM_WAIT", raising=False)
    monkeypatch.setenv("RSCHED_TEST_MEM_WAIT_S", "30")
    read, sleep, clock = _fake([500] * 10)
    with pytest.raises(pytest.exit.Exception) as exc:
        memgate.wait_for_memory("fast", read=read, sleep=sleep, clock=clock)
    assert "500 MB available" in str(exc.value) and "2048 MB" in str(exc.value)


def test_an_unreadable_meminfo_waits_for_nothing(tmp_path):
    assert memgate.available_mb(tmp_path / "missing") is None
    (tmp_path / "meminfo").write_text("MemTotal: 100 kB\nMemAvailable:  4194304 kB\n")
    assert memgate.available_mb(tmp_path / "meminfo") == 4096


def test_the_switch_turns_it_off(monkeypatch):
    monkeypatch.setenv("RSCHED_TEST_MEM_WAIT", "0")
    memgate.wait_for_memory("browser", read=lambda: 1, sleep=lambda _s: None, clock=lambda: 0)


def test_the_fast_budget_still_covers_the_worker_count_it_claims_to():
    """The two halves must move together — nothing has ever checked that they do.

    `memgate`'s own docstring says its needs ARE "the measured peaks of the selections: the fast
    suite at FAST_WORKERS workers (~250 MB each, plus the controller)". That makes `NEED_MB`
    arithmetic over a constant in another module, so raising `FAST_WORKERS` without raising
    `NEED_MB` leaves the guard describing a selection that no longer exists — silently, because
    a too-small budget does not fail, it merely stops waiting when it should have waited.

    NOT a past violation, stated plainly because the tempting story is wrong: at six workers the
    floor was 1,500 MB against a 2048 MB budget, so the relation held throughout. What the
    2026-11-07 cgroup measurement DID show is that the ~250 MB figure understates the real cost
    about fourfold — the fast stage peaked at 3.90 GiB on four workers, and at 96-100 % of the
    5 GiB cgroup on six (F849). So this floor is deliberately a weak lower bound: it catches the
    arithmetic going backwards, while the cgroup headroom `FAST_WORKERS` is really chosen for is
    measured per gate in the gate's own evidence.

    Pins the RELATION, never the numbers — either may be re-tuned, but not alone.
    """
    import browsersource

    workers = browsersource.FAST_WORKERS
    floor = workers * 250                      # the per-worker cost memgate's docstring states
    assert memgate.NEED_MB["fast"] >= floor, (
        f"memgate's fast budget is {memgate.NEED_MB['fast']} MB but FAST_WORKERS={workers} at "
        f"~250 MB each needs at least {floor} MB — raise NEED_MB['fast'] or lower FAST_WORKERS")
    assert memgate.NEED_MB["browser"] >= browsersource.BROWSER_WORKERS * 250
