"""D295: the session's hold is BOUNDED rather than predicted, and priced from its own history."""

from __future__ import annotations

import json
import signal

import memgate
import memhistory
import memwatch


def _watchdog(holds, cap=1000, **kw):
    """A watchdog over a scripted sequence of readings, recording what it would have killed."""
    readings = list(holds)
    killed: list[tuple[int, int]] = []
    dog = memwatch.Watchdog(cap=cap, pid=4242,
                            read=lambda _pid: readings.pop(0) if readings else 0.0,
                            kill=lambda pid, sig: killed.append((pid, sig)),
                            warn=lambda _line: None, **kw)
    return dog, killed


def test_a_session_inside_its_cap_is_never_touched():
    dog, killed = _watchdog([100, 400, 999], cap=1000)
    for _ in range(3):
        dog.sample()
    assert not killed
    assert dog.peak_mb == 999 and dog.killed_at_mb is None


def test_a_session_over_its_cap_is_ended_with_an_interrupt():
    """The whole point of the watchdog: an overrun kills the TEST RUN, so the kernel never gets
    to choose a live routine run as its OOM victim instead (2026-11-05, grant-hunter).
    """
    dog, killed = _watchdog([500, 1500], cap=1000)
    dog.sample()
    assert not killed, "the first reading is inside the cap"
    dog.sample()
    assert killed == [(4242, signal.SIGINT)], "SIGINT, so pytest stops its workers and reports"
    assert dog.killed_at_mb == 1500


def test_an_overrun_is_reported_once_however_long_it_lasts():
    """A session held over its cap for a minute must not be sent a signal every 0.25 s — the
    first one ends it, and the rest would only be noise in the log a reader has to diagnose.
    """
    dog, killed = _watchdog([1500, 1600, 1700], cap=1000)
    for _ in range(3):
        dog.sample()
    assert len(killed) == 1 and dog.killed_at_mb == 1500


def test_the_hold_is_read_in_the_same_unit_the_charges_were_measured_in(tmp_path):
    """The unit that cost gate 1795100129134213629-release its fast stage."""
    (tmp_path / "memory.stat").write_text("anon 2097152000\nfile 4000000000\n")
    (tmp_path / "memory.swap.current").write_text("1048576000\n")
    assert memwatch.cgroup_hold_mb(0, tmp_path) == 3000.0, (
        "anon + swap, and NOT the page cache in `file` that the kernel drops at the limit")
    assert memwatch.cgroup_hold_mb(0, tmp_path / "missing") == 0.0, "no cgroup, no bound"


def test_a_reading_of_zero_can_never_end_a_session():
    """Where there is no cgroup to read, the watchdog must be inert rather than trigger-happy:
    `cgroup_hold_mb` answers 0.0, which is below every cap by construction.
    """
    dog, killed = _watchdog([0.0, 0.0], cap=1024)
    dog.sample()
    dog.sample()
    assert not killed and dog.peak_mb == 0.0


def test_the_cap_is_what_the_session_was_admitted_for_plus_slack():
    assert memwatch.cap_mb(2816, slack_mb=512, floor_mb=1024) == 3328
    assert memwatch.cap_mb(200, slack_mb=512, floor_mb=1024) == 1024, "never under the floor"


def test_history_may_lower_admission_but_never_the_bound():
    """The specimen that cost gate 1795100129134213629-release its fast stage, exactly."""
    assert memwatch.cap_mb(128, class_mb=0) == 1024, "the defect: the floor was the whole bound"
    bound = memwatch.cap_mb(128, class_mb=memgate.NEED_MB["fast"])
    assert bound == memgate.NEED_MB["fast"] + memwatch.SLACK_MB
    assert bound > 1056, "the 1,056 MB stage that was killed now survives"


def test_the_cap_does_not_follow_the_memory_that_happens_to_be_free():
    """Caught before it shipped, 2026-11-19: this box had 2,802 MB available while the fast
    stage is measured holding 3.08-3.29 GB, so a headroom-derived cap would have killed a
    legitimate release gate for running at its ordinary cost. The cap is the admission figure.
    """
    fast_need = memgate.need_mb("fast", 4)
    assert fast_need >= 3328, fast_need
    assert memwatch.cap_mb(fast_need) > 3300, (
        "a fast stage's cap must cover the 3.29 GB it was measured holding, whatever is free")


def test_no_admission_figure_at_all_gets_no_cap_and_no_watchdog():
    """"No ceiling to read" has always meant "nothing waits" here, and it must mean "nothing is
    killed" too: a cap derived from nothing would bound every session by a number nobody
    measured.
    """
    assert memwatch.cap_mb(None) is None
    assert memwatch.arm(cap=None, selection="serial", key="k",
                        evidence_dir=memhistory.EVIDENCE_DIR) is None


def test_a_selection_is_identified_by_its_paths_marks_and_worker_count():
    """The key is what a hold belongs to, so everything that changes the hold is in it — and
    nothing that does not, including the order the paths were typed in.
    """
    one = memhistory.selection_key(["tests/test_a.py", "tests/test_b.py"], "not ui", 0)
    assert one == memhistory.selection_key(["tests/test_b.py", "tests/test_a.py"], "not ui", 0)
    assert one != memhistory.selection_key(["tests/test_a.py"], "not ui", 0)
    assert one != memhistory.selection_key(["tests/test_a.py", "tests/test_b.py"], "not ui", 4)
    assert one != memhistory.selection_key(["tests/test_a.py", "tests/test_b.py"], "", 0)


def test_a_selection_with_no_history_is_charged_its_class_by_name():
    """The fallback has to SAY it is the fallback: a refusal nobody can read is what sent
    R3376's author hunting a memory leak in a candidate that had none.
    """
    need, source = memhistory.priced_need_mb("unseen", history={}, fallback_mb=2816)
    assert need == 2816
    assert "class constant" in source and "not run before" in source


def test_a_selection_that_has_run_is_charged_its_own_worst_hold_plus_a_margin():
    """The defect D295 closes: a class-wide 2,816 MB charged a selection measured at 900 MB,
    which is how three gates were refused with zero tests while the suite ran green at 1.2 GB.
    """
    need, source = memhistory.priced_need_mb("seen", history={"seen": 900}, fallback_mb=2816)
    assert need == 1152, "900 MB + 25 %, rounded up to the next 64 MB"
    assert need < 2816 and "own worst hold (900 MB)" in source


def test_the_per_selection_price_never_exceeds_the_class_worst_case():
    """A selection whose own history is worse than its class's figure is charged the class
    figure: pricing above it would re-introduce the over-charge this replaces.
    """
    need, _source = memhistory.priced_need_mb("heavy", history={"heavy": 4393}, fallback_mb=2816)
    assert need == 2816


def test_the_history_keeps_the_worst_hold_per_selection(tmp_path):
    for held in (400, 1200, 900):
        memhistory.record_hold(tmp_path, key="k", held_mb=held, selection="serial", seconds=1.0)
    memhistory.record_hold(tmp_path, key="other", held_mb=3000, selection="fast", seconds=2.0)
    assert memhistory.holds_by_key(tmp_path) == {"k": 1200, "other": 3000}


def test_a_half_written_history_line_is_skipped_not_raised_on(tmp_path):
    """This file is appended to by sessions that can be KILLED mid-write, so a truncated last
    line is the expected state, not a corruption to report.
    """
    path = memhistory.history_path(tmp_path)
    path.write_text(json.dumps({"key": "k", "held_mb": 700}) + '\n{"key": "trunc"',
                    encoding="utf-8")
    assert memhistory.holds_by_key(tmp_path) == {"k": 700}


def test_no_history_at_all_reads_as_no_history(tmp_path):
    assert memhistory.holds_by_key(tmp_path / "nothing-here") == {}


def test_finishing_records_what_the_session_held(tmp_path):
    dog, _killed = _watchdog([250, 800], cap=1000)
    dog.selection, dog.key, dog.evidence_dir = "serial", "sk", tmp_path
    memwatch._ARMED[:] = [dog]
    dog.sample()
    dog.sample()
    assert memwatch.finish() == 800
    assert memhistory.holds_by_key(tmp_path) == {"sk": 800}
    assert memwatch.finish() is None, "nothing is armed any more"


def test_an_overrun_is_recorded_too(tmp_path):
    """The selection that overran its cap is exactly the one whose next admission must be
    priced from the attempt — its peak is the best lower bound on its real cost that exists.
    """
    dog, _killed = _watchdog([2000], cap=1000)
    dog.selection, dog.key, dog.evidence_dir = "serial", "over", tmp_path
    memwatch._ARMED[:] = [dog]
    dog.sample()
    assert memwatch.finish() == 2000
    assert memhistory.holds_by_key(tmp_path) == {"over": 2000}


def test_the_gate_prices_a_known_selection_from_the_history_it_recorded(tmp_path):
    """The two halves together, through `memgate`'s own entry point: what the watchdog recorded
    is what the next session of that selection is charged.
    """
    key = memhistory.selection_key(["tests/test_memwatch.py"], "not ui", 0)
    memhistory.record_hold(tmp_path, key=key, held_mb=640, selection="serial", seconds=3.0)
    need, source = memhistory.priced_for(key, memgate.need_mb("serial"), evidence_dir=tmp_path)
    assert need == 832 and need < memgate.NEED_MB["serial"]
    assert "own worst hold (640 MB)" in source


def test_the_wait_still_charges_the_class_when_no_key_is_given(monkeypatch):
    """Every existing caller — including the pins that fix the class constants — passes no key
    and must be unaffected, so the two mechanisms cannot interfere.
    """
    seen: list[int] = []
    monkeypatch.setattr(memgate, "available_mb", lambda *a, **k: 9000)
    memgate.wait_for_memory("serial", 0, read=lambda: 9000, read_cgroup=lambda: 9000,
                            sleep=lambda _s: seen.append(1), clock=lambda: 0.0)
    assert not seen, "9 GB is plenty for the 2,816 MB class charge, so nothing waited"
