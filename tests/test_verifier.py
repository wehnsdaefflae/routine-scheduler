"""Claim verification: a second model checks each `met` the finish ACCOUNTING claims against
the run's own transcript (`engine/verifier.py`).

The accounting proves a run ANSWERED for every line it owes; it cannot prove the answer is true.
Most of what is pinned here is the two ways the check could be worse than the problem it solves:
**false blocks** (so it is fail-open at every level — an unavailable endpoint, an unparseable
answer, an unmentioned claim and an uncertain judge all ACCEPT) and a **livelock** (so a line is
challenged at most once per run, after which the model's verdict stands and the disagreement is
recorded instead). An enforcement that can hang a run is not enforcement.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from rsched.endpoints.base import EndpointError
from rsched.engine import verifier


class _Completion:
    def __init__(self, parsed, text=""):
        self.parsed = parsed
        self.usage = {"in": 10, "out": 5}
        self.text = text


def _loop(tmp_path, parsed=None, *, raises=None, text=""):
    """A loop stub whose tool_call endpoint returns `parsed` and `text` (or raises)."""
    calls: list[dict] = []

    class _Endpoint:
        def complete(self, messages, **kw):
            calls.append({"messages": messages, **kw})
            if raises is not None:
                raise raises
            return _Completion(parsed, text)

    ctx = SimpleNamespace(
        routine=SimpleNamespace(dir=tmp_path, models={}), phase="",
        registry=SimpleNamespace(for_model=lambda kind, models: (
            _Endpoint(), SimpleNamespace(model="m", effort="", temperature=0.0,
                                         max_tokens=1000))),
        add_usage=lambda u: None)
    return SimpleNamespace(ctx=ctx, messages=[
        {"role": "system", "content": "SYSTEM PROMPT — must not be sent to the judge"},
        {"role": "assistant", "content": "I ran the checksum and it matched"}], calls=calls)


def _claims(*texts, stage=""):
    return [{"id": f"d{i}", "text": t, "stage": stage} for i, t in enumerate(texts, 1)]


# ---- the happy path ---------------------------------------------------------------------------

def test_a_refuted_claim_is_returned_with_its_objection(tmp_path):
    loop = _loop(tmp_path, {"verdicts": [
        {"id": "d1", "supported": False, "evidence": "no action ever opened the PDF"}]})
    got = verifier.refuted(loop, _claims("the PDF is verified"), "PDF verified")
    assert got == [{"id": "d1", "text": "the PDF is verified",
                    "evidence": "no action ever opened the PDF"}]


def test_a_judge_that_answers_in_text_is_read(tmp_path):
    """Every OpenAI-compatible adapter returns schema output as the reply TEXT and leaves
    `parsed` empty. Reading `parsed` alone made the check a silent no-op on all of them: every
    `met` was accepted unread."""
    reply = json.dumps({"verdicts": [
        {"id": "d1", "supported": False, "evidence": "no action ever opened the PDF"}]})
    got = verifier.refuted(_loop(tmp_path, text=reply), _claims("the PDF is verified"), "x")
    assert [o["id"] for o in got] == ["d1"]
    fenced = f"Here is my judgement:\n```json\n{reply}\n```"
    assert verifier.refuted(_loop(tmp_path, text=fenced), _claims("the PDF is verified"), "x")


def test_a_supported_claim_is_not_returned(tmp_path):
    loop = _loop(tmp_path, {"verdicts": [
        {"id": "d1", "supported": True, "evidence": "the checksum action matched"}]})
    assert verifier.refuted(loop, _claims("the PDF is verified"), "done") == []


def test_every_claim_is_put_up_for_judgement(tmp_path):
    loop = _loop(tmp_path, {"verdicts": []})
    verifier.refuted(loop, _claims("a", "b"), "summary")
    sent = loop.calls[0]["messages"][0]["content"]
    block = sent.split("OUTCOMES THE AGENT CLAIMS ARE MET:")[1].split("ITS FINISH SUMMARY")[0]
    assert "[d1] a" in block and "[d2] b" in block


def test_nothing_to_check_costs_no_subcall(tmp_path):
    """Most finishes claim nothing met that needs checking; they must pay nothing for it."""
    loop = _loop(tmp_path, {"verdicts": []})
    assert verifier.refuted(loop, [], "just a summary") == []
    assert loop.calls == []


def test_the_judge_is_the_tool_call_role_never_the_main_model(tmp_path):
    asked: list[str] = []
    loop = _loop(tmp_path, {"verdicts": []})
    inner = loop.ctx.registry.for_model
    loop.ctx.registry.for_model = lambda kind, models: (asked.append(kind), inner(kind, models))[1]
    verifier.refuted(loop, _claims("a"), "done")
    assert asked == ["tool_call"]
    assert loop.calls[0]["purpose"] == "finish · verify claims"


def test_the_judge_never_sees_the_system_prompt(tmp_path):
    """It is asked one bounded question about a transcript — not handed the run's harness."""
    loop = _loop(tmp_path, {"verdicts": []})
    verifier.refuted(loop, _claims("a"), "done")
    sent = loop.calls[0]["messages"][0]["content"]
    assert "must not be sent to the judge" not in sent
    assert "I ran the checksum and it matched" in sent      # the conversation IS the evidence


# ---- the stage signal is a HINT, never a verdict (F620) ----------------------------------------

def test_an_unentered_stage_becomes_a_note_for_the_judge_not_an_objection(tmp_path):
    """It used to be a verdict of its own, raised with no model asked — and the blamed line was
    then excluded from the judge that would have looked at what the run did. Stage entry is
    measured from module re-reads and phase-cursor writes, so it was wrong for every run that
    already knew its procedure (F620/R2228)."""
    claims = [{"id": "d1", "text": "sources read", "stage": "gather"},
              {"id": "d2", "text": "published", "stage": "publish"},
              {"id": "d3", "text": "ledger written", "stage": ""}]
    got = verifier.hinted(claims, {"gather"})
    assert [c["id"] for c in got if c.get("hint")] == ["d2"]   # entered/stage-less: no note
    assert "never entered the stage `publish`" in got[1]["hint"]
    assert "judge it by the actions" in got[1]["hint"]   # it tells the reader what outranks it
    assert claims[1] == {"id": "d2", "text": "published", "stage": "publish"}  # non-mutating


def test_a_claim_whose_stage_was_never_entered_is_still_put_to_the_judge(tmp_path):
    """The point of F620: the line with the weak signal against it is exactly the line whose
    evidence must be read, not the one excluded from the reading."""
    loop = _loop(tmp_path, {"verdicts": []})
    claims = [{"id": "d1", "text": "the page is published", "stage": "publish"}]
    assert verifier.refuted(loop, verifier.hinted(claims, set()), "done") == []
    sent = loop.calls[0]["messages"][0]["content"]
    assert "[d1] the page is published" in sent
    assert "never entered the stage `publish`" in sent          # the hint rides the claim
    assert "Judge by the ACTIONS and their results" in sent


def test_a_claim_without_a_hint_carries_no_note(tmp_path):
    loop = _loop(tmp_path, {"verdicts": []})
    verifier.refuted(loop, verifier.hinted(_claims("a", stage="gather"), {"gather"}), "done")
    block = loop.calls[0]["messages"][0]["content"]
    assert "[d1] a" in block and "note:" not in block


# ---- the evidence: what the run DID ------------------------------------------------------------

def test_the_judge_reads_the_run_s_action_record(tmp_path):
    """The transcript tail is the last TAIL_CHARS of the message list, so on a long run the
    actions that produced an early outcome are no longer in it. `turn_records` survives
    compaction and is the only surface that still shows them (F620)."""
    loop = _loop(tmp_path, {"verdicts": []})
    loop.turn_records = [
        {"turn": 7, "kind": "util", "brief": "rutorrent-rpc erase Dark.Matter.S02E06",
         "say": "erasing the drained torrent"},
        {"turn": 8, "kind": "edit_file", "brief": "state/shows.json", "say": "pointer advanced"}]
    verifier.refuted(loop, _claims("the episode is drained and its pointer advanced"), "done")
    sent = loop.calls[0]["messages"][0]["content"]
    assert "WHAT IT DID — one line per turn" in sent
    assert "turn 7: util" in sent and "rutorrent-rpc erase Dark.Matter.S02E06" in sent
    assert "turn 8: edit_file" in sent and "pointer advanced" in sent


def test_a_run_with_no_action_record_simply_has_that_section_left_out(tmp_path):
    loop = _loop(tmp_path, {"verdicts": []})           # no turn_records attribute at all
    verifier.refuted(loop, _claims("a"), "done")
    assert "WHAT IT DID" not in loop.calls[0]["messages"][0]["content"]


def test_a_malformed_turn_record_is_skipped_rather_than_breaking_the_check(tmp_path):
    """It runs at the finish: anything raising here turns a finished run into a crashed one."""
    loop = _loop(tmp_path, {"verdicts": []})
    loop.turn_records = ["not a record", {"no": "turn key"},
                         {"turn": 3, "kind": "shell", "brief": "ls", "say": "looking"}]
    assert verifier.refuted(loop, _claims("a"), "done") == []
    assert "turn 3: shell" in loop.calls[0]["messages"][0]["content"]


def test_the_action_record_loses_its_middle_not_its_beginning(tmp_path):
    """An outcome's evidence is as often at turn 12 as at turn 120 — which is precisely what the
    conversation tail could not show."""
    loop = _loop(tmp_path, {"verdicts": []})
    loop.turn_records = (
        [{"turn": 1, "kind": "util", "brief": "FIRST-ACTION-MARKER", "say": "x" * 100}]
        + [{"turn": i, "kind": "shell", "brief": "filler", "say": "y" * 400}
           for i in range(2, 200)]
        + [{"turn": 200, "kind": "finish", "brief": "LAST-ACTION-MARKER", "say": "z" * 100}])
    verifier.refuted(loop, _claims("a"), "done")
    sent = loop.calls[0]["messages"][0]["content"]
    assert "FIRST-ACTION-MARKER" in sent and "LAST-ACTION-MARKER" in sent
    assert "elided from the middle" in sent
    assert len(sent.split("WHAT IT DID")[1]) < verifier.ACTIONS_CHARS + 2000


# ---- fail-open: every uncertainty accepts ------------------------------------------------------

def test_an_unavailable_endpoint_accepts_the_run_s_word(tmp_path):
    loop = _loop(tmp_path, raises=EndpointError("provider down"))
    assert verifier.refuted(loop, _claims("a"), "done") == []


def test_any_failure_of_the_subcall_accepts_rather_than_ending_the_run(tmp_path):
    """It runs at the finish — an exception escaping here would turn a finished run into a
    crashed one, with the summary unwritten."""
    loop = _loop(tmp_path, raises=RuntimeError("adapter bug"))
    assert verifier.refuted(loop, _claims("a"), "done") == []


def test_an_unparseable_answer_accepts(tmp_path):
    for parsed in (None, "not a dict", {"verdicts": "junk"}, {"verdicts": 5},
                   {"verdicts": None}):
        assert verifier.refuted(_loop(tmp_path, parsed), _claims("a"), "done") == [], parsed
    assert verifier.refuted(_loop(tmp_path, text="I think it is fine."), _claims("a"),
                            "done") == []


def test_a_claim_the_judge_did_not_mention_accepts(tmp_path):
    """Silence is not a refutation."""
    loop = _loop(tmp_path, {"verdicts": [{"id": "d1", "supported": True, "evidence": "ok"}]})
    assert verifier.refuted(loop, _claims("a", "b"), "done") == []


def test_only_an_explicit_false_refutes(tmp_path):
    """`supported: null`/missing is uncertainty, and uncertainty accepts."""
    for verdict in ({"id": "d1", "evidence": "hmm"},
                    {"id": "d1", "supported": None, "evidence": "hmm"},
                    {"id": "d1", "supported": "false", "evidence": "hmm"}):
        loop = _loop(tmp_path, {"verdicts": [verdict]})
        assert verifier.refuted(loop, _claims("a"), "done") == [], verdict


def test_a_refutation_of_an_unclaimed_line_is_ignored(tmp_path):
    """The judge cannot widen the check to lines the run never claimed."""
    loop = _loop(tmp_path, {"verdicts": [
        {"id": "d2", "supported": False, "evidence": "made up"}]})
    assert verifier.refuted(loop, _claims("a"), "done") == []


def test_a_refutation_without_evidence_still_says_why(tmp_path):
    loop = _loop(tmp_path, {"verdicts": [{"id": "d1", "supported": False, "evidence": " "}]})
    got = verifier.refuted(loop, _claims("a"), "done")
    assert got[0]["evidence"] == "the transcript does not show this being done"


def test_the_prompt_tells_the_judge_to_be_generous(tmp_path):
    """The instruction IS the false-block defence — a tail reader that treats absence of
    evidence as evidence of absence strands finished jobs."""
    loop = _loop(tmp_path, {"verdicts": []})
    verifier.refuted(loop, _claims("a"), "done")
    sent = loop.calls[0]["messages"][0]["content"]
    assert "absence of evidence is NOT" in sent
    assert "Be generous." in sent
    assert "A wrong `false` strands a finished job" in sent


def test_the_challenge_message_says_the_check_can_be_wrong(tmp_path):
    """A run told only "you are wrong" argues; one told how to overrule can proceed."""
    msg = verifier.challenge_message([{"id": "d1", "text": "verify it",
                                       "evidence": "never opened"}])
    assert "[d1] verify it" in msg and "never opened" in msg
    assert "which it can be" in msg                      # the objection is fallible, and says so
    assert "You will not be asked twice" in msg          # ...and how to end the exchange
