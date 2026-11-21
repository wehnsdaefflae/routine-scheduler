"""An `ask_user` may carry a PROOF per option, and it reaches the decision record (D220, #4).

`rsched/proofs.py` and the daily sweep (`rsched/proofcheck.py`) existed with no way in: nothing
the engine files ever carried a proof. The `proof` field is that way in — checked at validation,
carried to `file_question(..., proof=…)`, and an unknown predicate NAMED in the observation.
"""

import json

import jsonschema

from rsched import proofs
from rsched.config import ServerConfig, load_routine
from rsched.engine import askbatch
from rsched.engine.actions import validate_action
from rsched.engine.actionschema import ACTION_SCHEMA
from rsched.engine.budgets_config import Budgets
from rsched.engine.interact import handle_ask
from rsched.engine.observations import format_observation
from rsched.engine.run_context import RunContext
from rsched.engine.transcript import Transcript

TS = "20261121-070000"
PATCH = {"budgets": {"max_turns": 90}}
LIVE = {"yes": {"predicate": "config_patch_live", "reads": "routine.yaml budgets.max_turns",
                "category": "operator_act"},
        "no": {"predicate": "config_patch_live", "reads": "routine.yaml budgets.max_turns",
               "category": "operator_act"}}


def _ctx(routine_dir) -> RunContext:
    cfg, _problems = load_routine(routine_dir)
    assert cfg is not None
    run_dir = routine_dir / "runs" / TS
    run_dir.mkdir(parents=True)
    ctx = RunContext(routine=cfg, server=ServerConfig(), registry=None, run_ts=TS,
                     run_dir=run_dir, transcript=Transcript(run_dir / "transcript.jsonl"),
                     budgets=Budgets.from_config(cfg.budgets))
    ctx.turn = 4
    return ctx


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


def _ask(**fields) -> dict:
    return {"kind": "ask_user", "say": "asking", **fields}


def test_the_schema_carries_proof_on_a_single_ask_and_on_a_batch_entry():
    jsonschema.validate(_ask(question="Raise it?", options=["yes", "no"],
                             config_patch=PATCH, proof=LIVE), ACTION_SCHEMA)
    jsonschema.validate(_ask(questions=[{"question": "Raise it?", "options": ["yes", "no"],
                                         "config_patch": PATCH, "proof": LIVE}]), ACTION_SCHEMA)


def test_a_proof_reaches_the_record_every_option_open(make_routine):
    d = make_routine(slug="proofask")
    ctx = _ctx(d)
    action = _ask(question="Raise the turn budget to 90?", options=["yes", "no"],
                  config_patch=PATCH, proof=LIVE)
    assert validate_action(action) == []

    obs = handle_ask(_loop(ctx), action, poll_s=0.01)

    [record] = _records(d)
    assert {o: e["state"] for o, e in record["proof"].items()} == {"yes": proofs.OPEN,
                                                                   "no": proofs.OPEN}
    assert record["proof"]["yes"]["predicate"] == "config_patch_live"
    text = format_observation(obs)
    assert "PROOF: filed per option" in text and "daily sweep checks" in text


def test_an_unknown_predicate_is_filed_and_named_in_the_observation(make_routine):
    """Not refused — only that routine can compute it — but the run must hear that the sweep
    will not check it for it."""
    d = make_routine(slug="proofmine")
    ctx = _ctx(d)
    mine = {o: {"predicate": "release_names_item", "reads": "CHANGELOG.md",
                "category": "routine_act"} for o in ("ship", "hold")}
    action = _ask(question="Ship 0.6?", options=["ship", "hold"], proof=mine)
    assert validate_action(action) == []

    text = format_observation(handle_ask(_loop(ctx), action, poll_s=0.01))

    assert _records(d)[0]["proof"]["ship"]["state"] == proofs.OPEN
    assert "`release_names_item` is not a predicate this instance checks" in text
    assert "config_patch_live" in text and "show the work landed yourself" in text


def test_a_record_without_a_proof_is_unchanged(make_routine):
    d = make_routine(slug="proofnone")
    obs = handle_ask(_loop(_ctx(d)), _ask(question="Fine?", options=["a", "b"]), poll_s=0.01)
    assert "proof" not in _records(d)[0]
    assert "PROOF" not in format_observation(obs)


def test_a_malformed_proof_is_refused_at_validation_naming_what_is_wrong():
    no_options = validate_action(_ask(question="Raise it?", config_patch=PATCH, proof=LIVE))
    assert any("keyed by OPTION" in p for p in no_options)

    missing = validate_action(_ask(question="Raise it?", options=["yes", "no"],
                                   config_patch=PATCH, proof={"yes": LIVE["yes"]}))
    assert any("option 'no' has no proof" in p for p in missing)

    thin = validate_action(_ask(question="Q?", options=["a"],
                                proof={"a": {"predicate": "x", "category": "someone"}}))
    assert any("reads" in p for p in thin)
    assert any("category 'someone'" in p for p in thin)


def test_config_patch_live_without_a_patch_is_refused():
    """It would read negative forever — an unexpected event on his page that nothing caused."""
    problems = validate_action(_ask(question="Raise it?", options=["yes", "no"], proof=LIVE))
    assert any("this question carries none" in p for p in problems)


def test_a_batch_carries_each_entrys_proof_and_says_so_once(make_routine):
    d = make_routine(slug="proofbatch")
    ctx = _ctx(d)
    action = _ask(questions=[
        {"question": "Raise it?", "options": ["yes", "no"], "config_patch": PATCH,
         "proof": LIVE},
        {"question": "Keep Tuesday?", "options": ["keep", "drop"]}])
    assert validate_action(action) == []

    obs = askbatch.handle(_loop(ctx), action, poll_s=0.01)

    by_q = {r["question"]: r for r in _records(d)}
    assert set(by_q["Raise it?"]["proof"]) == {"yes", "no"}
    assert "proof" not in by_q["Keep Tuesday?"]
    assert format_observation(obs).count("PROOF:") == 1


def test_a_batch_refuses_a_proof_riding_the_action_instead_of_its_entry():
    problems = validate_action(_ask(proof=LIVE, questions=[
        {"question": "Raise it?", "options": ["yes", "no"], "config_patch": PATCH}]))
    assert any("belong to EACH question" in p for p in problems)
