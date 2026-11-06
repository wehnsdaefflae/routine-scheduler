"""`wait` covers BACKGROUNDED calls, not only child runs (engine/waitop.py).

scheduler-builder used `wait` 8 times on one `background: true` call while no child run existed,
each answered at once with "no sub-workflows have been spawned"; and with children it never woke
on a landing, so a gate verdict back in two minutes sat unread through a 20-minute timeout ("The
wait burned 20 minutes"). The engine knows what is in flight, so a wait waits for it — a guard
whose fix is mechanical applies it.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

from rsched.engine import waitop
from rsched.engine.background import Pending
from rsched.engine.observations import format_observation
from rsched.engine.subruns import SubrunManager


def _call(release: threading.Event, handle: str = "bg1") -> Pending:
    """A REAL pending background call whose thread runs until `release` is set."""
    thread = threading.Thread(target=release.wait, args=(10,), daemon=True)
    thread.start()
    return Pending(handle=handle, kind="shell", brief="pytest", turn=1, thread=thread)


def _subs(children: dict | None = None, background: list | None = None):
    """A SubrunManager's surface as `waitop.wait` reads it — children plus the parent loop."""
    parent = SimpleNamespace(ctx=SimpleNamespace(depth=1), _background=background or [])
    subs = SimpleNamespace(subruns=children or {}, exit_event=threading.Event(), parent=parent)
    subs.take_finished_unannounced = list
    subs.finished_rows = SubrunManager.finished_rows
    return subs


def _wait(subs, **action):
    return waitop.wait(subs, action, poll_s=0.01, aborted=lambda: False)


def test_with_no_child_a_wait_waits_for_the_background_call_and_names_its_landing():
    release = threading.Event()
    subs = _subs(background=[_call(release)])
    threading.Timer(0.2, release.set).start()
    started = time.monotonic()
    obs = _wait(subs, timeout_s=5)
    assert time.monotonic() - started < 3                   # woke on the landing, not the timeout
    assert obs["timed_out"] is False and obs["background_landed"] == ["bg1"]
    seen = format_observation(obs)
    assert "bg1 LANDED" in seen and "follows below" in seen


def test_a_still_running_call_times_out_and_is_named_as_still_running():
    release = threading.Event()
    obs = _wait(_subs(background=[_call(release)]), timeout_s=0.1)
    release.set()
    assert obs["timed_out"] is True and obs["background_running"] == ["bg1"]
    assert "background still running: bg1" in format_observation(obs)


def test_an_any_wait_with_a_running_child_still_ends_on_a_background_landing():
    """The 20-minute case: a child is busy, the gate verdict lands — the wait returns then."""
    child = SimpleNamespace(n=1, label="t", status="", mode="parallel", summary="",
                            done=threading.Event(), announced=False, collected_paths=(),
                            ctx=SimpleNamespace(turn=1))
    release = threading.Event()
    subs = _subs({1: child}, [_call(release, "bg4")])
    threading.Timer(0.1, release.set).start()
    obs = _wait(subs, timeout_s=5)
    assert obs["timed_out"] is False and obs["background_landed"] == ["bg4"]
    assert obs["still_running"] == [1]


def test_all_waits_for_every_background_call_as_well_as_every_child():
    release = threading.Event()
    obs = _wait(_subs(background=[_call(release)]), all=True, timeout_s=0.1)
    assert obs["timed_out"] is True
    release.set()
    time.sleep(0.05)
    assert _wait(_subs(background=[_call(release)]), all=True, timeout_s=2)["timed_out"] is False


def test_with_nothing_in_flight_the_wait_says_so_at_once():
    obs = _wait(_subs(), timeout_s=60)
    assert "nothing to wait for" in obs["error"]
    assert format_observation(obs).startswith("OBSERVATION (wait FAILED): nothing to wait for")
