"""F826 — a lane chain's end-of-chain row says figures that reconcile against the store.

`daemon/lane_runs._finalize` printed `len(rec["log"])` as "<n> member runs", and the chain LOG
is not runs: a member switched off (or retired) is logged `outcome: "skipped"` with no run id.
Measured 2026-11-01 over 31 `lane_chain_done` rows: `Professional · Daily` read "5 member runs"
on every fire from 10-25 to 11-01 while two of its five members were `enabled: false` and only
three runs existed. The existing test of exactly that shape (`test_lane_runs.py`, the
switched-off member) asserted "2 member runs" for ONE run — the suite pinned the defect.

`chain_detail` now names the chain's MEMBERS (its arm-time fire list), how many RAN, which were
SKIPPED, which were NOT-OK and how many a stopped chain never REACHED, so
members = ran + skipped + missing + not reached for every chain.
"""

from __future__ import annotations

import re

from conftest import FakeRunner, mk_run
from helpers import health_events, tmp_server
from rsched import lane_runs, lanes, registry
from rsched.daemon.lane_runs import LaneRunManager, chain_detail
from test_lane_runs import _routine, m

_FIGURES = re.compile(r": (\d+) of (\d+) members ran(?:, (\d+) skipped \([^)]*\))?, (\d+) not-ok"
                      r"(?: \([^)]*\))?(?:, (\d+) not reached)?, armed_by=")


def _reconciles(detail: str, *, missing: int = 0) -> dict:
    """Parse the figures and check the identity every chain row must satisfy."""
    hit = _FIGURES.search(detail)
    assert hit, detail
    ran, members, skipped, not_ok, unreached = (int(g or 0) for g in hit.groups())
    failed_runs = not_ok - missing
    assert members == ran + skipped + missing + unreached, detail
    assert 0 <= failed_runs <= ran, detail
    return {"ran": ran, "members": members, "skipped": skipped, "not_ok": not_ok,
            "unreached": unreached}


async def test_disabled_members_are_skipped_not_counted_as_runs(tmp_path):
    """The `Professional · Daily` shape: five members, two switched off → 3 of 5 ran."""
    server = tmp_server(tmp_path)
    on = {s: _routine(server, s) for s in ("a", "b", "c")}
    for s in ("off1", "off2"):
        _routine(server, s, enabled=False)
    lane = lanes.create(server.routines_home, name="Professional · Daily",
                        members=[m("a"), m("off1"), m("b"), m("off2"), m("c")],
                        on_failure="continue")
    lane_runs.arm(server.routines_home, lane, default_on_failure="continue")
    runner, catalog = FakeRunner(), registry.scan(server)
    mgr = LaneRunManager(server, runner)
    for _ in range(12):                       # fire/collect each live member, skip the off ones
        await mgr.tick(catalog)
        for d in on.values():
            if runner.active and d.name in runner.active:
                mk_run(d, runner.ts, "finished", outcome="ok")
                runner.active.clear()
    (ev,) = health_events(server.routines_home, event="lane_chain_done")
    assert "Professional · Daily: 3 of 5 members ran, 2 skipped (off1, off2), 0 not-ok," \
        in ev["detail"]
    assert _reconciles(ev["detail"]) == {"ran": 3, "members": 5, "skipped": 2, "not_ok": 0,
                                         "unreached": 0}


def test_a_stopped_chain_names_what_it_never_reached():
    rec = {"name": "G", "members": [m("a"), m("b"), m("c")], "armed_by": "schedule",
           "created": "2026-11-01T09:00:00+01:00"}
    log = [{"slug": "a", "run_id": "a:20261101-090000", "outcome": "failed"}]
    detail = chain_detail(rec, log)
    assert detail.startswith("G: 1 of 3 members ran, 1 not-ok (a), 2 not reached, armed_by=")
    _reconciles(detail)


def test_a_missing_member_is_not_ok_without_being_a_run():
    rec = {"name": "G", "members": [m("ghost"), m("a")], "armed_by": "ui", "created": "t"}
    log = [{"slug": "ghost", "run_id": None, "outcome": "failed"},
           {"slug": "a", "run_id": "a:20261101-090000", "outcome": "ok"}]
    detail = chain_detail(rec, log)
    assert detail == "G: 1 of 2 members ran, 1 not-ok (ghost), armed_by=ui, armed t"
    _reconciles(detail, missing=1)


def test_an_empty_lane_reads_as_nothing_ran():
    """`rsched validate` names this case: an empty scheduled lane still writes a done row."""
    detail = chain_detail({"name": "E", "members": [], "created": "t"}, [])
    assert detail == "E: 0 of 0 members ran, 0 not-ok, armed_by=?, armed t"
