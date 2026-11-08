"""D220 step 3 — the check that moves a proof on, and the one predicate that can run today.

A proof nobody checks is a promise, which is what the decision ledger already had. So the
lifecycle needs a CHECK: for each answered decision whose chosen option sits `in_progress`,
run its predicate and write `positive` (it landed) or `negative` (performed, and the proof is
still absent — his "unexpected event" state).

The registry is deliberately small. One predicate is implementable from what a decision record
already carries and nothing else: `config_patch_live` — the decision proposed a `config_patch`,
so the proof is that the live config holds those values. A predicate id with no entry in the
registry is `unknown` and leaves the state ALONE: a check that cannot run must never be read as
a check that failed, which would turn every unimplemented predicate into a false alarm.
"""

from __future__ import annotations

import json

import pytest
import yaml

from rsched import proofcheck, proofs


def _routine(tmp_path, slug="demo", cfg=None):
    rdir = tmp_path / slug
    (rdir / "questions" / "pending").mkdir(parents=True)
    (rdir / "routine.yaml").write_text(yaml.safe_dump(cfg or {"budgets": {"max_turns": 400}}),
                                       encoding="utf-8")
    return rdir


def _answered(rdir, qid, proof, choice, *, patch=None):
    """A record as it stands after the operator answered — what the checker reads."""
    record = {"qid": qid, "question": "Raise it?", "options": list(proof),
              "answered": True, "answer": choice,
              "proof": proofs.answered(proofs.filed(proof), choice)}
    if patch is not None:
        record["config_patch"] = patch
    path = rdir / "questions" / "answered" / f"{qid}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")
    return record


_PATCH_PROOF = {
    "a: raise it to 500": {"predicate": "config_patch_live",
                           "reads": "routine.yaml", "category": "operator_act"},
    "b: leave it at 400": {"predicate": "config_patch_live",
                           "reads": "routine.yaml", "category": "operator_act"},
}


def test_a_patch_that_is_live_proves_the_decision_positive(tmp_path):
    rdir = _routine(tmp_path, cfg={"budgets": {"max_turns": 500}})
    _answered(rdir, "q-1", _PATCH_PROOF, "a: raise it to 500",
              patch={"budgets": {"max_turns": 500}})

    moved = proofcheck.sweep_routine(rdir)

    assert moved == 1
    record = json.loads((rdir / "questions" / "answered" / "q-1.json").read_text(encoding="utf-8"))
    assert record["proof"]["a: raise it to 500"]["state"] == proofs.POSITIVE
    assert proofs.unproven(record) is False


def test_a_patch_that_never_landed_is_negative_not_merely_open(tmp_path):
    """His state for an unexpected event: the answer was recorded, the act was his, and the
    live value still says otherwise. That is the case nothing notices today."""
    rdir = _routine(tmp_path, cfg={"budgets": {"max_turns": 400}})
    _answered(rdir, "q-1", _PATCH_PROOF, "a: raise it to 500",
              patch={"budgets": {"max_turns": 500}})

    assert proofcheck.sweep_routine(rdir) == 1
    record = json.loads((rdir / "questions" / "answered" / "q-1.json").read_text(encoding="utf-8"))
    assert record["proof"]["a: raise it to 500"]["state"] == proofs.NEGATIVE
    assert proofs.unproven(record) is True
    assert proofs.louder_than_open(record) is True


def test_an_unknown_predicate_leaves_the_state_alone(tmp_path):
    """A check that cannot run must never read as a check that failed — otherwise every
    predicate nobody has implemented yet would raise a false alarm on his page."""
    rdir = _routine(tmp_path)
    proof = {"a: ship it": {"predicate": "release_names_item",
                            "reads": "state/shipped.jsonl", "category": "routine_act"}}
    _answered(rdir, "q-1", proof, "a: ship it")

    assert proofcheck.sweep_routine(rdir) == 0
    record = json.loads((rdir / "questions" / "answered" / "q-1.json").read_text(encoding="utf-8"))
    assert record["proof"]["a: ship it"]["state"] == proofs.IN_PROGRESS
    assert "checked_ts" not in record["proof"]["a: ship it"]
    assert proofs.unproven(record) is True            # still owed, and still visible


def test_a_decision_with_no_proof_is_not_touched(tmp_path):
    """Every decision the fleet has ever answered. The sweep must not rewrite their records
    at all — a rewritten archive would re-stamp settled history on the day this shipped."""
    rdir = _routine(tmp_path)
    path = rdir / "questions" / "answered" / "q-1.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    before = {"qid": "q-1", "question": "?", "answered": True, "answer": "yes"}
    path.write_text(json.dumps(before), encoding="utf-8")

    assert proofcheck.sweep_routine(rdir) == 0
    assert json.loads(path.read_text(encoding="utf-8")) == before


def test_a_proof_already_positive_is_not_re_checked(tmp_path):
    rdir = _routine(tmp_path, cfg={"budgets": {"max_turns": 500}})
    _answered(rdir, "q-1", _PATCH_PROOF, "a: raise it to 500",
              patch={"budgets": {"max_turns": 500}})
    assert proofcheck.sweep_routine(rdir) == 1
    first = json.loads((rdir / "questions" / "answered" / "q-1.json").read_text(encoding="utf-8"))

    assert proofcheck.sweep_routine(rdir) == 0        # nothing moved the second time
    again = json.loads((rdir / "questions" / "answered" / "q-1.json").read_text(encoding="utf-8"))
    assert again["proof"]["a: raise it to 500"]["checked_ts"] == \
        first["proof"]["a: raise it to 500"]["checked_ts"]


def test_a_negative_proof_is_re_checked_and_can_turn_positive(tmp_path):
    """He applies the patch a day later. The row must clear itself — otherwise the loud
    state is permanent and he learns to ignore it."""
    rdir = _routine(tmp_path, cfg={"budgets": {"max_turns": 400}})
    _answered(rdir, "q-1", _PATCH_PROOF, "a: raise it to 500",
              patch={"budgets": {"max_turns": 500}})
    assert proofcheck.sweep_routine(rdir) == 1        # negative

    (rdir / "routine.yaml").write_text(yaml.safe_dump({"budgets": {"max_turns": 500}}),
                                       encoding="utf-8")
    assert proofcheck.sweep_routine(rdir) == 1        # and now positive
    record = json.loads((rdir / "questions" / "answered" / "q-1.json").read_text(encoding="utf-8"))
    assert record["proof"]["a: raise it to 500"]["state"] == proofs.POSITIVE


def test_a_nested_patch_is_compared_key_by_key_not_as_a_whole(tmp_path):
    """A patch is a PATCH: the live config holds much more than it names, and a whole-object
    comparison would call every real answer negative."""
    rdir = _routine(tmp_path, cfg={"budgets": {"max_turns": 500, "max_minutes": 240},
                                   "schedule": {"friendly": {"frequency": "daily"}}})
    _answered(rdir, "q-1", _PATCH_PROOF, "a: raise it to 500",
              patch={"budgets": {"max_turns": 500}})

    assert proofcheck.sweep_routine(rdir) == 1
    record = json.loads((rdir / "questions" / "answered" / "q-1.json").read_text(encoding="utf-8"))
    assert record["proof"]["a: raise it to 500"]["state"] == proofs.POSITIVE


def test_a_patch_for_another_routine_is_read_from_that_routines_config(tmp_path):
    """D123/F458: a decision may propose a change to ANOTHER routine. Reading the asker's own
    config would prove the wrong thing — and would read as negative forever."""
    asker = _routine(tmp_path, "asker")
    _routine(tmp_path, "subject", cfg={"budgets": {"max_turns": 500}})
    record = _answered(asker, "q-1", _PATCH_PROOF, "a: raise it to 500",
                       patch={"budgets": {"max_turns": 500}})
    record["config_target"] = "subject"
    (asker / "questions" / "answered" / "q-1.json").write_text(json.dumps(record),
                                                               encoding="utf-8")

    assert proofcheck.sweep_routine(asker, routines_home=tmp_path) == 1
    out = json.loads((asker / "questions" / "answered" / "q-1.json").read_text(encoding="utf-8"))
    assert out["proof"]["a: raise it to 500"]["state"] == proofs.POSITIVE


def test_the_registry_says_what_each_predicate_reads(tmp_path):
    """A surface must be able to tell the operator what is being checked, and a routine
    filing a decision must be able to see which predicates exist."""
    assert "config_patch_live" in proofcheck.PREDICATES
    assert proofcheck.known("config_patch_live") is True
    assert proofcheck.known("something_nobody_built") is False
    with pytest.raises(KeyError):
        proofcheck.run_predicate("something_nobody_built", {}, routines_home=tmp_path)
