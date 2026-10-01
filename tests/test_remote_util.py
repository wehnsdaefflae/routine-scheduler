"""The `remote` util (`util-seed/utils/remote`) holds the ONE `fair_share_order` and ships that
copy to the box, where an exclusive machine's queue decides which job runs next — the scheduler
only MIRRORS the box's answer (`machine_queue.refresh`). Its selftest pins concrete orders; this
pins the contract over every queue a three-holder pool can form, plus the shapes a damaged ticket
has, and that the box really runs this copy.
"""

from __future__ import annotations

import importlib.util
from itertools import combinations
from pathlib import Path

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
