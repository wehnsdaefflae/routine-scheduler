"""The `remote` util (`util-seed/utils/remote`) holds the ONE `fair_share_order` and ships that
copy to the box, where an exclusive machine's queue decides which job runs next — the scheduler
only MIRRORS the box's answer (`machine_queue.refresh`). Its selftest pins concrete orders; this
pins the contract over every queue a three-holder pool can form, plus the shapes a damaged ticket
has, and that the box really runs this copy.
"""

from __future__ import annotations

import importlib.util
import json
from itertools import combinations
from pathlib import Path
from types import SimpleNamespace

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "util-seed/utils/remote/main.py"


@pytest.fixture(scope="module")
def remote():
    spec = importlib.util.spec_from_file_location("remote_util", SOURCE)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# every subset of a pool that interleaves three holders, plus the shapes a damaged ticket has
POOL = [{"holder": h, "job": f"j{n}", "submitted": f"2026-09-0{n}"}
        for n, h in enumerate("aabcab", start=1)]
CASES = [list(c) for k in range(len(POOL) + 1) for c in combinations(POOL, k)] + [
    [{"job": "nameless", "submitted": "1"}, {"holder": "a", "job": "j", "submitted": "2"}],
    [{"holder": "a", "job": "undated"}, {"holder": "b", "job": "dated", "submitted": "1"}],
]


def test_every_queue_is_ordered_whatever_order_the_box_lists_it_in(remote):
    """The order is a property of the tickets, never of the listing: each ticket appears exactly
    once, a holder's own jobs run oldest first (FIFO within one), and no holder runs a second job
    while another holder that has one waiting has not yet run its first (round-robin)."""
    for tickets in CASES:
        out = remote.fair_share_order(tickets)
        assert out == remote.fair_share_order(tickets[::-1]), tickets
        assert sorted(map(repr, out)) == sorted(map(repr, tickets))
        dated = [t for t in out if t.get("holder") and t.get("submitted")]
        for holder in {t["holder"] for t in dated}:
            mine = [t["submitted"] for t in dated if t["holder"] == holder]
            assert mine == sorted(mine), (holder, out)
        holders = [t["holder"] for t in dated]
        first_seconds = next((i for i, h in enumerate(holders) if holders[:i].count(h) == 1),
                             len(holders))
        assert len(set(holders[:first_seconds])) == first_seconds, out


def test_the_box_runs_that_same_copy(remote):
    """`build_queue_helper` splices the util's function into the on-box script by source, so the
    order pinned above is the order the machine itself uses."""
    assert "def fair_share_order(" in remote.build_queue_helper()


def _box(monkeypatch, remote, answer: list[dict]) -> None:
    """Stand in for the SSH session: every remote command answers with the box's `list`."""
    monkeypatch.setattr(remote, "connect", lambda m, keys, **kw: SimpleNamespace(close=dict))
    monkeypatch.setattr(remote, "_run",
                        lambda client, command, timeout, cwd="": (0, json.dumps(answer), ""))


def test_the_queue_is_reported_in_the_order_the_box_gave(remote, monkeypatch):
    """The box orders over the whole ROUND — turns already spent and turns still waiting — and
    only it knows the spent ones. funscript's f1 has just run, so voice's v1 is next. Re-sorting
    the box's answer over the waiting tickets alone put funscript's f2 back in front, in the
    queue the console mirrors and in the position a submitting run reads."""
    box = [{"holder": "voice", "job": "v1", "submitted": "2026-09-05T10:00:03+00:00"},
           {"holder": "funscript", "job": "f2", "submitted": "2026-09-05T10:00:01+00:00"},
           {"holder": "funscript", "job": "f3", "submitted": "2026-09-05T10:00:02+00:00"}]
    assert [t["job"] for t in remote.fair_share_order(box)] != ["v1", "f2", "f3"]   # the trap
    _box(monkeypatch, remote, box)
    machine = {"name": "gpu", "exclusive": True}
    shown = remote.cmd_queue(machine, {}, "")
    assert [t["job"] for t in shown["tickets"]] == ["v1", "f2", "f3"]
    monkeypatch.setenv("RSCHED_ROUTINE", "voice")
    sub = remote._submit_queued(machine, {}, "v1", "QUk=", "", 1.0, 5)
    assert (sub["position"], sub["ahead"]) == (1, 0)
    assert [t["job"] for t in sub["queue"]] == ["v1", "f2", "f3"]


def test_an_unreachable_host_is_dialed_once_not_once_per_key_type(remote, monkeypatch):
    """Each key type is negotiated over a connection of its own, but a host that cannot be
    dialed is unreachable for all three: the scan waited out the 15 s connect three times and
    then only guessed "(unreachable?)"."""
    dials = []

    def refuse(address, timeout):
        dials.append(address)
        raise ConnectionRefusedError(111, "Connection refused")

    monkeypatch.setattr("socket.create_connection", refuse)
    with pytest.raises(remote.RemoteError, match=r"could not reach gpu\.lan:22: .*refused"):
        remote.cmd_scan_host("gpu.lan", 22)
    assert dials == [("gpu.lan", 22)]
