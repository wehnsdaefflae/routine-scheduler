"""The run gate's HISTORY: every run row carries the verdict that decided that fire.

R2764 (operator, in his own words): "like the list of previous runs under 'What a finished run
delivers' we also need a history of the previous gates. which ones fired, which ones didn't.
report that".

`runs/<ts>/gate.json` has carried that per-fire record since F457 (`daemon/run_gate.py`'s
`finally`, :284) and no read surface served a byte of it: `GET /api/routines/{slug}` carried
`run_gate` (the CONFIG — what the gate WOULD ask) and the run rows (what ran), so "which fires
were skipped, and by which check" was answerable only by reading JSON on the box.

The SKIPPED fire is the half the operator most wants, and it is readable: `daemon/runner.py`
claims the run dir (`_claim_run_dir`, :230) BEFORE `run_gate.admit` (:330), so a skip leaves
`runs/<ts>/` holding a `status.json` (`outcome: skipped`, written by `run_gate.terminal`) and a
`gate.json` exactly like an admitted fire's. No new store, and no fire goes unrecorded.
"""

import pytest

from conftest import authed_client, make_test_server, mk_run
from rsched.paths import atomic_write_json

SKIP_TS, RUN_TS = "20261023-030000", "20261023-150000"

# A skipped fire's gate.json as run_gate writes it: the fire's reason, every check's answer,
# the decision and the merged reason — plus the `fingerprints` block, which is the gate's own
# baseline bookkeeping and no reader's business.
SKIPPED_GATE = {
    "version": 1,
    "fire_reason": "schedule",
    "run_id": f"gater:{SKIP_TS}",
    "checks": [
        {"id": "inbox", "kind": "inbox", "work": False, "reason": "no new mail since 0a1b2c"},
        {"id": "script", "kind": "script", "work": False, "reason": "nothing due"},
    ],
    "fingerprints": {"inbox": "0a1b2c"},
    "decision": "skip",
    "reason": "no new mail since 0a1b2c \u00b7 nothing due",
}

ADMITTED_GATE = {
    "version": 1,
    "fire_reason": "schedule",
    "run_id": f"gater:{RUN_TS}",
    "checks": [{"id": "inbox", "kind": "inbox", "work": True, "reason": "3 new messages"}],
    "decision": "run",
    "reason": "3 new messages",
}


@pytest.fixture
def gated(tmp_path, make_routine):
    """A routine with two fires on disk: one the gate SKIPPED, one it admitted."""
    make_routine(slug="gater")
    d = tmp_path / "routines" / "gater"
    mk_run(d, SKIP_TS, "finished", outcome="skipped",
           summary="Run gate skipped: no new mail since 0a1b2c")
    atomic_write_json(d / "runs" / SKIP_TS / "gate.json", SKIPPED_GATE)
    mk_run(d, RUN_TS, "finished", outcome="ok", summary="did the thing")
    atomic_write_json(d / "runs" / RUN_TS / "gate.json", ADMITTED_GATE)
    with authed_client(make_test_server(tmp_path)) as c:
        yield c


def _rows(client):
    r = client.get("/api/routines/gater")
    assert r.status_code == 200, r.text
    return {row["ts"]: row for row in r.json()["runs"]}


def test_a_skipped_fires_verdict_rides_its_own_run_row(gated):
    """"Which ones didn't" — the skip, with the check that decided it, on the run row.

    This is the row no other surface can explain: a skipped fire has no turns, no cost and no
    work, so every other column reads as an empty run and nothing said a GATE made that call.
    """
    gate = _rows(gated)[SKIP_TS]["gate"]
    assert gate["decision"] == "skip"
    assert gate["fire_reason"] == "schedule"
    assert gate["reason"] == "no new mail since 0a1b2c \u00b7 nothing due"
    assert [(c["id"], c["kind"], c["work"], c["reason"]) for c in gate["checks"]] == [
        ("inbox", "inbox", False, "no new mail since 0a1b2c"),
        ("script", "script", False, "nothing due"),
    ]


def test_an_admitted_fires_verdict_says_which_check_found_the_work(gated):
    """"Which ones fired" — and WHY a fire became a run at all, which is the same question."""
    gate = _rows(gated)[RUN_TS]["gate"]
    assert gate["decision"] == "run"
    assert gate["reason"] == "3 new messages"
    assert [c["work"] for c in gate["checks"]] == [True]


def test_a_fire_no_gate_judged_carries_no_verdict(tmp_path, make_routine):
    """An ungated routine's rows read exactly as they did: `gate` is None, not an empty
    verdict. A gate that never ran must not render as a gate that found nothing.
    """
    make_routine(slug="plain")
    mk_run(tmp_path / "routines" / "plain", RUN_TS, "finished", outcome="ok")
    with authed_client(make_test_server(tmp_path)) as c:
        assert _rows_for(c, "plain")[RUN_TS]["gate"] is None


def _rows_for(client, slug):
    r = client.get(f"/api/routines/{slug}")
    assert r.status_code == 200, r.text
    return {row["ts"]: row for row in r.json()["runs"]}


def test_the_verdict_is_bounded_and_leaks_no_gate_bookkeeping(gated):
    """Only the named fields cross the wire. `fingerprints` is the gate's baseline — hashes
    of what the checks saw, meaningless to a reader and unbounded in width — so a verdict
    carries the decision, not a copy of gate.json.
    """
    gate = _rows(gated)[SKIP_TS]["gate"]
    assert set(gate) == {"decision", "fire_reason", "reason", "checks"}
    assert set(gate["checks"][0]) == {"id", "kind", "work", "reason"}


def test_the_resume_window_each_run_row_already_carried_is_untouched(gated):
    """The verdict is ADDED to `engine.turnwindow.run_row`'s row, never a replacement for it:
    F564/D155's window, the turn count and the summary stay where their readers expect them.
    """
    row = _rows(gated)[RUN_TS]
    assert {"run_id", "ts", "state", "summary", "turn", "usage", "elapsed_s", "window"} <= set(row)
    assert row["window"] is None
    assert row["summary"] == "did the thing"
