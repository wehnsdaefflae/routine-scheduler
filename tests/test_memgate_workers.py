"""The fast suite's worker count follows the cgroup headroom there IS (D291, 2026-11-14).

The operator, asked which side gives when a release gate's FAST stage (3,584 MB of this
container's 5,120 MB cgroup) and a live routine run (1,086-1,717 MB) cannot both fit:
*"Reduce FAST_WORKERS while a routine run is active, trading gate wall-clock for coexistence"*
— explicitly NOT the cgroup raise.

Until now both halves were constants — `browsersource.FAST_WORKERS = 4` and
`memgate.NEED_MB["fast"] = 3584` — so a live run holding the difference meant the gate was
REFUSED (exit 3, after up to `RSCHED_TEST_MEM_WAIT_S` of waiting) instead of being run smaller.
And because a gate and a run fit SOMETIMES, that refusal read as a flaky failure rather than as
a rule.

What is pinned here: the charge re-derives the old constant at four workers, an idle box is
untouched (4 workers, 3,584 MB), a box with a live run coexists at a lower count instead of
being refused, and the refusal says what it settled on.
"""

from __future__ import annotations

import types

import pytest

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

def test_four_workers_still_cost_the_3584_mb_the_constant_held():
    """The pin that stops the new arithmetic disagreeing with the old measurement.

    `NEED_MB["fast"]` was 3584 — the worst measured whole-stage hold (3,367 MB) rounded up to
    the next 256 MB. The charge is now derived from that same table: a worst-case controller
    term plus a worst-case per-worker term. At FOUR workers it must land on exactly 3,584, or
    the derivation has quietly replaced the figure the guard was measured into.
    """
    assert memgate.CONTROLLER_HOLD_MB + 4 * memgate.PER_WORKER_HOLD_MB == 3370, (
        "930 MB controller + 4 x 610 MB per worker = 3,370 MB, which rounds up to 3,584")
    assert memgate.fast_need_mb(4) == 3584
    assert memgate.need_mb("fast", 4) == 3584


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
    """A fast run IS a serial run plus workers, so it cannot be priced below one.

    The two measured samples disagree about the controller alone: the 7 four-worker `fast` rows
    imply ~930 MB for it, while the 21 serial `-n 0` rows were measured holding up to 2,700 MB.
    The charge takes the larger — a budget below a measured hold is a guard reporting safety it
    never checked (the lesson `NEED_MB`'s comment records from the 2,048 MB week).
    """
    assert memgate.fast_need_mb(2) >= memgate.need_mb("serial")
    assert memgate.fast_need_mb(0) >= memgate.need_mb("serial")


# ---- the count: the largest that FITS the headroom right now ------------------------------

def test_an_idle_box_still_runs_four_workers_and_still_pays_3584():
    """Point 4 of the decision: no release gate pays wall clock for a coexistence nobody needs.

    An idle box at its WORST measured hold (1,200 MB) leaves 3,920 MB — above the 3,584 the
    four-worker selection costs — so nothing about an idle gate changes.
    """
    idle = CGROUP_MB - IDLE_HOLD_MB
    assert browsersource.fast_workers(11, headroom=lambda: idle) == browsersource.FAST_WORKERS
    assert memgate.fast_need_mb(browsersource.FAST_WORKERS) <= idle


def test_a_live_run_buys_coexistence_instead_of_a_refusal():
    """The decision itself: the gate runs SMALLER beside a live run rather than being refused.

    An idle hold plus the lightest live run leaves 2,834 MB. The old constant (3,584) did not
    fit in that, so the session waited twenty minutes and exited 3; three workers cost 2,816 and
    do fit, so the stage runs — slower, beside the run, which is the trade he chose.
    """
    headroom = CGROUP_MB - IDLE_HOLD_MB - LIVE_RUN_MB
    assert headroom == 2834
    assert memgate.fast_need_mb(4) > headroom, "the four-worker charge is what used to refuse"
    workers = browsersource.fast_workers(11, headroom=lambda: headroom)
    assert workers == 3
    assert memgate.fast_need_mb(workers) <= headroom


def test_the_ladder_stays_between_two_and_the_ceiling():
    """Floor 2 and ceiling FAST_WORKERS, whatever the headroom or the box says.

    Below the two-worker charge it returns the floor rather than 1 or 0: `-n auto` has always
    guaranteed at least two (`max(2, …)`), a one-worker xdist run is a serial run paying xdist's
    overhead, and the memory wait is what handles a box with no room for even that — it waits,
    which is the behaviour a refusal is supposed to have.
    """
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
    config = types.SimpleNamespace(option=types.SimpleNamespace(markexpr="not ui"))
    # The hook delegates to `browsersource.auto_workers`, which owns the cores/memory/browser
    # reasoning: conftest.py is over the house size bar and may only shrink, so the decision
    # moved to the module that already held both worker counts. Pinned through the HOOK anyway —
    # that is the seam pytest calls, and a decision nothing calls is inert.
    assert root_conftest.pytest_xdist_auto_num_workers(config) == 3
    assert root_conftest.pytest_xdist_auto_num_workers(
        types.SimpleNamespace(option=types.SimpleNamespace(markexpr=""))) == 2, (
        "the browser selection is priced by its Chromiums in another container's cgroup, and "
        "BROWSER_WORKERS (2) is this floor, so it cannot be moved by this cgroup's headroom")


def test_the_memory_wait_is_charged_the_count_xdist_actually_resolved(monkeypatch):
    """The second half of the wiring: the charge must follow the count, not the ceiling.

    By the time `pytest_configure` runs, `-n auto` is already the resolved integer (measured on
    this box, pytest 9.1.1 / xdist 3.8.0 — the same reading `selection_for` prices by), so the
    count the hook settled on is what the wait is charged for.
    """
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


def test_the_refusal_says_which_count_it_settled_on(monkeypatch):
    """Point 6: the message names the selection, the count, the headroom it measured and which
    of the two limits decided. "the fast suite at 4 xdist workers" is a lie once the count is
    conditional, and a refusal nobody can read is what sent R3376's author hunting a leak."""
    monkeypatch.delenv("RSCHED_TEST_MEM_WAIT", raising=False)
    monkeypatch.setenv("RSCHED_TEST_MEM_WAIT_S", "0")
    with pytest.raises(pytest.exit.Exception) as exc:
        memgate.wait_for_memory("fast", 3, read=lambda: 9000, read_cgroup=lambda: 500,
                                sleep=lambda _s: None, clock=lambda: 1)
    message = str(exc.value)
    assert "at 3 xdist workers" in message, message
    assert "this container's cgroup: 500 MB available" in message, message
    assert "needs 2816 MB" in message, message
    assert memgate.prose_for("fast", 4) == "the fast suite at 4 xdist workers"
