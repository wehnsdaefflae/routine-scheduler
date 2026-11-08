"""`/api/status`'s `action_limits` — the engine's own default operation limits, served once.

`static/components/actiontime.js` compares an action's elapsed time with the limit the engine
would apply to it, and carried a COPY of those numbers (`{util: 300, script: 300, shell: 120,
wait: 600}`). Nothing tied that copy to the engine: raising `shell`'s default in `shellrun.py`
left the console measuring against 120 s, and no test anywhere noticed (D252). The server
publishes them instead, built from the constants themselves.

These assertions read the constants rather than restating their values — a test that retyped
the numbers would be a third copy to drift.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from rsched.engine import waitop
from rsched.engine.fileops import UTIL_DEFAULT_TIMEOUT_S
from rsched.engine.waitop import WAIT_DEFAULT_TIMEOUT_S
from rsched.scripts import SCRIPT_TIMEOUT_S
from rsched.shellrun import SHELL_DEFAULT_TIMEOUT_S
from rsched.web import actionlimits


def test_the_served_limits_are_the_engines_own_constants():
    """One source of truth: every served value IS the constant the executor applies, so a
    change to either side cannot leave the console measuring against a dead number."""
    assert actionlimits.LIMITS == {
        "util": UTIL_DEFAULT_TIMEOUT_S,
        "script": SCRIPT_TIMEOUT_S,
        "shell": SHELL_DEFAULT_TIMEOUT_S,
        "wait": WAIT_DEFAULT_TIMEOUT_S,
    }
    # the `wait` default is NAMED, where the other three already had names and this one was a
    # literal in the middle of an expression (engine/waitop.wait)
    assert WAIT_DEFAULT_TIMEOUT_S == 600
    assert all(isinstance(v, int) and v > 0 for v in actionlimits.LIMITS.values())


class _Sub:
    """One child run that never finishes."""

    def __init__(self) -> None:
        self.n = 1
        self.done = threading.Event()
        self.announced = False


class _Subs:
    def __init__(self) -> None:
        self.subruns = {1: _Sub()}
        self.exit_event = threading.Event()
        # depth 1: a child run does not drain the routine inbox, so no state home is needed
        self.parent = SimpleNamespace(ctx=SimpleNamespace(depth=1), _background=[])

    def finished_rows(self, rows):
        return []

    def take_finished_unannounced(self):
        return []


@pytest.mark.timeout(10)
def test_the_named_wait_default_is_the_deadline_wait_applies(monkeypatch):
    """The served name is the one `wait` really uses: with it at 0 the call returns at once,
    which it cannot do while the deadline comes from a literal in the expression.
    """
    monkeypatch.setattr(waitop, "WAIT_DEFAULT_TIMEOUT_S", 0)
    out = waitop.wait(_Subs(), {}, poll_s=0.01, aborted=lambda: False)
    assert out["kind"] == "wait" and out["timed_out"] is True
    assert out["still_running"] == [1]


def test_status_serves_them_so_the_console_keeps_no_copy(api_client):
    c, _ = api_client
    body = c.get("/api/status").json()
    assert body["action_limits"] == actionlimits.LIMITS
    assert body["action_limits"]["wait"] == WAIT_DEFAULT_TIMEOUT_S
