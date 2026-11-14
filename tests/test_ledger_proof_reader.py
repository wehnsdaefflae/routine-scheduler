"""A ledger decision's per-option PROOF reaches the Decisions page (R3417).

The step between two routines, and it had nobody on either side of it. `self-audit`'s ledger
writer now refuses to file a decision without a proof per option — one entry per option, each
carrying `predicate`, `reads` and `category`, with the five states derived exactly as
`proofs.answered` derives them. But `web/decisions_read` built its question items from id,
options, status and detail alone, so a proof on a ledger row reached **no surface at all**: the
Unproven band shipped in 0.520.0 covered engine-filed asks and not the ledger decisions, which
are precisely the ones that sit answered-and-unbuilt for weeks.

What done looks like, in this half: an answered ledger decision with an `in_progress` proof
renders in the Unproven band rather than the Settled one. Only then may `in_progress` leave
`_DECIDED_STATUSES` — the sequencing D220's closure named, which until now was one step short.

The two properties that keep it safe are tested here as hard as the feature:
  - a row with NO proof is unchanged, because `proofs.unproven` is False without one and the
    instance's 157 legacy rows are all in that state. Re-opening them is the F659 regression.
  - a MALFORMED proof is dropped rather than served: the ledger is another routine's file, and
    a proof the engine cannot read would park a row in a band no predicate could ever clear.
"""

from __future__ import annotations

import json

from rsched import proofs
from rsched.web.decisions_ledger import _audit_decisions_fresh, _ledger_proof

OPTIONS = ["build the reader", "leave as-is"]


def _proof(state: str = proofs.OPEN) -> dict:
    """A well-formed two-option proof map, in the shape self-audit files."""
    return {
        "build the reader": {"predicate": "file_contains", "reads": "web/decisions_read.py",
                             "category": "routine_act", "state": state},
        "leave as-is": {"predicate": "never", "reads": "nothing",
                        "category": "routine_act", "state": proofs.DECLINED},
    }


def _report(rdir, decision: dict) -> None:
    (rdir / "audit").mkdir(parents=True, exist_ok=True)
    (rdir / "audit" / "report.json").write_text(json.dumps({
        "generated": "2026-11-14T03:00:00+01:00", "decisions": [decision], "findings": []}),
        encoding="utf-8")


def _row(rdir, decision: dict) -> dict:
    _report(rdir, decision)
    rows = _audit_decisions_fresh(rdir)
    assert len(rows) == 1, rows
    return rows[0]


def test_a_well_formed_proof_reaches_the_question_item(tmp_path):
    """Without this the map is in the ledger and on no surface."""
    row = _row(tmp_path, {"id": "D900", "title": "Build it?", "status": "open",
                          "options": OPTIONS, "proof": _proof()})
    assert row["proof"]["build the reader"]["predicate"] == "file_contains"
    assert row["proof"]["build the reader"]["category"] == "routine_act"


def test_an_answered_decision_with_an_in_progress_proof_reads_as_unproven(tmp_path):
    """The whole point, stated as the page's own predicate: answered, and nothing yet shows the
    work landed. `proofs.unproven` is what the console's `unproven()` mirrors."""
    rdir = tmp_path
    (rdir / "audit").mkdir(parents=True, exist_ok=True)
    (rdir / "audit" / "decisions-answered.json").write_text(
        json.dumps({"D900": "2026-11-14T04:00:00+01:00"}), encoding="utf-8")
    row = _row(rdir, {"id": "D900", "title": "Build it?", "status": "open", "options": OPTIONS,
                      "proof": _proof(proofs.IN_PROGRESS)})
    assert row["answered"] is True
    assert proofs.unproven(row) is True, row
    assert proofs.state_of(row) == proofs.IN_PROGRESS


def test_a_positive_proof_clears_the_row(tmp_path):
    """The other side of the same predicate: once an option is proven, the decision is done and
    must leave the band — otherwise the band fills up and stops being read."""
    rdir = tmp_path
    (rdir / "audit").mkdir(parents=True, exist_ok=True)
    (rdir / "audit" / "decisions-answered.json").write_text(
        json.dumps({"D900": "2026-11-14T04:00:00+01:00"}), encoding="utf-8")
    row = _row(rdir, {"id": "D900", "title": "Build it?", "status": "open", "options": OPTIONS,
                      "proof": _proof(proofs.POSITIVE)})
    assert proofs.unproven(row) is False, row


def test_a_row_without_a_proof_is_untouched(tmp_path):
    """Every one of the instance's 157 legacy decision rows is in this state, and `unproven` is
    False without a proof — which is what stops this reader re-opening settled history (F659).
    """
    row = _row(tmp_path, {"id": "D901", "title": "Older", "status": "open", "options": OPTIONS})
    assert "proof" not in row
    assert proofs.unproven({**row, "answered": True}) is False


def test_a_malformed_proof_is_dropped_rather_than_served(tmp_path):
    """The ledger is another routine's file. A proof the engine cannot read is worse than none:
    it would park the row in a band whose state no predicate could ever clear.
    """
    for broken in (
        {"build the reader": {"predicate": "x", "reads": "y"}},            # no category
        {"build the reader": {"predicate": "x", "reads": "y", "category": "unclear"}},
        {"build the reader": "a sentence about it"},                        # not a map
        {"an option the decision does not offer": {"predicate": "x", "reads": "y",
                                                   "category": "routine_act"}},
        {},
    ):
        assert _ledger_proof({"proof": broken}, OPTIONS) == {}, broken
        row = _row(tmp_path, {"id": "D902", "title": "Broken", "status": "open",
                              "options": OPTIONS, "proof": broken})
        assert "proof" not in row, broken


def test_an_in_progress_row_with_a_proof_is_shown_and_reads_as_unproven(tmp_path):
    """The second half, and the one D220's closure named: `in_progress` stops HIDING a row that
    carries a proof.

    A row self-audit marks `in_progress` is one it says it is already building. Without a proof
    that is a claim no surface can check, which is why the status hides the row at all. WITH one
    it is checkable, and the honest place for "answered, and nothing shows it landed" is the
    Unproven band. Its answer is the ledger's own `resolution`: the marker gate cannot help here
    because an answer recorded IN a report is older than the report recording it.
    """
    row = _row(tmp_path, {"id": "D903", "title": "Reroute it?", "status": "in_progress",
                          "options": OPTIONS, "proof": _proof(proofs.IN_PROGRESS),
                          "resolution": "build the reader"})
    assert row["answered"] is True
    assert row["answer"] == "build the reader", row["answer"]
    assert proofs.unproven(row) is True


def test_an_in_progress_row_without_a_proof_stays_hidden(tmp_path):
    """The F659 guard, and the reason `in_progress` cannot simply leave `_DECIDED_STATUSES`:
    156 decided rows on this instance carry no proof, and un-hiding them would re-present
    already-addressed decisions as answerable cards (six did, on 2026-10-10, and one came back
    answered the OPPOSITE way).
    """
    _report(tmp_path, {"id": "D904", "title": "Older work", "status": "in_progress",
                       "options": OPTIONS, "resolution": "build the reader"})
    assert _audit_decisions_fresh(tmp_path) == []


def test_a_proof_does_not_un_hide_the_other_decided_statuses(tmp_path):
    """Only `in_progress` is re-opened by a proof. `addressed` and `dropped` are disposals and
    `settled` is a disposal too: a proof on one of those says the work is DONE, so re-presenting
    it would be the same regression wearing a proof.
    """
    for status in ("addressed", "dropped", "settled", "closed", "shipped"):
        _report(tmp_path, {"id": "D905", "title": "Disposed", "status": status,
                           "options": OPTIONS, "proof": _proof(proofs.IN_PROGRESS),
                           "resolution": "build the reader"})
        assert _audit_decisions_fresh(tmp_path) == [], status


def test_the_reader_validates_through_the_engines_own_module(tmp_path):
    """Not a second implementation: `self-audit` mirrors `proofs.REQUIRED`/`CATEGORIES`
    deliberately so the two cannot disagree, and the reader must check against the same module
    rather than re-stating its rules — or the disagreement comes back through the back door.
    """
    missing_one = {opt: {k: v for k, v in entry.items() if k != "reads"}
                   for opt, entry in _proof().items()}
    assert proofs.problems(missing_one, OPTIONS), "the module itself must reject this"
    assert _ledger_proof({"proof": missing_one}, OPTIONS) == {}
