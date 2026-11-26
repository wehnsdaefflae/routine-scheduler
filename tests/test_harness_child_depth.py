"""The start prompt must not offer a child that admission cannot start."""
from types import SimpleNamespace

import pytest

from helpers import run_context, server_config
from rsched.engine.harness import harness_contract
from rsched.engine.subruns import SubrunManager


@pytest.mark.parametrize(("depth", "limit", "used"), [(1, 2, 3), (2, 2, 3), (0, 0, 0)])
def test_child_allowance_respects_admission_depth(make_routine, tmp_path, depth, limit, used):
    ctx = run_context(make_routine(slug="depth"), "20260708-070000",
                      server=server_config(libraries_home=tmp_path / "libraries"))
    ctx.depth = depth
    ctx.budgets.max_child_depth = limit
    ctx.budgets.max_child_runs = 8
    ctx.sub_counter[0] = used
    admission = SubrunManager(SimpleNamespace(ctx=ctx))._cap_reason(noun="child-task")
    line = next(line for line in harness_contract(ctx).splitlines()
                if line.startswith("Budgets for this run:"))
    if depth < limit:
        assert admission is None
        assert "5 more child runs" in line
    else:
        assert admission == f"max child-task depth ({limit}) reached"
        assert "no child runs (depth limit reached)" in line
        assert "more child runs" not in line and "at most 8 child runs" not in line
