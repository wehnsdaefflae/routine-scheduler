"""The Messages page's headline number, decomposed (`readmodels/item_counts.py`): who the
worklist is waiting on, and which addressed reports are still in transit to a routine that has
not run yet. On 2026-10-18 the page read 100 with one item the operator could act on, and
nothing on it said so.
"""

from __future__ import annotations

import json

from rsched.readmodels import item_counts, memo
from rsched.readmodels import items as items_model


def _item(item_id: str, type_: str, status: str = "in_progress", **extra: object) -> dict:
    return {"id": item_id, "type": type_, "status": status, "title": "", "detail": "",
            "addressed": [], "origin": {"routine": "self-audit"}, **extra}


ROWS = [
    _item("F1", "finding", owner="scheduler-builder"),
    _item("F2", "finding", "open", owner="self-audit"),
    _item("D1", "decision", "open", owner="operator"),
    _item("D2", "decision"),                                     # no owner written yet
    _item("R1", "report", "open"),                               # unaddressed → triage
    _item("R2", "report", "open", to="grants-radar"),            # waits for its target's run
    _item("R3", "report", "in_progress", to="scheduler-builder"),  # drained, being worked
    _item("R4", "report", "open", to="switched-off"),            # nobody will ever drain it
    _item("F3", "finding", "addressed", owner="scheduler-builder"),  # not live
]


def test_the_worklist_is_split_by_who_must_act_next():
    active = item_counts.counts(ROWS, readers={"grants-radar", "scheduler-builder"})["active"]
    assert active["by_owner"] == {"scheduler-builder": 2, "operator": 1, "self-audit": 1,
                                  "switched-off": 1, "triage": 1, "unassigned": 1}
    # the split is OF the worklist: its parts sum to it, and nothing else is in it
    assert sum(active["by_owner"].values()) == active["worklist"] == 7


def test_an_owner_is_never_guessed():
    """A finding or decision with no `owner` in its report.json row is shown as unknown —
    not read off its status (an open decision is not necessarily the operator's) or its prose."""
    assert item_counts.owner_of(_item("D9", "decision", "open")) == item_counts.UNASSIGNED
    assert item_counts.owner_of(_item("R9", "report", "open")) == item_counts.TRIAGE
    assert item_counts.owner_of(_item("R8", "report", "open", to="hub")) == "hub"


def test_a_report_is_in_transit_only_when_a_routine_that_runs_will_drain_it():
    """An addressed, undrained report can only be acted on after its target's next scheduled
    run, so it is counted apart. One aimed at a routine that starts no run gets no delivery at
    all, and counting it as "in transit" would hide a stuck row — it stays on the worklist."""
    active = item_counts.counts(ROWS, readers={"grants-radar", "scheduler-builder"})["active"]
    assert active["in_transit"] == 1                            # R2 only
    assert "grants-radar" not in active["by_owner"]
    assert active["by_owner"]["switched-off"] == 1              # R4 stays visible
    # without readers nothing is in transit — the caller that knows the fleet supplies them
    assert item_counts.counts(ROWS)["active"]["in_transit"] == 0


def test_the_active_cross_tab_splits_the_worklist_from_the_unread_feed():
    """`open`+`in_progress` summed across types is the page's headline number and the one
    place summing lies: it mixes items someone must ACT on with unread run summaries, which
    reappear whenever any routine finishes. As one figure it can never reach zero by working
    the backlog. Neither `type` nor `status` can answer it — neither is conditioned on the
    other, and the type totals are lifetime rather than active.
    """
    merged = [*ROWS,
              {"type": "summary", "status": "open"},      # unread
              {"type": "summary", "status": "settled"}]   # read
    counted = item_counts.counts(merged, readers={"grants-radar"})
    assert counted["active"]["unread"] == 1
    assert counted["active"]["in_transit"] == 1                 # R2, on its way to the radar
    assert counted["active"]["worklist"] == sum(
        1 for i in merged if i["type"] != "summary" and i["status"] in ("open", "in_progress")
    ) - 1
    # the parts are disjoint and together they are the naive sum a reader would otherwise take
    naive = counted["status"].get("open", 0) + counted["status"].get("in_progress", 0)
    active = counted["active"]
    assert active["worklist"] + active["unread"] + active["folded"] + active["in_transit"] == naive


def test_a_folded_row_is_counted_and_listed_apart_from_the_worklist():
    """A row another report took over cannot be answered and settles with its carrier, so it
    is not a peer item. Counting it would make ten defects carried in one hand-off read as ten
    open threads, and it put the console 14 apart from self-audit's vitals over one ledger.
    """
    rows = [{"id": "R1", "type": "report", "status": "open", "title": "carrier", "detail": "",
             "addressed": [], "superseded": {}},
            {"id": "R2", "type": "report", "status": "open", "title": "folded in", "detail": "",
             "addressed": [], "superseded": {"by": "R1", "to": "owner", "ts": "x"}}]
    counted = item_counts.counts(rows)
    assert counted["active"] == {"worklist": 1, "unread": 0, "folded": 1, "in_transit": 0,
                                 "by_owner": {"triage": 1}}

    # off the list by default…
    assert [i["id"] for i in items_model.filter_items(rows)] == ["R1"]
    # …reachable when asked for by name, or by the chip
    assert [i["id"] for i in items_model.filter_items(rows, folded=True)] == ["R1", "R2"]
    assert [i["id"] for i in items_model.filter_items(rows, search="folded in")] == ["R2"]


def test_filter_items_by_owner():
    assert [i["id"] for i in items_model.filter_items(ROWS, owner="operator")] == ["D1"]
    assert [i["id"] for i in items_model.filter_items(ROWS, owner="triage")] == ["R1"]
    assert {i["id"] for i in items_model.filter_items(ROWS, owner="scheduler-builder")} \
        == {"F1", "R3", "F3"}
    summary = {"id": "s:1", "type": "summary", "status": "open", "title": "", "detail": "",
               "addressed": [], "origin": {"routine": "x"}}
    assert items_model.filter_items([summary], owner="unassigned") == []   # a summary has none


def test_api_items_carries_the_owner_split_and_filters_by_it(api_client, make_routine):
    c, tmp = api_client
    make_routine("grants-radar")                 # a routine that runs: a delivery will reach it
    routines = tmp / "routines"
    audit = routines / "self-audit" / "audit"
    audit.mkdir(parents=True)
    report = {"schema": 1, "run_id": "self-audit:1", "generated": "2026-10-18T01:00:00+02:00",
              "findings": [{"id": "F1", "severity": "problem", "status": "in_progress",
                            "title": "handed", "detail": "", "owner": "scheduler-builder"}],
              "decisions": [{"id": "D1", "title": "pick", "detail": "", "status": "open",
                             "options": ["a", "b"], "owner": "operator"}]}
    (audit / "report.json").write_text(json.dumps(report), encoding="utf-8")
    (routines / ".control").mkdir(parents=True, exist_ok=True)
    rows = [{"id": "R1", "ts": "2026-10-17T08:00:00+02:00", "routine": "self-audit",
             "run_id": "self-audit:1", "title": "for the radar", "detail": "",
             "target": "grants-radar"}]
    (routines / ".control" / "reports.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    memo.reset()

    data = c.get("/api/items").json()
    assert data["counts"]["active"]["by_owner"] == {"operator": 1, "scheduler-builder": 1}
    assert data["counts"]["active"]["in_transit"] == 1
    assert {i["id"]: i.get("owner") for i in data["items"] if i["id"] in ("F1", "D1")} \
        == {"F1": "scheduler-builder", "D1": "operator"}
    assert [i["id"] for i in c.get("/api/items?owner=operator").json()["items"]] == ["D1"]
