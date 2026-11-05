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
