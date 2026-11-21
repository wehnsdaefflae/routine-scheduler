"""D287 step 1: a decision model answers the SAME typed question, beside the chat model."""

from __future__ import annotations

import json
from pathlib import Path
from typing import ClassVar

import pytest

from rsched.endpoints.decisions import Decision, DecisionAnswer
from rsched.engine import decision_shadow as shadow


class _FakeServer:
    def __init__(self, home: Path, *, models: dict | None = None):
        self.routines_home = home
        self.decision_models = models if models is not None else {"gemini-decision": object()}
        self.decision_model = "gemini-decision"
        self.decision_media_model = ""


class _FakeRoutine:
    slug = "a-routine"
    models: ClassVar[dict] = {}


class _FakeCtx:
    def __init__(self, home: Path, **kw):
        self.server = _FakeServer(home, **kw)
        self.routine = _FakeRoutine()
        self.run_id = "20261114-140002"


def _rows(home: Path) -> list[dict]:
    p = home / ".control" / shadow.SHADOW_FILE
    if not p.is_file():
        return []
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line]


def _answered(prob: float) -> Decision:
    return Decision(answers=[DecisionAnswer(name="refusal", type="yes_no",
                                            probabilities={"yes": prob, "no": 1 - prob},
                                            probability=prob)],
                    usage={"in": 10, "out": 2}, model="gemini-decision", provider="otari")


# ---- 1. the verdict is never touched ------------------------------------------------------

@pytest.mark.parametrize("chat_said", [True, False])
def test_the_shadow_returns_the_chat_models_verdict_unchanged(tmp_path, monkeypatch, chat_said):
    """The whole point of step 1: his 2026-08-22 constraint still governs the path, so what the
    run acts on is the chat model's answer and nothing else."""
    monkeypatch.setattr(shadow, "_ask", lambda *a, **k: _answered(0.99))
    ctx = _FakeCtx(tmp_path)
    assert shadow.compare(ctx, site="refusal", question="Is this a refusal?",
                          evidence="some reply", chat_verdict=chat_said) is chat_said


def test_a_disagreement_is_recorded_rather_than_acted_on(tmp_path, monkeypatch):
    monkeypatch.setattr(shadow, "_ask", lambda *a, **k: _answered(0.97))
    ctx = _FakeCtx(tmp_path)
    assert shadow.compare(ctx, site="refusal", question="q", evidence="e",
                          chat_verdict=False) is False
    row = _rows(tmp_path)[0]
    assert row["chat_verdict"] is False
    assert row["decision_verdict"] is True
    assert row["agree"] is False


# ---- 2. a measurement may never fail a run ------------------------------------------------

def test_no_decision_model_configured_records_nothing_and_raises_nothing(tmp_path):
    ctx = _FakeCtx(tmp_path, models={})
    assert shadow.compare(ctx, site="refusal", question="q", evidence="e",
                          chat_verdict=True) is True
    assert _rows(tmp_path) == []


def test_an_endpoint_failure_is_swallowed_and_noted_not_raised(tmp_path, monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError("provider said 400")

    monkeypatch.setattr(shadow, "_ask", _boom)
    ctx = _FakeCtx(tmp_path)
    assert shadow.compare(ctx, site="refusal", question="q", evidence="e",
                          chat_verdict=True) is True
    row = _rows(tmp_path)[0]
    assert row["decision_verdict"] is None
    assert "400" in row["error"]
    assert row["agree"] is None, "an unanswered comparison is not an agreement"


def test_an_answer_with_no_probability_reads_as_unanswered(tmp_path, monkeypatch):
    """A malformed answer must not be read as `no`: an absent verdict and a negative verdict
    are different facts, and conflating them is how a measurement lies in the safe direction."""
    monkeypatch.setattr(shadow, "_ask",
                        lambda *a, **k: Decision(answers=[], usage={"in": 1, "out": 0}))
    ctx = _FakeCtx(tmp_path)
    assert shadow.compare(ctx, site="refusal", question="q", evidence="e",
                          chat_verdict=True) is True
    assert _rows(tmp_path)[0]["decision_verdict"] is None


def test_an_unwritable_control_dir_still_returns_the_verdict(tmp_path, monkeypatch):
    monkeypatch.setattr(shadow, "_ask", lambda *a, **k: _answered(0.9))
    monkeypatch.setattr(shadow, "append_jsonl",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("read-only fs")))
    ctx = _FakeCtx(tmp_path)
    assert shadow.compare(ctx, site="refusal", question="q", evidence="e",
                          chat_verdict=False) is False


# ---- 3. the row carries what makes the swap decidable -------------------------------------

def test_the_row_carries_both_answers_the_probability_and_its_provenance(tmp_path, monkeypatch):
    monkeypatch.setattr(shadow, "_ask", lambda *a, **k: _answered(0.84))
    ctx = _FakeCtx(tmp_path)
    shadow.compare(ctx, site="refusal", question="Is this a refusal?",
                   evidence="I can't help with that.", chat_verdict=True)
    row = _rows(tmp_path)[0]
    for key in ("ts", "site", "routine", "run_id", "chat_verdict", "decision_verdict",
                "agree", "probability", "model", "usage"):
        assert key in row, f"the row omits {key!r}, so the comparison cannot be folded later"
    assert row["probability"] == pytest.approx(0.84)
    assert row["agree"] is True
    assert row["site"] == "refusal"
    assert row["routine"] == "a-routine"


def test_the_probability_is_kept_because_the_argmax_is_what_hid_the_instability(tmp_path,
                                                                               monkeypatch):
    """D287's own measurement: permuting options moved jev's mass ~10 points while the argmax
    never changed. A row carrying only the verdict would reproduce exactly that blindness."""
    monkeypatch.setattr(shadow, "_ask", lambda *a, **k: _answered(0.51))
    ctx = _FakeCtx(tmp_path)
    shadow.compare(ctx, site="refusal", question="q", evidence="e", chat_verdict=True)
    row = _rows(tmp_path)[0]
    assert row["probability"] == pytest.approx(0.51)
    assert row["decision_verdict"] is True
    assert 0.4 < row["probability"] < 0.6, "a near-coin-flip must be visible AS one"


def test_the_threshold_is_named_in_the_row_so_a_reread_can_rescore_it(tmp_path, monkeypatch):
    """The verdict is P(yes) against a threshold. If the row recorded only the boolean, a later
    reader could not ask what a different threshold would have decided — which is the first
    question anyone folding this stream will have."""
    monkeypatch.setattr(shadow, "_ask", lambda *a, **k: _answered(0.6))
    ctx = _FakeCtx(tmp_path)
    shadow.compare(ctx, site="refusal", question="q", evidence="e", chat_verdict=True)
    assert _rows(tmp_path)[0]["threshold"] == pytest.approx(shadow.THRESHOLD)


# ---- 4. the evidence goes through the one helper that works on this instance --------------

def test_structured_evidence_is_announced_as_text_before_it_is_sent(tmp_path, monkeypatch):
    """F888's lesson, pinned at the one new site that could repeat it: a dict was refused by
    `otari-decisions` five times (HTTP 400), and the byte-exact announced string was answered
    by all three of this instance's models. 0.535.2 announced it in the `openai` adapter — dead
    code here — and the live path stayed broken for nine days under a green pin.
    """
    seen: dict = {}

    def _spy(_ctx, question, evidence, **_k):
        seen["evidence"] = evidence
        return _answered(0.2)

    monkeypatch.setattr(shadow, "_ask", _spy)
    ctx = _FakeCtx(tmp_path)
    shadow.compare(ctx, site="finish", question="Does the record support this claim?",
                   evidence={"claim": "the digest was published", "turns": 14},
                   chat_verdict=False)
    assert isinstance(seen["evidence"], str), (
        "structured evidence must be announced as TEXT by decisions.evidence_text before it "
        "reaches a protocol — a dict is refused by the protocol this instance actually uses")
    assert "the digest was published" in seen["evidence"]


# ---- the batch form: the shape the cascade will actually ship in -------------------------

def _batch(*probs: float | None) -> Decision:
    return Decision(answers=[DecisionAnswer(name=f"c{i}", type="yes_no",
                                            probabilities={}, probability=p)
                             for i, p in enumerate(probs)],
                    usage={"in": 40, "out": 6}, model="gemini-decision", provider="otari")


def test_every_claim_is_screened_in_one_call_and_recorded_separately(tmp_path, monkeypatch):
    """One evidence, many questions is what these models are fastest at — and it is the shape
    the cascade his answer asks for would use, so the measurement must be taken in it."""
    calls: list[int] = []

    def _spy(_ctx, questions, _evidence):
        calls.append(len(questions))
        return _batch(0.9, 0.1, 0.6)

    monkeypatch.setattr(shadow, "_ask_many", _spy)
    ctx = _FakeCtx(tmp_path)
    claims = [{"id": "d1", "text": "the digest was published"},
              {"id": "d2", "text": "every link resolves"},
              {"id": "b1", "text": "the operator was told"}]
    shadow.compare_claims(ctx, question="Does the record support this claim?",
                          evidence={"summary": "s", "transcript_tail": "t"}, claims=claims,
                          chat_supported={"d1": True, "d2": True, "b1": False})
    assert calls == [3], "one call carrying three questions, not three calls"
    rows = _rows(tmp_path)
    assert [r["claim_id"] for r in rows] == ["d1", "d2", "b1"]
    assert [r["agree"] for r in rows] == [True, False, False]
    assert all(r["claims_in_call"] == 3 for r in rows)


def test_the_batchs_usage_is_booked_once_not_once_per_claim(tmp_path, monkeypatch):
    """A fold that sums `usage` over the rows must read the call's real cost. Repeating it per
    claim would report a batch of ten claims as ten calls — and cost is the number this whole
    decision turns on."""
    monkeypatch.setattr(shadow, "_ask_many", lambda *a, **k: _batch(0.9, 0.2))
    ctx = _FakeCtx(tmp_path)
    shadow.compare_claims(ctx, question="q", evidence="e",
                          claims=[{"id": "d1", "text": "x"}, {"id": "d2", "text": "y"}],
                          chat_supported={"d1": True, "d2": False})
    rows = _rows(tmp_path)
    assert rows[0]["usage"] == {"in": 40, "out": 6}
    assert rows[1]["usage"] == {}


def test_a_claim_the_batch_did_not_answer_is_unanswered_not_unsupported(tmp_path, monkeypatch):
    monkeypatch.setattr(shadow, "_ask_many", lambda *a, **k: _batch(0.9, None))
    ctx = _FakeCtx(tmp_path)
    shadow.compare_claims(ctx, question="q", evidence="e",
                          claims=[{"id": "d1", "text": "x"}, {"id": "d2", "text": "y"}],
                          chat_supported={"d1": True, "d2": True})
    rows = _rows(tmp_path)
    assert rows[1]["decision_verdict"] is None and rows[1]["agree"] is None
    assert "no probability" in rows[1]["error"]


def test_a_batch_failure_records_every_claim_as_unanswered_and_raises_nothing(tmp_path,
                                                                             monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError("provider said 400")

    monkeypatch.setattr(shadow, "_ask_many", _boom)
    ctx = _FakeCtx(tmp_path)
    shadow.compare_claims(ctx, question="q", evidence="e",
                          claims=[{"id": "d1", "text": "x"}], chat_supported={"d1": True})
    row = _rows(tmp_path)[0]
    assert row["decision_verdict"] is None and "400" in row["error"]


def test_no_claims_is_no_call_and_no_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(shadow, "_ask_many",
                        lambda *a, **k: pytest.fail("a call was made with nothing to screen"))
    shadow.compare_claims(_FakeCtx(tmp_path), question="q", evidence="e", claims=[],
                          chat_supported={})
    assert _rows(tmp_path) == []


# ---- both sites are wired, or the measurement measures nothing ---------------------------

def test_the_refusal_site_is_wired_to_the_shadow():
    """A module nothing calls records nothing — the failure mode of a measurement increment."""
    import inspect

    from rsched.engine import refusal

    src = inspect.getsource(refusal.is_refusal)
    assert "decision_shadow.compare" in src, (
        "is_refusal does not reach the shadow, so the refusal site is measured by nothing")


def test_the_finish_verifier_site_is_wired_to_the_shadow():
    import inspect

    from rsched.engine import verifier

    assert "compare_claims" in inspect.getsource(verifier)


def test_both_named_sites_are_the_ones_the_decision_authorizes():
    """`refusal · classify reply` (34 calls/month) and `finish · verify claims` (184 calls,
    24.5 s, $11.15) are the two sites the operator's answer names. A third would be scope the
    decision does not carry."""
    assert set(shadow.SITES) == {"refusal", "finish"}


def test_the_stream_is_the_durable_instance_wide_one():
    """Not the run dir: the question is whether the two models agree over hundreds of calls,
    and run dirs fall to retention (`readmodels/usage_stream.py` says the same of its own
    stream)."""
    assert shadow.SHADOW_FILE.endswith(".jsonl")
    assert "/" not in shadow.SHADOW_FILE, "the file lives in .control/, named by the writer"
