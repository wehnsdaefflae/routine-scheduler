"""F586 / D160-C, step B1: the ENGINE SEAM for cancelling the one call that is running.

The operator, 2026-09-28: *"i want the option to cancel a util run. just the once currently
running. via a red x or sth on the message."* D160 settled on C (2026-09-30): a DISTINCT
`cancelled` outcome — its own exit code and wording — **plus** a transcript event, so an audit
of the run afterwards sees the human intervention instead of inferring it from a gap.

What these tests pin is the half the decision turns on: **what the run is TOLD.** A run that
reads "timed out" sensibly retries, and retrying the call a person just stopped is the whole
behaviour the feature exists to prevent. So a cancel is not a deadline, not an abort, and says
so in its exit code, in its flag and in its note.

The seam itself is deliberately cheap: `_wait` already polls the run's `aborted` callback every
`ABORT_POLL_S`, so the cancel rides the same look. No second wait loop in the one function every
util, script and shell action goes through, and no second writer under `runs/`.
"""

import subprocess
import sys
import time

import pytest

from rsched import utils_run
from rsched.captured_output import CapturedOutput
from rsched.utils_run import ABORT_EXIT, CANCEL_EXIT, TIMEOUT_EXIT, Jailed, run_jailed


def _sleeper(seconds: float = 30) -> list[str]:
    """A child that outlives the test unless something ends its group."""
    return [sys.executable, "-c", f"import time; time.sleep({seconds})"]


def _flag(after: float = 0.0):
    """A callback that answers True once `after` seconds have passed."""
    start = time.monotonic()
    return lambda: time.monotonic() - start >= after


# -- the outcome mapping: four endings, four codes, one place ----------------------------


def test_the_four_endings_each_report_their_own_code():
    """One mapping, so the three callable kinds cannot report an ended call four ways."""
    out = CapturedOutput("")
    assert Jailed(0, out, out, False).exit_code == 0
    assert Jailed(7, out, out, False).exit_code == 7          # the command's own code
    assert Jailed(0, out, out, True).exit_code == TIMEOUT_EXIT
    assert Jailed(0, out, out, False, aborted=True).exit_code == ABORT_EXIT
    assert Jailed(0, out, out, False, cancelled=True).exit_code == CANCEL_EXIT


def test_the_cancel_code_is_positive_and_its_own():
    """POSITIVE because a negative return code means "killed by signal N" on POSIX and
    `executor._note_if_killed` reads one to file a `util_killed` health event — the event whose
    job is catching the cgroup OOM killer. A deliberate human act must never land there.

    And DISTINCT from the deadline's 124 and the abort's 130, because that distinction IS the
    decision: `obsState()` in the console styles a timeout dashed ("nothing is known about the
    work"), which is the wrong thing to say about a call somebody stopped on purpose.
    """
    assert CANCEL_EXIT > 0
    assert CANCEL_EXIT not in (0, TIMEOUT_EXIT, ABORT_EXIT)


def test_a_cancel_outranks_a_deadline_and_an_abort_at_the_same_look():
    """All three can be true in one 0.25 s poll. The ending the run must LEARN about is the
    most deliberate one: on an abort the run is stopping anyway, but only a cancel carries the
    instruction "this call, and not the clock".
    """
    out = CapturedOutput("")
    both = Jailed(0, out, out, True, aborted=True, cancelled=True)
    assert both.exit_code == CANCEL_EXIT
    assert Jailed(0, out, out, True, aborted=True).exit_code == ABORT_EXIT


# -- the seam: a running call is actually stopped ----------------------------------------


def test_a_cancel_mid_flight_ends_the_call_and_says_who_did_it(tmp_path):
    """The point of the feature: a wedged call does not cost the whole run. The group is ended
    the same way a deadline ends it (`procgroup.terminate`), so a `uv run` grandchild and a
    backgrounded shell child die with it.
    """
    started = time.monotonic()
    res = run_jailed(_sleeper(), env={}, cwd=tmp_path, timeout=30, label="util 'wedged'",
                     cancelled=_flag(after=0.5))
    assert res.cancelled is True
    assert res.timed_out is False and res.aborted is False
    assert res.exit_code == CANCEL_EXIT
    assert time.monotonic() - started < 25           # it did NOT sit out its 30 s deadline
    note = res.stderr
    assert "cancelled by the user" in note
    assert "util 'wedged'" in note
    # The note is what the RUN reads, so it must discourage the retry a timeout would invite.
    assert "do not simply run it again" in note


def test_a_call_that_finishes_first_is_not_cancelled(tmp_path):
    """A cancel that arrives after the command exited is a non-event: the outcome is the
    command's own. Otherwise a click landing a quarter-second late would rewrite a result that
    already exists.
    """
    res = run_jailed([sys.executable, "-c", "print('done')"], env={}, cwd=tmp_path,
                     timeout=30, label="util 'quick'", cancelled=lambda: True)
    assert res.cancelled is False
    assert res.exit_code == 0
    assert "done" in res.stdout
    assert "cancelled" not in res.stderr


def test_a_cancel_that_never_fires_leaves_the_deadline_in_charge(tmp_path):
    """The cancel must not shorten the clock of a call nobody cancelled — the regression that
    would make every long util look like a human intervention.
    """
    res = run_jailed(_sleeper(), env={}, cwd=tmp_path, timeout=1, label="util 'slow'",
                     cancelled=lambda: False)
    assert res.timed_out is True and res.cancelled is False
    assert res.exit_code == TIMEOUT_EXIT
    assert "timed out after 1s" in res.stderr


def test_what_was_printed_before_the_cancel_is_kept(tmp_path):
    """The same guarantee a deadline already carries: a call that logged why it wedged must not
    lose exactly the material that explains it. Cancelling to READ that output is a main reason
    a person clicks the ×.
    """
    cmd = [sys.executable, "-u", "-c",
           "print('phase one complete', flush=True); import time; time.sleep(30)"]
    res = run_jailed(cmd, env={}, cwd=tmp_path, timeout=30, label="util 'chatty'",
                     cancelled=_flag(after=0.7))
    assert res.cancelled is True
    assert "phase one complete" in res.stdout


def test_an_abort_still_outranks_a_deadline_when_no_cancel_is_watching(tmp_path):
    """The pre-existing contract, unchanged: adding the cancel must not disturb the two endings
    that were already there.
    """
    res = run_jailed(_sleeper(), env={}, cwd=tmp_path, timeout=30, label="util 'wedged'",
                     aborted=_flag(after=0.5))
    assert res.aborted is True and res.cancelled is False
    assert res.exit_code == ABORT_EXIT
    assert "ended by the run's abort" in res.stderr


def test_a_call_whose_run_is_already_aborted_is_never_started(tmp_path):
    """Also unchanged — and it reports the abort, not a cancel."""
    res = run_jailed(_sleeper(), env={}, cwd=tmp_path, timeout=30, label="util 'x'",
                     aborted=lambda: True, cancelled=lambda: True)
    assert res.aborted is True and res.cancelled is False
    assert res.exit_code == ABORT_EXIT
    assert "was not started" in res.stderr


def test_the_poll_is_the_same_one_look_for_both_questions(monkeypatch, tmp_path):
    """One loop, one interval. A cancel polled in a loop of its own would put a second wait in
    the hottest process runner in the system, which is why F586 was a decision and not a button.
    """
    waits: list[float | None] = []
    real = subprocess.Popen.wait

    def spy(self, timeout=None):
        waits.append(timeout)
        return real(self, timeout=timeout)

    monkeypatch.setattr(subprocess.Popen, "wait", spy)
    run_jailed(_sleeper(1.2), env={}, cwd=tmp_path, timeout=30, label="util 'x'",
               aborted=lambda: False, cancelled=lambda: False)
    polled = [w for w in waits if w is not None]
    assert polled, "the wait must poll rather than block for the whole deadline"
    assert max(polled) <= utils_run.ABORT_POLL_S


def test_an_unwatched_call_still_blocks_for_its_whole_deadline(monkeypatch, tmp_path):
    """With neither callback there is nothing to poll FOR, so the wait must not spin: the
    no-callback path keeps its single long wait.

    The figure is `deadline - now` rather than the integer itself, so this asserts one wait of
    about the whole timeout — the property — and not a float equality that would fail on the
    microseconds spent reaching the loop.
    """
    waits: list[float | None] = []
    real = subprocess.Popen.wait

    def spy(self, timeout=None):
        waits.append(timeout)
        return real(self, timeout=timeout)

    monkeypatch.setattr(subprocess.Popen, "wait", spy)
    run_jailed([sys.executable, "-c", "pass"], env={}, cwd=tmp_path, timeout=9,
               label="util 'x'")
    assert len(waits) == 1
    assert waits[0] == pytest.approx(9, abs=0.1)


@pytest.mark.parametrize(("label", "expected"), [
    ("util 'thing'", "util 'thing' was cancelled by the user"),
    ("script 'helper'", "script 'helper' was cancelled by the user"),
    ("", "the command was cancelled by the user"),
])
def test_every_callable_kind_names_itself_in_the_cancel_note(tmp_path, label, expected):
    """All three kinds go through this one runner, so all three say it the same way."""
    res = run_jailed(_sleeper(), env={}, cwd=tmp_path, timeout=30, label=label,
                     cancelled=_flag(after=0.4))
    assert expected in res.stderr
