"""The fast suite's worker count follows the cgroup headroom there IS (D291, 2026-11-14)."""

from __future__ import annotations

import os
import tempfile
import types

import browsersource
import memgate

#: This container's cgroup ceiling in MB (`memory.max` = 5 GiB) — what every charge below has
#: to fit inside, daemon and live runs included.
CGROUP_MB = 5 * 1024

#: What the whole cgroup HOLDS with a test controller and nothing else, at its WORST: the 27
#: `tests` rows of memgate's own measured table (0.79-1.20 GB of `held_peak_bytes`).
IDLE_HOLD_MB = 1200

#: What one live routine run holds beside it, at its lightest, from D291's measurement
#: (1,086-1,717 MB). The lightest is the interesting end: it is the case that USED to fit
#: sometimes, which is what made a refusal look like a flake.
LIVE_RUN_MB = 1086


# ---- the charge: arithmetic over the measurements, not a constant ------------------------

def test_the_charge_covers_the_largest_hold_a_completed_stage_measured():
    """The pin that keeps the charge above REALITY: 2,970 MB held at 3 workers (stamp
    1795798044095941639, the green fast stage of 2026-11-27) is what a 3-worker charge has to
    cover, and a stage killed part-way cannot calibrate it because it never reached its peak.
    """
    assert memgate.CONTROLLER_HOLD_MB + 3 * memgate.PER_WORKER_HOLD_MB >= 2970, (
        "930 MB controller + 3 x 700 MB per worker = 3,030 MB, over the 2,970 MB measured")
    assert memgate.fast_need_mb(3) >= 2970, "the rounded charge must still cover the hold"
    assert memgate.fast_need_mb(4) <= 5 * 1024, (
        "a charge over the cgroup ceiling can never be satisfied: raising the per-worker "
        "figure must not price the full worker count out of its own box")


def test_the_charge_rises_with_the_count_and_is_rounded_up_to_256():
    """Rounded on the MB figure and never the GB one — 3.29 GB rounds to 3,328 MB, which is
    BELOW the hold it has to cover (the reason memgate's own comment gives)."""
    charges = [memgate.fast_need_mb(n) for n in range(2, browsersource.FAST_WORKERS + 1)]
    assert charges == sorted(charges), f"a bigger selection must never cost less: {charges}"
    assert all(mb % 256 == 0 for mb in charges), charges
    assert memgate.fast_need_mb(browsersource.FAST_WORKERS) < CGROUP_MB, (
        "a charge above the cgroup ceiling can never be satisfied: the gate would wait out "
        "RSCHED_TEST_MEM_WAIT_S and exit 3 on every run, on an idle box, forever")


def test_a_fast_selection_is_never_charged_less_than_the_serial_it_contains():
    """A fast run IS a serial run plus workers, so it cannot be priced below one."""
    assert memgate.fast_need_mb(2) >= memgate.need_mb("serial")
    assert memgate.fast_need_mb(0) >= memgate.need_mb("serial")


# ---- the count: the largest that FITS the headroom right now ------------------------------

def test_an_idle_box_still_runs_four_workers_and_still_pays_3584():
    """Point 4 of the decision: no release gate pays wall clock for a coexistence nobody needs."""
    idle = CGROUP_MB - IDLE_HOLD_MB
    assert browsersource.fast_workers(11, headroom=lambda: idle) == browsersource.FAST_WORKERS
    assert memgate.fast_need_mb(browsersource.FAST_WORKERS) <= idle


def test_a_live_run_buys_coexistence_instead_of_a_refusal():
    """The decision itself: the gate runs SMALLER beside a live run rather than being refused.
    The count it settles on follows the re-measured charge — at 700 MB a worker, 2,834 MB of
    headroom buys two, where the old 610 MB figure bought three and then held 2,970 MB.
    """
    headroom = CGROUP_MB - IDLE_HOLD_MB - LIVE_RUN_MB
    assert headroom == 2834
    assert memgate.fast_need_mb(4) > headroom, "the four-worker charge is what used to refuse"
    workers = browsersource.fast_workers(11, headroom=lambda: headroom)
    assert workers >= browsersource.MIN_FAST_WORKERS, "it runs smaller, it is never refused"
    assert memgate.fast_need_mb(workers) <= headroom, (
        "the count the ladder settles on must FIT the charge it will be held to — the whole "
        "point of deriving both from one figure")


def test_the_ladder_stays_between_two_and_the_ceiling():
    """Floor 2 and ceiling FAST_WORKERS, whatever the headroom or the box says."""
    assert browsersource.fast_workers(11, headroom=lambda: 0) == 2
    assert browsersource.fast_workers(11, headroom=lambda: 10 ** 6) == browsersource.FAST_WORKERS
    assert browsersource.fast_workers(1, headroom=lambda: 10 ** 6) == 2, "a 1-core box kept 2"
    assert browsersource.fast_workers(3, headroom=lambda: 10 ** 6) == 3, "cores still cap it"


def test_without_a_cgroup_ceiling_nothing_changes_and_nothing_waits():
    """Not Linux, cgroup v1, or `memory.max` reading `max`: `cgroup_headroom_mb` answers None
    and the ceiling is used, exactly as the module behaved before this change."""
    assert browsersource.fast_workers(11, headroom=lambda: None) == browsersource.FAST_WORKERS


def test_the_count_is_read_from_the_headroom_the_cgroup_reports(tmp_path):
    """The headroom is memgate's existing reader, not a second implementation: `memory.max`
    minus what the cgroup HOLDS (anon + swapped), never `memory.current`."""
    (tmp_path / "memory.max").write_text(f"{CGROUP_MB * 1024 * 1024}\n")
    (tmp_path / "memory.stat").write_text("anon 2080374784\nfile 4000000000\n")  # 1,984 MB anon
    (tmp_path / "memory.swap.current").write_text("1073741824\n")               # 1,024 MB swap
    assert memgate.cgroup_headroom_mb(tmp_path) == 2112
    assert browsersource.fast_workers(
        11, headroom=lambda: memgate.cgroup_headroom_mb(tmp_path)) == 2


# ---- the wiring: without it every function above is inert --------------------------------

def test_the_xdist_auto_hook_hands_the_headroom_aware_count_to_pytest(monkeypatch):
    """`-n auto` must resolve to what fits, which is the only way this reaches a real gate."""
    import conftest as root_conftest

    monkeypatch.setattr("os.cpu_count", lambda: 12)
    monkeypatch.setattr(memgate, "cgroup_headroom_mb",
                        lambda *_a, **_k: CGROUP_MB - IDLE_HOLD_MB - LIVE_RUN_MB)
    # The host side is pinned too: the count follows the SMALLER of the two readings, so a live
    # box's own MemAvailable would otherwise decide this assertion (F898).
    monkeypatch.setattr(memgate, "available_mb", lambda *_a, **_k: 10 ** 6)
    config = types.SimpleNamespace(option=types.SimpleNamespace(markexpr="not ui"))
    # The hook delegates to `browsersource.auto_workers`, which owns the cores/memory/browser
    # reasoning: conftest.py is over the house size bar and may only shrink, so the decision
    # moved to the module that already held both worker counts. Pinned through the HOOK anyway —
    # that is the seam pytest calls, and a decision nothing calls is inert.
    headroom = CGROUP_MB - IDLE_HOLD_MB - LIVE_RUN_MB
    resolved = root_conftest.pytest_xdist_auto_num_workers(config)
    assert resolved == browsersource.fast_workers(12, headroom=lambda: headroom), (
        "the hook must hand pytest the count the ladder derives from the live charge")
    assert memgate.fast_need_mb(resolved) <= headroom
    assert root_conftest.pytest_xdist_auto_num_workers(
        types.SimpleNamespace(option=types.SimpleNamespace(markexpr=""))) == 4, (
        "the browser selection is priced by its Chromiums in another container's cgroup, so "
        "this cgroup's headroom (which allowed the fast suite only 3) cannot cut BROWSER_WORKERS")


def test_the_memory_wait_is_charged_the_count_xdist_actually_resolved(monkeypatch):
    """The second half of the wiring: the charge must follow the count, not the ceiling."""
    import conftest as root_conftest

    charged: list[tuple[str, int]] = []
    # `wait_for_config` is the seam conftest calls (the reasoning lives in memgate, whose
    # subject the charge is, because conftest.py is over the house size bar and may only
    # shrink). It reads the resolved count off the config and hands it to `wait_for_memory`,
    # which is what this patches — so the pin still measures what is CHARGED, not what is read.
    monkeypatch.setattr(memgate, "wait_for_memory",
                        lambda selection, workers=0, **_k: charged.append((selection, workers)))
    config = types.SimpleNamespace(
        option=types.SimpleNamespace(markexpr="not ui", dist="worksteal", numprocesses=3),
        pluginmanager=object())
    root_conftest.pytest_configure(config)
    assert charged == [("fast", 3)]


def test_the_message_says_which_count_it_settled_on(monkeypatch, capsys):
    """Point 6, unchanged in substance: the message names the selection, the count, the headroom
    it measured and which of the two limits decided. "the fast suite at 4 xdist workers" is a lie
    once the count is conditional, and a message nobody can read is what sent R3376's author
    hunting a leak.
    """
    monkeypatch.delenv("RSCHED_TEST_MEM_WAIT", raising=False)
    monkeypatch.setenv("RSCHED_TEST_MEM_WAIT_S", "0")
    bound = memgate.wait_for_memory("fast", 3, read=lambda: 9000, read_cgroup=lambda: 500,
                                    sleep=lambda _s: None, clock=lambda: 1,
                                    read_hold=lambda: 0)
    assert bound == 500, "bounded at what the short side really had"
    message = capsys.readouterr().err
    assert "at 3 xdist workers" in message, message
    assert "this container's cgroup still has only 500 MB" in message, message
    assert f"wants {memgate.need_mb('fast', 3)} MB" in message, message
    assert memgate.prose_for("fast", 4) == "the fast suite at 4 xdist workers"


# ---- the host side binds too (F898, 2026-11-15) ------------------------------------------

def test_the_count_reduces_against_the_host_when_the_host_is_the_short_side():
    """D291 reduced against the cgroup alone while the wait guarded host memory as well."""
    short = {"read": lambda: 2900, "read_cgroup": lambda: CGROUP_MB - IDLE_HOLD_MB}
    assert memgate.headroom_mb(**short) == 2900
    workers = browsersource.fast_workers(11, headroom=lambda: memgate.headroom_mb(**short))
    assert memgate.fast_need_mb(workers) <= 2900, (
        "the count is cut against the HOST reading (2,900) even though the cgroup reports more")
    assert memgate.fast_need_mb(workers + 1) > 2900, "and it is the LARGEST count that fits"


def test_the_headroom_is_the_smaller_of_the_two_readings_and_none_only_without_both():
    """One reading for the count and for the wait, so the two cannot disagree — the reason
    `need_mb` gives for deriving both charges in one place."""
    assert memgate.headroom_mb(read=lambda: 4000, read_cgroup=lambda: 1500) == 1500
    assert memgate.headroom_mb(read=lambda: 1200, read_cgroup=lambda: 4000) == 1200
    assert memgate.headroom_mb(read=lambda: None, read_cgroup=lambda: 2000) == 2000
    assert memgate.headroom_mb(read=lambda: 2000, read_cgroup=lambda: None) == 2000
    assert memgate.headroom_mb(read=lambda: None, read_cgroup=lambda: None) is None


# ---- whose memory was it? (F969, 2026-11-28) ----------------------------------------------

def test_the_kill_says_a_co_tenant_held_it_when_the_session_is_inside_its_cap():
    """Measured 2026-11-27: a micro-VM probe in a sibling process ended two release gates at
    101-105 % of the cgroup while the same stage held 58 % alone. The watchdog reads the WHOLE
    cgroup, so naming only the selection sent the gate's owner to re-measure a constant that
    was not the cause — the message has to separate the two readings.
    """
    said: list[str] = []
    dog = memgate.Watchdog(cap=3328, pid=4242, read=lambda _p: 5186.0,
                           read_session=lambda _p: 2970.0, kill=lambda *_a: None,
                           warn=said.append)
    assert dog.sample() == 5186.0
    message = "".join(said)
    assert "this CGROUP holds 5186 MB" in message, message
    assert "holds only 2970 MB, INSIDE that cap" in message, message
    assert "other processes in the same cgroup" in message, message
    assert "narrower selection will not help" in message, message


def test_the_kill_still_blames_the_suite_when_the_suite_really_is_the_holder():
    """The other branch, or the first one would excuse a genuine overrun: when the session's own
    tree is over the cap, a narrower selection IS the answer and the message must say so.
    """
    said: list[str] = []
    memgate.Watchdog(cap=3328, pid=4242, read=lambda _p: 4000.0,
                     read_session=lambda _p: 3900.0, kill=lambda *_a: None,
                     warn=said.append).sample()
    message = "".join(said)
    assert "holds 3900 MB, over the cap by itself" in message, message
    assert "re-measure PER_WORKER_HOLD_MB" in message, message
    assert "INSIDE that cap" not in message, message


def test_an_unreadable_proc_omits_the_split_rather_than_guessing_one(tmp_path):
    """`session_hold_mb` answers None where /proc cannot be walked, and the kill then asks the
    reader to check the cgroup's other tenants instead of asserting a figure it does not have.
    """
    assert memgate.session_hold_mb(1, proc=tmp_path / "missing") is None
    said: list[str] = []
    memgate.Watchdog(cap=100, pid=4242, read=lambda _p: 200.0,
                     read_session=lambda _p: None, kill=lambda *_a: None,
                     warn=said.append).sample()
    assert "check what else shares this cgroup" in "".join(said)


def test_the_session_reading_sums_the_controller_and_its_workers():
    """It must be the process TREE: an xdist worker's RSS is the session's, and a sibling's is
    not. Built from a fake /proc so the sum is checkable rather than merely plausible.
    """
    import pathlib
    proc = pathlib.Path(tempfile.mkdtemp())
    page = os.sysconf("SC_PAGE_SIZE")
    for pid, ppid, mb in ((100, 1, 500), (101, 100, 300), (102, 101, 200), (900, 1, 4000)):
        d = proc / str(pid)
        d.mkdir()
        (d / "stat").write_text(f"{pid} (python) S {ppid} 0 0\n")
        (d / "statm").write_text(f"0 {mb * 1024 * 1024 // page} 0 0 0 0 0\n")
    held = memgate.session_hold_mb(100, proc=proc)
    assert held is not None and round(held) == 1000, (
        f"500 + 300 + 200 (a grandchild counts) and NOT the 4,000 MB sibling: {held}")
