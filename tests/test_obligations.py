"""OBLIGATIONS (engine/obligations.py) and the owed-work watch (daemon/owed_watch.py).

On 2026-07-30 the operator approved freelance-radar's profile update on the Decisions page —
"You fix all of that" — and it was never applied: the answer reached one run's digest, that run
ended, and the next run started from nothing. R2044 ("nobody owns deploying the Steward kit")
sat three weeks with no owner. These tests pin the two paths that end both: an approval becomes
a goal a run can quote and carry until one meets it, and owed work past a week reaches the
operator — an overdue obligation on the blocked-work surface, an unowned report as a decision.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from conftest import authed_client, finish, seeded_server
from helpers import server_for, transcript_events
from rsched import pending, reports
from rsched.daemon.owed_watch import OwedWatch
from rsched.engine import obligations
from rsched.engine.runtime import run_routine
from rsched.paths import atomic_write_json

TS1, TS2 = "20261008-120000", "20261009-120000"
APPROVAL = "You fix all of that"


def _deferred_answer(d, text=APPROVAL):
    qid = "q-20261007-090000-1"
    atomic_write_json(d / "questions" / "pending" / f"{qid}.json",
                      {"qid": qid, "question": "Shall I update your profile?", "mode": "deferred"})
    atomic_write_json(d / "inbox" / f"answer-{qid}.json", {"qid": qid, "text": text})


def _goal(goals):
    return {"say": "Taking on what they approved.", "kind": "goal", "verb": "set", "goals": goals}


def test_an_approval_answered_between_runs_is_carried_until_a_run_meets_it(make_routine,
                                                                          scripted):
    d = make_routine(slug="radar")
    (d / "questions" / "pending").mkdir(parents=True)
    _deferred_answer(d)
    scripted([_goal([{"text": "the profile is updated as approved", "quote": APPROVAL}]),
              {**finish(), "accounting": ["b1 unmet: the profile text is not written yet"]}])
    assert run_routine(d, server_for(d), run_ts=TS1)[0] == "ok"
    answer = next(e for e in transcript_events(d / "runs" / TS1) if e["type"] == "answer")
    assert answer["payload"]["text"] == APPROVAL and answer["payload"]["source"] == "deferred"
    [owed] = obligations.load(d)
    assert owed["id"] == "b1" and owed["quote"] == APPROVAL
    assert owed["remains"] == "the profile text is not written yet"

    # the NEXT run starts with it and must answer for it — a finish that skips it is deferred
    look = {"say": "Reading the recipe.", "kind": "read_file", "path": "main.md"}
    scripted([look, finish(), {**finish(), "accounting": ["b1 unmet: still not written"]}])
    assert run_routine(d, server_for(d), run_ts=TS2)[0] == "ok"
    deferred = [e for e in transcript_events(d / "runs" / TS2)
                if e["type"] == "observation" and e["payload"].get("kind") == "finish"
                and e["payload"].get("rejected")]
    assert deferred and "b1" in json.dumps(deferred[0]["payload"])
    assert obligations.load(d)[0]["since"] == owed["since"]      # the day it was first owed


def test_a_run_cannot_close_an_obligation_by_editing_the_file(make_routine, scripted):
    d = make_routine(slug="sealed")
    scripted([{"say": "Clearing it.", "kind": "write_file", "path": "state/obligations.json",
               "content": {"open": []}}, finish()])
    run_routine(d, server_for(d), run_ts=TS1)
    obs = next(e["payload"] for e in transcript_events(d / "runs" / TS1)
               if e["type"] == "observation" and e["payload"].get("kind") == "write_file")
    assert "engine's record of what a person asked" in json.dumps(obs)


def test_owed_work_past_a_week_reaches_the_operator(tmp_path, make_routine):
    server = seeded_server(tmp_path)
    home = server.routines_home
    d = make_routine(slug="radar")
    old = (datetime.now(UTC) - timedelta(days=10)).date().isoformat()
    atomic_write_json(d / obligations.FILE, {"open": [
        {"id": "b1", "text": "the profile is updated", "quote": APPROVAL, "since": old,
         "remains": "not written"}]})
    make_routine(slug="scheduler-builder")
    reports.file_report(home, routine="radar", run_id="radar:1",
                        title="Nobody owns deploying the kit")
    ledger = reports.reports_path(home)
    rows = [json.loads(x) for x in ledger.read_text().splitlines()]
    rows[0]["ts"] = f"{old}T08:00:00+00:00"
    ledger.write_text("".join(json.dumps(r) + "\n" for r in rows))

    OwedWatch(server).check(datetime.now(UTC).date())
    events = [json.loads(x) for x in (home / ".control" / "health-events.jsonl").read_text()
              .splitlines()]
    [ev] = [e for e in events if e["event"] == "obligation_overdue"]
    assert ev["routine"] == "radar" and "b1 open 10 days" in ev["detail"]
    [rec] = pending.load_all(home)
    assert rec["kind"] == "unowned-report" and rec["fields"]["report"] == rows[0]["id"]
    assert rec["fields"]["suggest"][:2] == ["radar", "scheduler-builder"]

    with authed_client(server) as c:
        r = c.post(f"/api/pending-creations/{rec['id']}/materialize",
                   json={"choice": "route", "to": "scheduler-builder"})
        assert r.status_code == 200, r.text
    folded = [json.loads(x) for x in ledger.read_text().splitlines()]
    assert any(x.get("event") == "superseded" and x.get("to") == "scheduler-builder"
               for x in folded)
    assert list((home / "scheduler-builder" / "inbox").glob("*rep-*.json"))
    # owned now: the next sweep files nothing new for it
    OwedWatch(server).check(datetime.now(UTC).date())
    assert pending.load_all(home) == []
