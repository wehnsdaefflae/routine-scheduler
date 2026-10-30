"""An `accounting` field that arrived ABSENT is named as absent — never as incomplete content.

MEASURED on `birthday-admin:20261022-080004` turns 83-96 (F805). Turn 83 finished with a
complete nine-entry accounting and was rejected on a CLAIM objection against d4; from turn 84 on
every finish payload in the transcript carries `accounting: null` — turns 84, 85, 86, 87, 88, 89,
90, 91 and 95 — and each was rejected with the same sentence, "your accounting is incomplete: no
entry for d1, d2, d3, d4, d5, d6, d7, d8, g1". The run's own words at turn 85: "the accounting
array appears to have been present but the engine reports it as missing all entries;
re-submitting identically". It escaped only by rebuilding the list one entry at a time (turn 92
one entry, 93 two, 94 all nine, accepted at 96) — roughly ten turns at full prompt price.

The CAUSE is a transport defect the engine already repairs where it can (`engine/paramsalvage.py`:
a `</summary>` closer the model mismatched swallows every parameter written after it, measured on
this same run). What had no fix is the DIAGNOSIS: `accounting.parse` flattens a missing key, a
`null` and an empty list all to `{}`, so every owed id read as `missing` and the refusal spoke
about CONTENT the run could still see in its own reply. One sentence naming the absence would
have saved those turns, so absence is its own finding here — exclusive with `missing`, because an
entry that was never received is not an entry that is wrong.
"""

from __future__ import annotations

import json

import pytest
import yaml

from conftest import WORKFLOW_MD, finish, make_test_server  # noqa: F401  (fixture side effects)
from helpers import server_for
from rsched import jobs
from rsched.config import ServerConfig
from rsched.engine import accounting
from rsched.engine.runtime import run_routine
from rsched.engine.transcript import read_events

TS = "20261030-090000"

DONE = [{"id": "d1", "stage": "", "text": "a"}, {"id": "d2", "stage": "", "text": "b"}]
OUTCOMES = [{"id": "g1", "judge": "run", "text": "x"}]

DONE_WHEN_MD = WORKFLOW_MD.rstrip() + """

## Done when

- d1 — the figures are checked against the ledger
- d2 — the digest is published and its link resolves
"""


# ---- what counts as ARRIVED --------------------------------------------------------------------

@pytest.mark.parametrize("nothing", [None, [], "", "d1 met: x", ["   "], ["", " "], 0])
def test_nothing_received_is_nothing_received_whatever_shape_it_took(nothing):
    """Every shape the specimen's turns and a constrained decoder can produce. A STRING is not
    the field either (`parse` already refuses one), and a list of blanks carries no entry."""
    assert not accounting.arrived(nothing), nothing


def test_one_real_entry_is_a_field_that_arrived():
    assert accounting.arrived(["d1 met: the ledger matches"])
    assert accounting.arrived(["nonsense that will not parse"])   # arrived, and says nothing


# ---- absent is its own finding, exclusive with missing -----------------------------------------

def test_the_owed_ids_are_reported_absent_rather_than_missing():
    found = accounting.problems({}, DONE, OUTCOMES, arrived=False)
    assert found["absent"] == ["d1", "d2", "g1"]
    assert found["missing"] == []          # one finding about one field, never two
    assert found["bare"] == [] and found["placeholder"] == [] and found["refused"] == []


def test_a_field_that_did_arrive_short_still_reports_its_missing_entries():
    found = accounting.problems(accounting.parse(["d1 met: the ledger matches"]),
                                DONE, OUTCOMES, arrived=True)
    assert found["absent"] == [] and found["missing"] == ["d2", "g1"]


def test_a_field_that_arrived_unparseable_is_missing_not_absent():
    """It was received; what it says answers for nothing. That IS a verdict about content."""
    found = accounting.problems(accounting.parse(["garbage"]), DONE, OUTCOMES, arrived=True)
    assert found["absent"] == [] and found["missing"] == ["d1", "d2", "g1"]


# ---- what the refusal says ---------------------------------------------------------------------

def test_the_refusal_names_the_absence_and_what_to_do_about_it():
    msg = accounting.deferral(accounting.problems({}, DONE, OUTCOMES, arrived=False))
    assert "is incomplete" not in msg                 # the verdict that cost ten turns
    assert "no `accounting` field arrived" in msg
    assert "ABSENT" in msg
    assert "SHORTER summary" in msg                   # the actionable half of the diagnosis
    assert "d1, d2, g1" in msg                        # …and what is still owed
    assert "Carry one entry per line you owe" in msg  # the shared tail survives


def test_an_incomplete_field_still_reads_as_incomplete():
    msg = accounting.deferral(accounting.problems(
        accounting.parse(["d1 met: the ledger matches"]), DONE, OUTCOMES, arrived=True))
    assert "your `accounting` is incomplete" in msg
    assert "arrived" not in msg and "ABSENT" not in msg
    assert "no entry for d2, g1" in msg


def test_the_gaps_clause_names_the_absence_once():
    text = accounting.gaps(accounting.problems({}, DONE, OUTCOMES, arrived=False))
    assert "nothing was received for d1, d2, g1" in text
    assert "no entry for" not in text


# ---- the finish gate -------------------------------------------------------------------------

def test_a_finish_whose_accounting_field_did_not_arrive_is_told_exactly_that(make_routine,
                                                                            scripted,
                                                                            monkeypatch):
    """The specimen's own payload (`accounting: null`), end to end: deferred once, with the
    absence named, and the run finishes when the field arrives.

    The verifier is stubbed — this is the ACCOUNTING rung's test, and a live judge would consume
    a scripted reply for a verdict it says nothing about.
    """
    from rsched.engine import verifier

    monkeypatch.setattr(verifier, "refuted", lambda loop, claims, summary: [])
    d = make_routine(slug="absentacc", workflow_md=DONE_WHEN_MD)
    summary = "**Process:** read the recipe. **Work:** figures checked, digest published."
    scripted([
        {"say": "Reading the recipe.", "kind": "read_file", "path": "main.md"},
        {**finish(summary=summary), "accounting": None},            # turns 84-95 → deferred
        {**finish(summary=summary),
         "accounting": ["d1 met: the figures match the ledger",
                        "d2 met: the digest is published and its link resolves"]},
    ])
    status, run_dir = run_routine(d, server_for(d), run_ts=TS)
    events, _ = read_events(run_dir / "transcript.jsonl")
    assert status == "ok"
    deferred = [e["payload"] for e in events if e["type"] == "observation"
                and e["payload"].get("accounting")]
    assert len(deferred) == 1
    assert deferred[0]["accounting"]["absent"] == ["d1", "d2"]
    assert deferred[0]["accounting"]["missing"] == []
    assert "no `accounting` field arrived" in deferred[0]["message"]
    assert "is incomplete" not in deferred[0]["message"]
    status_doc = json.loads((run_dir / "status.json").read_text(encoding="utf-8"))
    assert status_doc["accounting"][0] == "d1 met: the figures match the ledger"


# ---- the job checkpoint inherits it ------------------------------------------------------------

def test_a_job_checkpoint_whose_accounting_did_not_arrive_is_told_so_too(make_routine, scripted):
    """`jobops._checkpoint` answers for the JOB recipe's Done when through the same
    `accounting.problems`/`gaps` seam, so it inherits the distinction without its own rule."""
    d = make_routine(slug="absentjob")
    cfg = yaml.safe_load((d / "routine.yaml").read_text())
    cfg["capabilities"] = {"actions": [], "utils": [], "runs": "last", "jobs": "on"}
    cfg["permissions"] = []
    (d / "routine.yaml").write_text(yaml.safe_dump(cfg))
    jobs.save(d, {"version": 1, "deleted": [], "run": {}, "jobs": [
        {"id": "alpha", "title": "Alpha", "brief": "steward alpha", "state": "active"}]})
    (d / "jobs" / "alpha").mkdir(parents=True, exist_ok=True)
    (d / "jobs" / "alpha" / "main.md").write_text(
        "# Alpha\n\n## Done when\n\n- d1 — the alpha page is current\n")
    scripted([
        {"say": "Open it.", "kind": "job", "verb": "open", "id": "alpha"},
        {"say": "Checkpoint.", "kind": "job", "verb": "checkpoint", "id": "alpha",
         "outcome": "advanced", "summary": "x", "accounting": None},            # refused
        {"say": "Checkpoint.", "kind": "job", "verb": "checkpoint", "id": "alpha",
         "outcome": "advanced", "summary": "x",
         "accounting": ["d1 met: republished rev 4"]},
        finish(summary="alpha processed"),
    ])
    server = ServerConfig()
    server.routines_home = d.parent
    server.libraries_home = d.parent.parent / "lib"
    status, run_dir = run_routine(d, server, run_ts=TS)
    assert status == "ok"
    rows = [json.loads(line) for line in
            (run_dir / "transcript.jsonl").read_text(encoding="utf-8").splitlines()]
    refused = [r["payload"] for r in rows if r.get("type") == "observation"
               and r["payload"].get("rejected") and "accounting" in r["payload"].get("reason", "")]
    assert len(refused) == 1, refused
    assert "nothing was received for d1" in refused[0]["reason"]
    assert jobs.find(jobs.load(d), "alpha")["last"]["accounting"] == ["d1 met: republished rev 4"]


# ---- what the console RENDERS ------------------------------------------------------------------

def test_the_console_renders_the_absence_as_its_own_clause():
    """Lockstep with `accounting.problems` (the rule `test_accounting_floor` states): a category
    the engine reports and the console does not render is a finish deferred for a reason the
    operator cannot see — which is how this one would have been missed again."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "static" / "components"
    gaps = (root / "obs-accounting.js").read_text(encoding="utf-8")
    assert "found.absent" in gaps
    assert "arrived" in gaps                 # the word that makes it the right diagnosis
    head = (root / "obs-text.js").read_text(encoding="utf-8")
    assert "absent" in head                  # …and the row's headline says which rung it was
