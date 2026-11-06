"""An empty `tools:` list RESTRICTS, it does not open the gates — D242 (review item E1.6).

`engine/loopsetup.configure` read the allowlist with a FALSY test:

    loop.allowed_tools = set(allowed_tools) | {"finish"} if allowed_tools else None

`None` means "every kind allowed", and `[]` is falsy — so a workflow declaring `tools: []`
(nothing but the always-kinds) got exactly the opposite of what it declared: the full `KINDS`
surface, with the action schema projected from it. A restriction that fails OPEN is the one
direction a restriction must never fail in, and nothing downstream could catch it: both other
readers of the allowlist are already correct (`engine/kindsurface.effective_kinds` tests
`allowed_kinds is None`, and `workflows/lint._tools_problems` tests `tools is None`), so the
list arrived at them already turned into "unrestricted" by this one line.

The fix is the same `is not None` the other two use. The always-kinds still ride through —
`finish` so a run can always end, and `report`/`list_models` by `ALWAYS_KINDS` in
`effective_kinds` — so `tools: []` means "the always-kinds and nothing else", which is what a
workflow declaring it is asking for. An ABSENT `tools:` key still means every kind: both call
sites (`engine/runtime.load_workflow` and `engine/childrun`) pass `None` for a missing key and a
list only when the frontmatter really carries one, so no existing workflow changes.

Pinned through a real `EngineLoop`, not through the source line: what matters is the SET the
run ends up with, which is what the action schema is projected from.
"""

from __future__ import annotations

from helpers import server_for
from rsched.config import load_routine
from rsched.engine.actions import ALWAYS_KINDS, KINDS
from rsched.engine.budgets_config import Budgets
from rsched.engine.kindsurface import effective_kinds
from rsched.engine.loop import EngineLoop
from rsched.engine.run_context import RunContext
from rsched.engine.transcript import Transcript

TS = "20260708-070000"


def _loop(make_routine, allowed_tools):
    """A configured run, the way tests/test_loop.py builds one."""
    d = make_routine(slug="tv")
    server = server_for(d)
    run_dir = d / "runs" / TS
    run_dir.mkdir(parents=True)
    cfg, _ = load_routine(d)
    ctx = RunContext(routine=cfg, server=server, registry=None, run_ts=TS, run_dir=run_dir,
                     transcript=Transcript(run_dir / "transcript.jsonl"),
                     budgets=Budgets.from_config(cfg.budgets))
    return EngineLoop(ctx, "## Run flow", "instr", allowed_tools=allowed_tools)


def test_an_empty_tools_list_is_a_restriction_not_a_blank_cheque(make_routine):
    """THE DEFECT: `[]` is falsy, so it read as `None` — every kind allowed."""
    loop = _loop(make_routine, [])

    assert loop.allowed_tools is not None, \
        "tools: [] read as unrestricted — the restriction failed OPEN"
    # `create_routine`/`manage_lane` are added to any allowlist at depth 0 (loopsetup), so the
    # set is the creation kinds plus `finish` — never the whole surface.
    assert "write_file" not in loop.allowed_tools
    assert "shell" not in loop.allowed_tools
    assert "finish" in loop.allowed_tools


def test_a_workflow_declaring_no_tools_still_finishes_and_reports(make_routine):
    """`finish` is added by the allowlist itself (a run must always be able to end) and the
    other always-kinds ride through `effective_kinds`, so a `tools: []` run can end and raise a
    problem — and cannot write a file.
    """
    kinds = effective_kinds(_loop(make_routine, []).allowed_tools)

    for kind in ALWAYS_KINDS:
        assert kind in kinds, f"{kind} is an always-kind and must survive tools: []"
    assert "write_file" not in kinds
    assert "shell" not in kinds
    assert "util" not in kinds
    assert len(kinds) < len(KINDS) / 2, f"tools: [] left {len(kinds)} of {len(KINDS)} kinds"


def test_an_absent_tools_list_still_means_every_kind(make_routine):
    """The other half of the contract, which must NOT change: a workflow that declares no
    `tools:` at all is unrestricted. `None` and `[]` were the same value before this fix and
    are two different declarations after it.

    Asserted on the KINDS the run ends up with, not on `allowed_tools is None`: on an instance
    with no DECISION model `loopsetup` materializes the unrestricted case into
    `set(KINDS) - {"decide"}` so the schema omits a kind that cannot work, and the test server
    configures none. So "unrestricted" is a property of the set's CONTENTS either way — which
    is also what the action schema is projected from.
    """
    kinds = effective_kinds(_loop(make_routine, None).allowed_tools)

    assert "write_file" in kinds
    assert "shell" in kinds
    assert "util" in kinds
    assert len(kinds) >= len(KINDS) - 1, f"an absent tools: left only {len(kinds)} kinds"


def test_a_named_tools_list_keeps_its_kinds_and_only_those(make_routine):
    """And the ordinary case is untouched: the named kinds plus the always-kinds."""
    kinds = effective_kinds(_loop(make_routine, ["write_file", "read_file"]).allowed_tools)

    assert "write_file" in kinds and "read_file" in kinds
    assert set(ALWAYS_KINDS) <= set(kinds)
    assert "shell" not in kinds
