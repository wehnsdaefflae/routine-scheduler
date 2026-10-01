"""The `remote` util (`util-seed/utils/remote`) carries its own `fair_share_order` and ships THAT
copy to the box, where an exclusive machine's queue decides which job runs next; the scheduler's
`machine_queue.fair_share_order` orders the same tickets for the console and the prompt. The
util runs in its own interpreter and cannot import the package, so the two are twins — pinned
here, because a drifted twin tells a run one position while the machine runs another.
"""

from __future__ import annotations

import importlib.util
from itertools import combinations
from pathlib import Path

import pytest

from rsched import machine_queue

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


def test_its_order_is_the_schedulers_for_every_queue(remote):
    for tickets in CASES:
        for given in (tickets, tickets[::-1]):
            assert remote.fair_share_order(given) == machine_queue.fair_share_order(given), given


def test_the_box_runs_that_same_copy(remote):
    """`build_queue_helper` splices the util's function into the on-box script by source, so the
    pin above covers the order the machine itself uses."""
    assert "def fair_share_order(" in remote.build_queue_helper()
