"""D220 — a decision's option carries a PROOF, and recording an answer is not the end of it.

The operator, 2026-10-29: *"You need to come up with a proof at creation and accept / decline
my response based on the result of you checking the proof"*. His five states and the rule that
makes them matter: a decision stays open, visible and open to his feedback until at least one
option has a POSITIVE proof, or it is actively discarded or overridden.

These tests pin the vocabulary and the state machine. The surfaces that RENDER it and the
periodic check that moves `in_progress` on are later increments; what is pinned here is that
the mechanism cannot be filed wrong, cannot silently clear a row, and tells the asking run
what will be checked.
"""

from __future__ import annotations

import pytest

from rsched import proofs

_PROOF = {
    "a: ship it": {"predicate": "release_names_item",
                   "reads": "state/shipped.jsonl", "category": "routine_act"},
    "b: leave it": {"predicate": "item_row_dropped",
                    "reads": "audit/report.json", "category": "routine_act"},
}


def _answered(choice: str) -> dict:
    """A record as it stands after the operator picked `choice`."""
    return {"qid": "q-1", "options": list(_PROOF), "answered": True,
            "proof": proofs.answered(proofs.filed(_PROOF), choice)}


def test_every_option_needs_a_predicate_what_it_reads_and_its_category():
    """Required AT FILING: a proof invented after the fact is the thing this decision
    exists to prevent. Each complaint names the option and the missing field."""
    assert proofs.problems(_PROOF, list(_PROOF)) == []

    missing = proofs.problems({"a: ship it": {"predicate": "release_names_item"}},
                              ["a: ship it"])
    assert len(missing) == 1
    assert "reads" in missing[0] and "category" in missing[0]

    uncovered = proofs.problems({"a: ship it": _PROOF["a: ship it"]},
                                ["a: ship it", "b: leave it"])
    assert any("b: leave it" in p for p in uncovered)

    bad_cat = proofs.problems(
        {"a: ship it": {**_PROOF["a: ship it"], "category": "whatever"}}, ["a: ship it"])
    assert any("whatever" in p for p in bad_cat)
    # his OWN split, not a free vocabulary: an act only he can perform, or one of mine
    assert set(proofs.CATEGORIES) == {"operator_act", "routine_act"}


def test_a_filed_proof_starts_open_and_an_answer_moves_exactly_one_option_on():
    """His words: `open` = an option offered, not yet selected; `in_progress` = selected,
    the actions not yet performed; `declined` = an option not selected."""
    filed = proofs.filed(_PROOF)
    assert {spec["state"] for spec in filed.values()} == {proofs.OPEN}
    assert filed["a: ship it"]["predicate"] == "release_names_item"   # the spec is kept

    after = proofs.answered(filed, "a: ship it")
    assert after["a: ship it"]["state"] == proofs.IN_PROGRESS
    assert after["b: leave it"]["state"] == proofs.DECLINED
    assert after["a: ship it"]["reads"] == "state/shipped.jsonl"


def test_a_free_text_answer_selects_no_option_and_so_proves_nothing():
    """He answers in prose more often than he picks (D220 itself was answered that way).
    Nothing may read that as a selection — and nothing may read it as unanswered either."""
    after = proofs.answered(proofs.filed(_PROOF), "do whichever is cheaper")
    assert {spec["state"] for spec in after.values()} == {proofs.OPEN}
    assert proofs.unproven({"answered": True, "proof": after}) is True


def test_only_a_positive_proof_or_an_override_stops_the_decision_being_unproven():
    """The load-bearing rule: recording his answer is NOT what settles the item."""
    record = _answered("a: ship it")
    assert proofs.unproven(record) is True
    assert proofs.state_of(record) == proofs.IN_PROGRESS

    checked = {**record, "proof": proofs.checked(record["proof"], "a: ship it", found=True)}
    assert checked["proof"]["a: ship it"]["state"] == proofs.POSITIVE
    assert proofs.unproven(checked) is False
    assert proofs.state_of(checked) == proofs.POSITIVE

    negative = {**record, "proof": proofs.checked(record["proof"], "a: ship it", found=False)}
    assert negative["proof"]["a: ship it"]["state"] == proofs.NEGATIVE
    assert proofs.unproven(negative) is True          # performed, and the proof is ABSENT


def test_an_override_is_his_act_and_says_who_and_when():
    """*"an operator override that a proof has been verified"*, gated behind an are-you-sure
    step in the UI — so the record must say it was overridden, never look self-proven."""
    record = _answered("a: ship it")
    over = proofs.overridden(record["proof"], "a: ship it",
                             who="operator", when="2026-11-08T15:04:00+01:00")
    assert over["a: ship it"]["state"] == proofs.POSITIVE
    assert over["a: ship it"]["overridden_by"] == "operator"
    assert over["a: ship it"]["overridden_ts"] == "2026-11-08T15:04:00+01:00"
    assert proofs.unproven({**record, "proof": over}) is False
    # and it is distinguishable from a proof that was actually checked
    assert proofs.overridden_option(over) == "a: ship it"
    assert proofs.overridden_option(
        proofs.checked(record["proof"], "a: ship it", found=True)) == ""


def test_a_negative_proof_renders_louder_than_an_unanswered_one():
    """His ordering: `negative` flags unexpected events, so it outranks `open` on any
    surface. A positive one outranks everything, because it CLEARS the row."""
    filed = proofs.filed(_PROOF)
    assert proofs.state_of({"proof": filed}) == proofs.OPEN
    mixed = proofs.checked(proofs.answered(filed, "a: ship it"), "a: ship it", found=False)
    assert proofs.state_of({"proof": mixed}) == proofs.NEGATIVE
    assert proofs.louder_than_open({"proof": mixed}) is True
    assert proofs.louder_than_open({"proof": filed}) is False
    # one positive is enough, whatever the others say
    both = proofs.checked(mixed, "b: leave it", found=True)
    assert proofs.state_of({"proof": both}) == proofs.POSITIVE


def test_a_record_with_no_proof_at_all_is_never_called_unproven():
    """Every ask the engine files today carries no proof, and must keep behaving exactly as
    it does — otherwise this mechanism would re-open the whole fleet's settled history."""
    assert proofs.unproven({"answered": True}) is False
    assert proofs.unproven({"answered": True, "proof": {}}) is False
    assert proofs.state_of({}) == ""
    assert proofs.louder_than_open({}) is False
    assert proofs.unproven({"proof": proofs.filed(_PROOF)}) is False   # not answered yet


def test_the_asking_run_is_told_its_answer_is_conditional_and_on_what():
    """The line a RUN reads under its answer: what will be checked, and that the decision is
    not settled by the answer. A run that reads "yes" and plans as if the item were closed is
    the failure this sentence prevents."""
    pair = proofs.carry({"question": "Ship it?", "answer": "a: ship it"},
                        _answered("a: ship it"))
    line = proofs.digest_line(pair)
    assert "release_names_item" in line
    assert "state/shipped.jsonl" in line
    assert proofs.digest_suffix(pair).startswith("\n  ")
    # nothing to say about an answer whose decision carried no proof
    assert proofs.digest_line({"question": "?", "answer": "yes"}) == ""
    assert proofs.digest_suffix({"question": "?", "answer": "yes"}) == ""


def test_checking_or_overriding_an_option_the_decision_never_offered_is_refused():
    """A typo in a predicate id would otherwise write a proof state nobody asked for, onto a
    record that looks proven afterwards."""
    filed = proofs.filed(_PROOF)
    with pytest.raises(KeyError):
        proofs.checked(filed, "c: something else", found=True)
    with pytest.raises(KeyError):
        proofs.overridden(filed, "c: something else", who="operator", when="now")
