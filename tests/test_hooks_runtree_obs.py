"""What a run-tree hook's SENDER is told — the observation wording of `report` with a run-tree
`target` (R2924).

Its own module rather than more of `test_hooks_runtree.py`, which pins the ADDRESSING and the
DELIVERY: what the sender reads back is a third responsibility, and the file was at the size bar.

The hole these pin is specific. `runtree.send` returns its own observation shape —
`runtree`/`delivered`/`hook`/`addressee`, never the `filed` + `R<n>` of a ledger row — and the
report renderer had no branch for it, so every run-tree send, DELIVERED ones included, fell
through to the last line of the report branch: *"could NOT write the reports log (I/O error) —
the report was not filed"*. Measured on 2026-10-26: a child of `scheduler-builder` was told its
`target: parent` send had failed, re-sent it, and BOTH copies arrived in the parent's prompt. A
false failure costs more than a refusal, because the sender redoes work that already landed —
and it discredits the one channel a child has for saying its brief is wrong.
"""

from __future__ import annotations

from rsched.config import ServerConfig
from rsched.engine import runtree
from rsched.engine.obs_admin import format_admin
from test_hooks_runtree import TS, _child_ctx, _loop, _sub, run_context


def test_a_delivered_hook_to_the_parent_is_not_reported_as_a_failed_report(make_routine,
                                                                          tmp_path):
    """The exact case R2924 reported: delivered, and told the log could not be written."""
    d = make_routine("rtobs")
    parent = run_context(d, TS)
    parent.server = ServerConfig()
    parent.server.routines_home = tmp_path
    child = _child_ctx(parent, n=1, label="F677")
    child.server = parent.server

    obs = runtree.send(_loop(child), target="parent", title="brief is wrong", detail="why")
    line = format_admin(obs, "report")

    assert obs["delivered"] is True
    assert line is not None
    assert "I/O error" not in line
    assert "not filed" not in line
    assert obs["hook"] in line
    assert "parent" in line


def test_a_delivered_hook_to_a_child_names_the_child_it_reached(make_routine, tmp_path):
    """PARENT→CHILD too: the addressee is in the line, so a parent with several children can
    tell WHICH one it just reached — the whole point of addressing by label.
    """
    d = make_routine("rtobs2")
    parent = run_context(d, TS)
    parent.server = ServerConfig()
    parent.server.routines_home = tmp_path
    child = _child_ctx(parent, n=1, label="D202")
    child.server = parent.server
    loop = _loop(parent, subs=[_sub(1, "D202", child)])

    obs = runtree.send(loop, target="child:D202", title="stop", detail="rebase first")
    line = format_admin(obs, "report")

    assert obs["delivered"] is True
    assert line is not None
    assert "I/O error" not in line
    assert "child:D202" in line


def test_a_refused_hook_says_why_instead_of_blaming_the_log(make_routine, tmp_path):
    """The same hole swallowed every REFUSAL `runtree.resolve` writes — an unknown label, a
    child that already exited, a top-level run addressing `parent`, a sibling. Each is a
    precise reason the sender can act on, and all of them rendered as a disk error that never
    happened, which teaches the sender to retry the one thing that cannot work.
    """
    d = make_routine("rtref")
    ctx = run_context(d, TS)
    ctx.server = ServerConfig()
    ctx.server.routines_home = tmp_path

    obs = runtree.send(_loop(ctx), target="child:nosuch", title="t", detail="d")
    line = format_admin(obs, "report")

    assert obs["delivered"] is False
    assert obs["refused"]
    assert line is not None
    assert "I/O error" not in line
    assert "could NOT write" not in line
    assert obs["refused"] in line


def test_a_genuine_write_failure_still_says_nothing_was_delivered(make_routine, tmp_path,
                                                                  monkeypatch):
    """And the honest failure must survive the fix: when the inbox write really does raise,
    the sender is told the hook was NOT delivered and nothing is queued.

    Without this case the fix could have been "never report a failure", which is the same
    defect with its sign flipped — a failed delivery reading as a success.
    """
    d = make_routine("rtfail")
    parent = run_context(d, TS)
    parent.server = ServerConfig()
    parent.server.routines_home = tmp_path
    child = _child_ctx(parent, n=1, label="F677")
    child.server = parent.server

    def _boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr("rsched.engine.inbox.file_message", _boom)
    obs = runtree.send(_loop(child), target="parent", title="t", detail="d")
    line = format_admin(obs, "report")

    assert obs["delivered"] is False
    assert not obs.get("refused")
    assert line is not None
    assert "not delivered" in line
    assert "I/O error" in line
