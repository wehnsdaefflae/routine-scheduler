"""Stopping-condition verification, v2 of F334/D98.

v1 proves a run ACCOUNTED for its conditions; it cannot prove the account is true. v2 has a
second model check each `met` claim against the run's own transcript.

Most of what is pinned here is the two ways v2 could be worse than the problem it solves:
**false blocks** (so it is fail-open at every level — an unavailable endpoint, an unparseable
answer, an unmentioned condition and an uncertain judge all ACCEPT) and a **livelock** (so a
condition is challenged at most once per run, after which the model's verdict stands and the
disagreement is recorded instead). An enforcement that can hang a run is not enforcement.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from rsched.endpoints.base import EndpointError
from rsched.engine import stopping, verifier


class _Completion:
    def __init__(self, parsed):
        self.parsed = parsed
        self.usage = {"in": 10, "out": 5}
        self.text = ""


def _loop(tmp_path, parsed=None, *, raises=None):
    """A loop stub whose tool_call endpoint returns `parsed` (or raises)."""
    calls: list[dict] = []

    class _Endpoint:
        def complete(self, messages, **kw):
            calls.append({"messages": messages, **kw})
            if raises is not None:
                raise raises
            return _Completion(parsed)

    ctx = SimpleNamespace(
        routine=SimpleNamespace(dir=tmp_path, models={}), phase="",
        registry=SimpleNamespace(for_model=lambda kind, models: (
            _Endpoint(), SimpleNamespace(model="m", effort="", temperature=0.0,
                                         max_tokens=1000))),
        add_usage=lambda u: None)
    return SimpleNamespace(ctx=ctx, messages=[
        {"role": "system", "content": "SYSTEM PROMPT — must not be sent to the judge"},
        {"role": "assistant", "content": "I ran the checksum and it matched"}], calls=calls)


def _conditions(tmp_path, texts):
    stopping.save(tmp_path, {"conditions": [{"text": t} for t in texts]}, now="t")


# ---- the happy path ---------------------------------------------------------------------------

def test_a_refuted_claim_is_returned_with_its_objection(tmp_path):
    _conditions(tmp_path, ["the PDF is verified"])
    loop = _loop(tmp_path, {"verdicts": [
        {"id": "s1", "supported": False, "evidence": "no action ever opened the PDF"}]})
    got = verifier.refuted(loop, "[s1] met — PDF verified")
    assert len(got) == 1
    assert got[0]["id"] == "s1" and got[0]["text"] == "the PDF is verified"
    assert got[0]["evidence"] == "no action ever opened the PDF"


def test_a_supported_claim_is_not_returned(tmp_path):
    _conditions(tmp_path, ["the PDF is verified"])
    loop = _loop(tmp_path, {"verdicts": [
        {"id": "s1", "supported": True, "evidence": "the checksum action matched"}]})
    assert verifier.refuted(loop, "[s1] met — PDF verified") == []


def test_only_met_claims_are_checked(tmp_path):
    """An `unmet` claim needs no judge — the run is already saying it did not do it, and
    paying a subcall to agree is waste."""
    _conditions(tmp_path, ["a", "b"])
    loop = _loop(tmp_path, {"verdicts": []})
    verifier.refuted(loop, "[s1] met — did it\n[s2] unmet — blocked")
    sent = loop.calls[0]["messages"][0]["content"]
    # scoped to the CONDITIONS block: the full summary is shown to the judge as context, so
    # s2 legitimately appears there — what matters is that it is not put up for judgement
    block = sent.split("CONDITIONS THE AGENT CLAIMS ARE MET:")[1].split("ITS FINISH SUMMARY")[0]
    assert "[s1]" in block and "[s2]" not in block


def test_nothing_to_check_costs_no_subcall(tmp_path):
    """Most runs have no goal at all; they must pay nothing for v2."""
    loop = _loop(tmp_path, {"verdicts": []})
    assert verifier.refuted(loop, "just a summary") == []
    assert loop.calls == []


def test_the_judge_never_sees_the_system_prompt(tmp_path):
    """It is asked one bounded question about a transcript — not handed the run's harness."""
    _conditions(tmp_path, ["a"])
    loop = _loop(tmp_path, {"verdicts": []})
    verifier.refuted(loop, "[s1] met — done")
    sent = loop.calls[0]["messages"][0]["content"]
    assert "must not be sent to the judge" not in sent
    assert "I ran the checksum and it matched" in sent      # the conversation IS the evidence


# ---- fail-open: every uncertainty accepts ------------------------------------------------------

def test_an_unavailable_endpoint_accepts_the_run_s_word(tmp_path):
    _conditions(tmp_path, ["a"])
    loop = _loop(tmp_path, raises=EndpointError("provider down"))
    assert verifier.refuted(loop, "[s1] met — done") == []


def test_an_unparseable_answer_accepts(tmp_path):
    _conditions(tmp_path, ["a"])
    assert verifier.refuted(_loop(tmp_path, None), "[s1] met — done") == []
    assert verifier.refuted(_loop(tmp_path, "not a dict"), "[s1] met — done") == []
    assert verifier.refuted(_loop(tmp_path, {"verdicts": "junk"}), "[s1] met — done") == []


def test_a_condition_the_judge_did_not_mention_accepts(tmp_path):
    """Silence is not a refutation."""
    _conditions(tmp_path, ["a", "b"])
    loop = _loop(tmp_path, {"verdicts": [{"id": "s1", "supported": True, "evidence": "ok"}]})
    assert verifier.refuted(loop, "[s1] met — x\n[s2] met — y") == []


def test_only_an_explicit_false_refutes(tmp_path):
    """`supported: null`/missing is uncertainty, and uncertainty accepts."""
    _conditions(tmp_path, ["a"])
    for verdict in ({"id": "s1", "evidence": "hmm"},
                    {"id": "s1", "supported": None, "evidence": "hmm"},
                    {"id": "s1", "supported": "false", "evidence": "hmm"}):
        loop = _loop(tmp_path, {"verdicts": [verdict]})
        assert verifier.refuted(loop, "[s1] met — done") == [], verdict


def test_a_refutation_of_an_unclaimed_condition_is_ignored(tmp_path):
    """The judge cannot widen the check to conditions the run never claimed."""
    _conditions(tmp_path, ["a", "b"])
    loop = _loop(tmp_path, {"verdicts": [
        {"id": "s2", "supported": False, "evidence": "made up"}]})
    assert verifier.refuted(loop, "[s1] met — done") == []


def test_a_dormant_condition_is_never_checked(tmp_path):
    """It is not demanded in the accounting, so it cannot be judged either."""
    stopping.save(tmp_path, {"conditions": [
        {"id": "s1", "text": "gate"},
        {"id": "s2", "text": "gated", "requires": ["s1"]}]}, now="t")
    loop = _loop(tmp_path, {"verdicts": [
        {"id": "s2", "supported": False, "evidence": "not done"}]})
    assert verifier.refuted(loop, "[s1] met — did it\n[s2] met — did it") == []


def test_the_prompt_tells_the_judge_to_be_generous(tmp_path):
    """The instruction IS the false-block defence — a tail reader that treats absence of
    evidence as evidence of absence strands finished jobs."""
    _conditions(tmp_path, ["a"])
    loop = _loop(tmp_path, {"verdicts": []})
    verifier.refuted(loop, "[s1] met — done")
    sent = loop.calls[0]["messages"][0]["content"]
    assert "absence of evidence is NOT" in sent
    assert "Be generous." in sent
    assert "A wrong `false` strands a finished job" in sent


def test_the_challenge_message_says_the_check_can_be_wrong(tmp_path):
    """A run told only "you are wrong" argues; one told how to overrule can proceed."""
    msg = verifier.challenge_message([{"id": "s1", "text": "verify it",
                                       "evidence": "never opened"}])
    assert "[s1] verify it" in msg and "never opened" in msg
    assert "which it can be" in msg                      # the objection is fallible, and says so
    assert "You will not be asked twice" in msg          # ...and how to end the exchange


# ---- the store side of a standing disagreement --------------------------------------------------

def test_a_disputed_verdict_still_lands_and_keeps_the_objection(tmp_path):
    """The model keeps the last word — an engine that could veto it forever would hang the
    run, which is the outcome stopping conditions exist to replace."""
    _conditions(tmp_path, ["verify it"])
    stopping.record_accounting(tmp_path, "[s1] met — I did verify it", run_id="r:1", now="t",
                               disputes={"s1": "no action opened the file"})
    row = stopping.load(tmp_path)["conditions"][0]
    assert row["status"] == "met"                        # the verdict stands
    assert row["disputed"] == "no action opened the file"


def test_an_undisputed_verdict_carries_no_objection(tmp_path):
    _conditions(tmp_path, ["verify it"])
    stopping.record_accounting(tmp_path, "[s1] met — done", run_id="r:1", now="t")
    assert stopping.load(tmp_path)["conditions"][0]["disputed"] == ""


def test_a_dispute_is_engine_owned_and_survives_a_user_save(tmp_path):
    _conditions(tmp_path, ["verify it"])
    stopping.record_accounting(tmp_path, "[s1] met — done", run_id="r:1", now="t",
                               disputes={"s1": "objection"})
    stopping.save(tmp_path, {"conditions": [{"id": "s1", "text": "verify it properly",
                                             "status": "met"}]}, now="t")
    row = stopping.load(tmp_path)["conditions"][0]
    assert row["text"] == "verify it properly" and row["disputed"] == "objection"


@pytest.mark.parametrize("claimed", ["met", "unmet"])
def test_read_accounting_and_the_verifier_agree_on_what_a_claim_is(tmp_path, claimed):
    """One parser, so the gate, the writer and the judge can never disagree about which
    conditions a summary claimed."""
    _conditions(tmp_path, ["a"])
    loop = _loop(tmp_path, {"verdicts": []})
    verifier.refuted(loop, f"[s1] {claimed} — because")
    assert bool(loop.calls) is (claimed == "met")
