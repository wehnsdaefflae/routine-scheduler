"""The CLOSING asks a finishing run leaves behind — D212 increment (b).

Increment (a) made every decision record carry its CLASS and nothing read it. These pin the
first reader: which of a run's own records reach the person reading its finish, and — just as
much — which never do.
"""

from __future__ import annotations

from types import SimpleNamespace

from rsched.engine import askclass, closingasks
from rsched.engine.inbox_questions import file_question


def _ctx(tmp_path, run_ts: str = "20261101-140001"):
    (tmp_path / "questions" / "pending").mkdir(parents=True)
    (tmp_path / "inbox").mkdir()
    return SimpleNamespace(run_ts=run_ts, routine=SimpleNamespace(dir=tmp_path, slug="r"))


def _ask(tmp_path, qid: str, question: str = "which shape next time?", **kw):
    return file_question(tmp_path, qid, question, kw.pop("options", []),
                         "2026-11-01T15:00:00+01:00", **kw)


def test_a_run_with_no_asks_has_nothing_to_show(tmp_path):
    assert closingasks.for_run(_ctx(tmp_path)) == []


def test_a_closing_ask_of_this_run_reaches_its_finish(tmp_path):
    ctx = _ctx(tmp_path)
    _ask(tmp_path, "q-20261101-140001-7", "keep doing X?", options=["yes", "no"],
         default="I keep doing X")
    [ask] = closingasks.for_run(ctx)
    assert ask == {"qid": "q-20261101-140001-7", "question": "keep doing X?",
                   "options": ["yes", "no"], "default": "I keep doing X"}


def test_a_blocking_ask_never_rides_the_finish(tmp_path):
    """The whole point of the axis. A blocking question is one the run could NOT work without;
    it belongs on the Decisions page while the run is still going, not in a banner saying the
    run finished.
    """
    ctx = _ctx(tmp_path)
    _ask(tmp_path, "q-20261101-140001-3", "may I write there?", request=["fs-write:/srv"])
    assert closingasks.for_run(ctx) == []
    # and the record really does carry that class, so the test is about the class and not
    # about the request key
    _ask(tmp_path, "q-20261101-140001-4", "blocked on this", blocks_work=True)
    assert closingasks.for_run(ctx) == []


def test_another_runs_ask_is_not_this_runs_to_advertise(tmp_path):
    """A qid is `q-<run-ts>-<turn>` (engine/interact._free_qid) and that prefix is how
    `inbox_questions.take_answer` already attributes a record. The fleet's open questions are
    the Decisions page's surface, not this one's.
    """
    ctx = _ctx(tmp_path)
    _ask(tmp_path, "q-20261031-140002-11", "an earlier run's question")
    assert closingasks.for_run(ctx) == []


def test_an_ask_already_answered_is_not_offered_again(tmp_path):
    """`open_questions` flags a record whose answer waits in the inbox. Between the ask and the
    finish is exactly when the operator answers on the Decisions page, so this is the common
    case rather than an edge one.
    """
    ctx = _ctx(tmp_path)
    _ask(tmp_path, "q-20261101-140001-9", "answered meanwhile?")
    (tmp_path / "inbox" / "answer-q-20261101-140001-9.json").write_text(
        '{"text": "yes"}', encoding="utf-8")
    assert closingasks.for_run(ctx) == []


def test_a_record_written_before_the_class_existed_reads_as_closing(tmp_path):
    """Increment (a)'s own instruction to its readers: a record filed before it has no `class`
    key, and a reader must default a missing one to `closing` rather than hide it. The
    conservative direction — it costs a reader one glance instead of vanishing.
    """
    ctx = _ctx(tmp_path)
    path = _ask(tmp_path, "q-20261101-140001-5", "old shape")
    import json
    rec = json.loads(path.read_text(encoding="utf-8"))
    del rec["class"]
    path.write_text(json.dumps(rec), encoding="utf-8")
    assert closingasks.of_record(rec) == askclass.CLOSING
    assert [a["qid"] for a in closingasks.for_run(ctx)] == ["q-20261101-140001-5"]


def test_an_unknown_class_value_reads_as_closing_too(tmp_path):
    assert closingasks.of_record({"class": "nonsense"}) == askclass.CLOSING
    assert closingasks.of_record({"class": askclass.BLOCKING}) == askclass.BLOCKING


def test_only_the_fields_a_banner_needs_are_carried(tmp_path):
    """The record carries a dozen keys that belong to the Decisions page's rendering (a config
    patch, a report id, an access request's entities). The finish offers the question, its
    options and what happens without an answer; the one answer route does the rest from the qid.
    """
    ctx = _ctx(tmp_path)
    _ask(tmp_path, "q-20261101-140001-2", "pick one", options=["a", "b"],
         config_patch={"budgets": {"max_turns": 10}}, report="R999")
    [ask] = closingasks.for_run(ctx)
    assert set(ask) == {"qid", "question", "options"}
    assert "config_patch" not in ask and "report" not in ask


def test_a_store_that_cannot_be_read_never_fails_a_finish(tmp_path):
    """A surface that only ADDS something to read must not be able to end a run badly."""
    ctx = SimpleNamespace(run_ts="20261101-140001",
                          routine=SimpleNamespace(dir=tmp_path / "gone", slug="r"))
    assert closingasks.for_run(ctx) == []


def test_the_asks_keep_their_ask_order(tmp_path):
    ctx = _ctx(tmp_path)
    for n in (2, 10, 3):
        _ask(tmp_path, f"q-20261101-140001-{n}", f"question {n}")
    assert [a["question"] for a in closingasks.for_run(ctx)] == [
        "question 10", "question 2", "question 3"]
