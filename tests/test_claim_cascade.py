"""The cascade where it acts (D302): a screened routine whose claims all clear the threshold
skips the prose judge, every other case reaches it, and the stream still records the pair —
flagged so a skipped judge is never read as an unanswered one.
"""

from __future__ import annotations

from types import SimpleNamespace

from rsched.engine import claim_screen, verifier

SCREENED = "folder-reorg"            # in the operator's six
EXCLUDED = "global-utils-review"     # in his three


class _Completion:
    def __init__(self, parsed):
        self.parsed = parsed
        self.usage = {"in": 10, "out": 5}
        self.text = ""


def _loop(tmp_path, slug, parsed=None):
    """A loop stub that COUNTS prose-judge calls — the thing the cascade is meant to avoid."""
    calls: list[dict] = []

    class _Endpoint:
        def complete(self, messages, **kw):
            calls.append({"messages": messages, **kw})
            return _Completion(parsed if parsed is not None else {"verdicts": []})

    ctx = SimpleNamespace(
        routine=SimpleNamespace(dir=tmp_path, models={}, efforts={}, slug=slug), phase="",
        registry=SimpleNamespace(for_model=lambda kind, models, efforts: (
            _Endpoint(), SimpleNamespace(model="m", effort="", temperature=0.0,
                                         max_tokens=1000))),
        add_usage=lambda u: None)
    return SimpleNamespace(ctx=ctx, messages=[
        {"role": "assistant", "content": "I ran the checksum and it matched"}], calls=calls)


def _claims(*texts):
    return [{"id": f"d{i}", "text": t} for i, t in enumerate(texts, 1)]


def _screen(monkeypatch, probabilities):
    """Stub the typed screen and capture what the shadow was handed."""
    recorded: list[dict] = []
    monkeypatch.setattr(verifier, "_screen", lambda *a, **k: dict(probabilities))
    monkeypatch.setattr(verifier, "_shadow_claims",
                        lambda *a, **k: recorded.append(dict(k)))
    return recorded


def test_a_screened_routine_whose_claims_all_clear_the_bar_skips_the_prose_judge(tmp_path,
                                                                                 monkeypatch):
    high = claim_screen.THRESHOLD + 0.01
    recorded = _screen(monkeypatch, {"d1": high, "d2": 0.99})
    loop = _loop(tmp_path, SCREENED)

    assert verifier.refuted(loop, _claims("a", "b"), "summary") == []

    assert loop.calls == [], "the prose judge was called for a finish the screen answered for"
    assert recorded and recorded[0]["judge_skipped"] is True


def test_one_claim_below_the_bar_sends_the_whole_finish_to_the_judge(tmp_path, monkeypatch):
    """The judge is one call over all the claims, so a partial skip saves nothing and loses
    its view of the rest."""
    high = claim_screen.THRESHOLD + 0.01
    recorded = _screen(monkeypatch, {"d1": high, "d2": 0.4})
    loop = _loop(tmp_path, SCREENED)

    verifier.refuted(loop, _claims("a", "b"), "summary")

    assert len(loop.calls) == 1
    assert recorded and recorded[0]["judge_skipped"] is False


def test_an_excluded_routine_reaches_the_judge_however_confident_the_screen_is(tmp_path,
                                                                               monkeypatch):
    _screen(monkeypatch, {"d1": 1.0})
    loop = _loop(tmp_path, EXCLUDED)

    verifier.refuted(loop, _claims("a"), "summary")

    assert len(loop.calls) == 1


def test_a_routine_the_table_does_not_name_reaches_the_judge(tmp_path, monkeypatch):
    _screen(monkeypatch, {"d1": 1.0})
    loop = _loop(tmp_path, "deficit-coach")

    verifier.refuted(loop, _claims("a"), "summary")

    assert len(loop.calls) == 1


def test_a_screen_that_could_not_answer_reaches_the_judge(tmp_path, monkeypatch):
    """No decision model, a provider error, a malformed answer: the cascade is never quieter
    than the judge it replaces."""
    _screen(monkeypatch, {})
    loop = _loop(tmp_path, SCREENED)

    verifier.refuted(loop, _claims("a"), "summary")

    assert len(loop.calls) == 1


def test_the_skipped_finish_still_reaches_the_stream_so_the_table_stays_revisable(tmp_path,
                                                                                  monkeypatch):
    """The table is only revisable while the stream it was measured from keeps growing, and a
    skipped judge must be distinguishable from a judge that answered nothing."""
    high = claim_screen.THRESHOLD + 0.01
    recorded = _screen(monkeypatch, {"d1": high})
    loop = _loop(tmp_path, SCREENED)

    verifier.refuted(loop, _claims("a"), "summary")

    assert recorded[0]["screen"] == {"d1": high}
    assert recorded[0]["judge_skipped"] is True


def test_the_recorded_row_says_the_judge_was_skipped(tmp_path, monkeypatch):
    """Through the REAL shadow, not a stub: a row where the cascade answered must be
    distinguishable from one where the judge answered nothing, or every later agreement
    figure silently includes claims nothing was compared against."""
    import json
    from types import SimpleNamespace as NS

    from rsched.endpoints.decisions import Decision, DecisionAnswer
    from rsched.engine import decision_shadow as shadow

    high = claim_screen.THRESHOLD + 0.01
    answered = Decision(
        answers=[DecisionAnswer(name="c0", type="yes_no",
                                probabilities={"yes": high, "no": 1 - high}, probability=high)],
        usage={"in": 10, "out": 2}, model="gemini-decision", provider="otari")
    monkeypatch.setattr(shadow, "_ask_many", lambda *a, **k: answered)
    ctx = NS(server=NS(routines_home=tmp_path, decision_models={"gemini-decision": object()},
                       decision_model="gemini-decision", decision_media_model=""),
             routine=NS(slug=SCREENED, models={}), run_id="20261121-140004")

    shadow.compare_claims(ctx, question="q", evidence="e",
                          claims=[{"id": "d1", "text": "x"}], chat_supported={},
                          screen={"d1": high}, judge_skipped=True)

    rows = [json.loads(line) for line
            in (tmp_path / ".control" / shadow.SHADOW_FILE).read_text().splitlines() if line]
    assert rows[0]["judge_skipped"] is True
    assert rows[0]["chat_verdict"] is None, "no judge ran, so there is no chat verdict"
    assert rows[0]["probability"] == high, "the screen's own answer is what was recorded"


def test_the_screen_and_the_shadow_ask_the_very_same_question(tmp_path):
    """The table was measured from probabilities answered to one wording; acting on
    probabilities answered to another would compare two different quantities."""
    import inspect

    src = inspect.getsource(verifier)
    assert src.count("SCREEN_QUESTION") >= 3, (
        "the screen and the shadow must both use verifier.SCREEN_QUESTION")
