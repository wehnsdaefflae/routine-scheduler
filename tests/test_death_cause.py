"""WHY a run's process is gone — the one classifier both the daemon and the engine read."""

from __future__ import annotations

from rsched.daemon import deathcause


def test_classify_cause_separates_the_deaths_that_need_different_investigations():
    """F480: rc was read and then thrown away, so every death read alike. Each of these is a
    different next step — an OOM is a memory problem, a clean exit without a finish is an
    engine defect, and a user abort is nothing at all."""
    assert deathcause.classify_cause(-9) == "oom_kill"
    assert deathcause.classify_cause(-15) == "signal_kill"
    assert deathcause.classify_cause(1) == "engine_crash"
    assert deathcause.classify_cause(0) == "no_finish"
    assert deathcause.classify_cause(None) == "unknown"
    # a user abort is a user abort whatever signal carried it out
    assert deathcause.classify_cause(-9, user_cancel=True) == "user_abort"


def test_a_sigkill_at_sixty_megabytes_is_not_diagnosed_as_an_oom():
    """F569: `oom_kill` is a DIAGNOSIS, and rc=-9 alone does not support it."""
    assert deathcause.classify_cause(-9, vm_hwm_kb=61716) == "signal_kill"
    assert deathcause.classify_cause(-9, vm_hwm_kb=64492) == "signal_kill"
    # a genuine OOM signature survives: a peak in the gigabytes keeps the memory verdict
    assert deathcause.classify_cause(-9, vm_hwm_kb=7_500_000) == "oom_kill"
    # no sample at all is not evidence against an OOM — the old verdict stands, unqualified
    assert deathcause.classify_cause(-9) == "oom_kill"
    assert deathcause.classify_cause(-9, vm_hwm_kb=None) == "oom_kill"
    # and a user abort still outranks every memory reading
    assert deathcause.classify_cause(-9, vm_hwm_kb=61716, user_cancel=True) == "user_abort"


def test_the_host_ram_read_names_the_total_and_fails_open(tmp_path, monkeypatch):
    """The one reading every memory verdict rests on, and nothing covered it: every other test
    replaces `_host_ram_kb` wholesale, so the parse itself — WHICH field of /proc/meminfo, and
    what happens when it cannot be read — was asserted nowhere. It must name MemTotal (a
    ceiling, which is what a peak is compared against) and never MemAvailable (what is free
    right now, which would call a modest peak an OOM on a loaded box), and an unreadable file
    must return None rather than raise, because None is not evidence and the caller keeps the
    verdict it had.
    """
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemAvailable:    1024 kB\nMemTotal:     8192000 kB\n"
                       "SwapTotal:    4096 kB\n", encoding="utf-8")
    monkeypatch.setattr(deathcause, "Path", lambda _p: meminfo)
    assert deathcause._host_ram_kb() == 8192000

    monkeypatch.setattr(deathcause, "Path", lambda _p: tmp_path / "absent")
    assert deathcause._host_ram_kb() is None

    garbled = tmp_path / "garbled"
    garbled.write_text("MemTotal:\n", encoding="utf-8")
    monkeypatch.setattr(deathcause, "Path", lambda _p: garbled)
    assert deathcause._host_ram_kb() is None
