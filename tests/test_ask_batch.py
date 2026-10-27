"""Several questions in ONE `ask_user`, and the CLASS each filed record carries (D212a).

Measured on 282 real question records before this existed: `mode: blocking` was used 0 times,
0% of answers arrived within an hour (median latency 2.97 h) — so "surfaced during the run"
can never mean "the run's process waits". What the store DID show is batching: 37% of runs
(60 of 163) already ask more than one question, up to 6, and the operator answers in sittings
that dispose of several at once. The asking side paid a turn and a card per question; one
action, one dispatch, N records is the win.

The CLASS is the operator's hybrid rule, verbatim — "Both axes: subject decides the default,
the run may override it when the work is genuinely blocked":

* a question carrying a typed access `request` defaults to `blocking`,
* a plain question defaults to `closing`,
* `blocks_work: true` overrides to `blocking`, per question.

It is orthogonal to `mode`, which keeps its own meaning (the process waits, with the ask
timeout) — a batch never blocks the process, whatever class its questions carry.
"""

import json

import jsonschema
import pytest

from rsched.config import ServerConfig, load_routine
from rsched.engine import askbatch
from rsched.engine.actions import validate_action
from rsched.engine.actionschema import ACTION_SCHEMA
from rsched.engine.budgets_config import Budgets
from rsched.engine.interact import handle_ask
from rsched.engine.run_context import RunContext
from rsched.engine.transcript import Transcript

TS = "20260708-070000"


def _ctx(routine_dir) -> RunContext:
    cfg, _problems = load_routine(routine_dir)
    assert cfg is not None
    run_dir = routine_dir / "runs" / TS
    run_dir.mkdir(parents=True)
    return RunContext(routine=cfg, server=ServerConfig(), registry=None, run_ts=TS,
                      run_dir=run_dir, transcript=Transcript(run_dir / "transcript.jsonl"),
                      budgets=Budgets.from_config(cfg.budgets))


def _loop(ctx):
    class _Loop:
        pass
    loop = _Loop()
    loop.ctx = ctx
    loop.dialog_qids = {}
    return loop


def _records(routine_dir) -> list[dict]:
    pending = sorted((routine_dir / "questions" / "pending").glob("*.json"))
    return [json.loads(p.read_text(encoding="utf-8")) for p in pending]


# ---- one action, one dispatch, N records --------------------------------------------------

def test_a_batch_files_one_record_per_question_sharing_a_batch_id(make_routine):
    d = make_routine(slug="batchask")
    ctx = _ctx(d)
    ctx.turn = 7

    obs = askbatch.handle(_loop(ctx), {
        "kind": "ask_user",
        "questions": [
            {"question": "Which folder do the invoices live in?", "options": ["a", "b"]},
            {"question": "Keep the Tuesday slot?", "default": "keep it"},
            {"question": "May I read the project folder?", "request": "fs-read:~/project"},
        ]}, poll_s=0.01)

    assert obs["kind"] == "ask_user"
    assert obs["count"] == 3
    rows = _records(d)
    assert len(rows) == 3, "one record per question — the Decisions page renders one card each"
    assert len({r["qid"] for r in rows}) == 3, "each record keeps its own qid"
    batches = {r["batch"] for r in rows}
    assert len(batches) == 1, f"every record shares ONE batch id, got {batches}"
    assert obs["batch"] == rows[0]["batch"]
    # the run is told which record each question became, so it can refer to them later —
    # in the order it ASKED them (the pending dir's filenames sort `-7.json` after `-7-2.json`)
    assert {f["qid"] for f in obs["filed"]} == {r["qid"] for r in rows}
    assert [f["question"] for f in obs["filed"]] == [
        "Which folder do the invoices live in?", "Keep the Tuesday slot?",
        "May I read the project folder?"]


def test_a_batch_never_blocks_the_process(make_routine):
    """0% of answers arrive within an hour: N records are filed and the run carries on."""
    d = make_routine(slug="batchdefer")
    ctx = _ctx(d)
    ctx.turn = 2

    obs = askbatch.handle(_loop(ctx), {
        "kind": "ask_user",
        "questions": [{"question": "One?"}, {"question": "Two?"}]}, poll_s=0.01)

    assert obs["mode"] == "deferred"
    assert all(r["mode"] == "deferred" for r in _records(d))


# ---- the class: subject decides the default, the run may override ------------------------

def test_the_subject_sets_the_default_class(make_routine):
    d = make_routine(slug="batchclass")
    ctx = _ctx(d)
    ctx.turn = 3

    askbatch.handle(_loop(ctx), {
        "kind": "ask_user",
        "questions": [
            {"question": "Which wording do you prefer?"},
            {"question": "May I use the Discord bot?", "request": "util:discord"},
        ]}, poll_s=0.01)

    by_q = {r["question"]: r for r in _records(d)}
    assert by_q["Which wording do you prefer?"]["class"] == "closing"
    assert by_q["May I use the Discord bot?"]["class"] == "blocking"


def test_the_run_may_override_one_question_to_blocking(make_routine):
    d = make_routine(slug="batchoverride")
    ctx = _ctx(d)
    ctx.turn = 4

    askbatch.handle(_loop(ctx), {
        "kind": "ask_user",
        "questions": [
            {"question": "Which of the two tones?"},
            {"question": "Which account do I bill this to?", "blocks_work": True},
        ]}, poll_s=0.01)

    by_q = {r["question"]: r for r in _records(d)}
    assert by_q["Which of the two tones?"]["class"] == "closing"
    assert by_q["Which account do I bill this to?"]["class"] == "blocking", (
        "the run's own override: without this answer it cannot do the work it was fired to do")


def test_a_single_question_ask_is_unchanged_but_for_its_class(make_routine):
    """The dominant live path: same observation, same record keys, plus the class fact."""
    d = make_routine(slug="singleask")
    ctx = _ctx(d)
    ctx.turn = 11

    obs = handle_ask(_loop(ctx), {"kind": "ask_user", "question": "Which city?",
                                  "mode": "deferred"}, poll_s=0.01)

    assert obs == {"kind": "ask_user", "qid": obs["qid"], "mode": "deferred"}
    (record,) = _records(d)
    assert set(record) == {"qid", "question", "options", "asked", "mode", "type", "class"}
    assert record["class"] == "closing"
    assert "batch" not in record, "a lone ask was not asked together with anything"


def test_a_single_question_ask_may_claim_blocking_too(make_routine):
    d = make_routine(slug="singleblocks")
    ctx = _ctx(d)
    ctx.turn = 12

    handle_ask(_loop(ctx), {"kind": "ask_user", "question": "Which API key should I use?",
                            "mode": "deferred", "blocks_work": True}, poll_s=0.01)

    (record,) = _records(d)
    assert record["class"] == "blocking"


# ---- the schema declares the fields, so the applier can see them -------------------------

def test_the_schema_carries_the_batch_fields(make_routine):
    """`ACTION_SCHEMA` is additionalProperties: false — an undeclared sub-field is stripped
    silently, with no error and no log line, and the applier never sees it.
    """
    action = {"say": "two questions", "kind": "ask_user",
              "questions": [{"question": "One?", "options": ["a", "b"], "default": "a",
                             "blocks_work": True},
                            {"question": "Two?", "request": "secret:FOO_KEY",
                             "config_patch": {"budgets": {"max_turns": 100}}}],
              "blocks_work": False}
    jsonschema.Draft202012Validator(ACTION_SCHEMA).validate(action)
    assert validate_action(action) == []


def test_a_batch_has_a_brief_and_an_observation_that_say_it_is_several():
    """`BRIEF_FIELD["ask_user"]` is `question`, which a batch does not carry — so every turn
    record, compaction digest and reminder match would have read an EMPTY brief.
    """
    from rsched.engine.actionschema import brief_value
    from rsched.engine.obs_admin import format_admin

    action = {"say": "s", "kind": "ask_user",
              "questions": [{"question": "Which folder?"}, {"question": "Which account?"}]}
    assert brief_value(action) == "Which folder? (+1 more)"

    line = format_admin({"kind": "ask_user", "batch": "qb-20260708-070000-3", "mode": "deferred",
                         "count": 3, "blocking": 1, "filed": []}, "ask_user")
    assert "3 questions filed as one batch" in line
    assert "1 marked blocking" in line
    assert "filed as deferred" not in line, "a batch is N records, not one"


@pytest.mark.parametrize(("action", "needle"), [
    ({"say": "s", "kind": "ask_user"}, "question"),
    ({"say": "s", "kind": "ask_user", "question": "a?",
      "questions": [{"question": "b?"}]}, "not both"),
    ({"say": "s", "kind": "ask_user", "mode": "blocking",
      "questions": [{"question": "b?"}]}, "batch"),
    ({"say": "s", "kind": "ask_user", "questions": []}, "1-"),
    ({"say": "s", "kind": "ask_user", "questions": [{"question": "  "}]}, "question"),
    ({"say": "s", "kind": "ask_user",
      "questions": [{"question": "b?", "options": ["a", "b", "c", "d", "e", "f"]}]}, "options"),
])
def test_the_grammar_refuses_an_incoherent_batch(action, needle):
    problems = validate_action(action)
    assert problems, f"expected a refusal naming {needle!r}"
    assert any(needle in p for p in problems), problems
