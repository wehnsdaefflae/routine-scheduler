"""Action schema + schema_guard: extraction tolerance, validation, semantic checks, retry text."""

import json

import jsonschema
import pytest

from rsched.engine.actions import ACTION_SCHEMA, KINDS, example_action, validate_action
from rsched.schema_guard import SchemaViolation, extract_json, parse_reply, retry_message


def test_schema_compiles_and_example_passes():
    jsonschema.Draft202012Validator.check_schema(ACTION_SCHEMA)
    obj = example_action()
    jsonschema.Draft202012Validator(ACTION_SCHEMA).validate(obj)
    assert validate_action(obj) == []


@pytest.mark.parametrize(
    "action",
    [
        {"say": "s", "kind": "util", "name": "websearch", "args": ["q", "--json"]},
        {"say": "s", "kind": "write_util", "name": "my-util", "content": "# script"},
        {"say": "s", "kind": "read_file", "path": "LEDGER.md", "max_lines": 40},
        {"say": "s", "kind": "write_file", "path": "state/x.json", "content": "{}", "append": False},
        {"say": "s", "kind": "llm", "prompt": "p", "system": "sys", "response_schema": {"type": "object"}},
        {"say": "s", "kind": "spawn", "prompt": "do x", "label": "research", "workflow": "general-task"},
        {"say": "s", "kind": "subruns"},
        {"say": "s", "kind": "kill", "n": 2},
        {"say": "s", "kind": "wait", "all": True, "timeout_s": 120},
        {"say": "s", "kind": "ask_user", "question": "q?", "mode": "blocking", "options": ["a", "b"]},
        {"say": "s", "kind": "finish", "status": "ok", "summary": "done"},
    ],
    ids=KINDS,
)
def test_valid_actions_pass_both_layers(action):
    assert parse_reply(json.dumps(action), ACTION_SCHEMA, validate_action) == action


@pytest.mark.parametrize(
    "action,fragment",
    [
        ({"say": "s", "kind": "util"}, "name"),
        ({"say": "s", "kind": "util", "name": "   "}, "name"),
        ({"say": "s", "kind": "write_util", "name": "x"}, "content"),
        ({"say": "s", "kind": "write_file", "path": "x"}, "content"),
        ({"say": "s", "kind": "finish", "status": "ok"}, "summary"),
        ({"say": "s", "kind": "ask_user", "question": "q?", "args": ["ls"]}, "do not belong"),
        ({"say": "s", "kind": "llm", "prompt": "p", "path": "x"}, "do not belong"),
    ],
)
def test_semantic_violations(action, fragment):
    problems = validate_action(action)
    assert problems and any(fragment in p for p in problems)


def test_schema_layer_rejects_unknown_kind_and_extra_props():
    with pytest.raises(SchemaViolation):
        parse_reply(json.dumps({"say": "s", "kind": "dance"}), ACTION_SCHEMA, validate_action)
    with pytest.raises(SchemaViolation):
        parse_reply(json.dumps({"say": "s", "kind": "util", "name": "ls", "extra": 1}),
                    ACTION_SCHEMA, validate_action)


def test_validate_action_enforces_workflow_allowlist():
    """A workflow's `tools:` allowlist narrows the action vocabulary; the error names the
    permitted kinds and `finish` is always allowed."""
    allowed = {"ask_user", "read_file", "write_file", "finish"}
    disallowed = {"say": "s", "kind": "util", "name": "websearch"}
    problems = validate_action(disallowed, allowed_kinds=allowed)
    assert len(problems) == 1 and "not available" in problems[0]
    for kind in sorted(allowed):
        assert kind in problems[0]                    # actionable: the allowed set is spelled out
    fin = {"say": "s", "kind": "finish", "status": "ok", "summary": "d"}
    assert validate_action(fin, allowed_kinds={"read_file"}) == []   # finish always permitted
    assert validate_action(disallowed) == []                          # None → unrestricted
    ok = {"say": "s", "kind": "read_file", "path": "LEDGER.md"}
    assert validate_action(ok, allowed_kinds=allowed) == []


def test_extract_json_tolerates_fences_and_prose():
    inner = {"say": "s", "kind": "finish", "status": "ok", "summary": "d"}
    fenced = f"Sure! Here is the action:\n```json\n{json.dumps(inner)}\n```\nHope that helps."
    assert extract_json(fenced) == inner
    prosed = f"I will finish now. {json.dumps(inner)} That is all."
    assert extract_json(prosed) == inner
    nested = json.dumps({"say": "uses {braces} inside", "kind": "util", "name": "echo"})
    assert extract_json("prefix " + nested) == json.loads(nested)


def test_extract_json_failure_and_retry_message():
    with pytest.raises(SchemaViolation) as exc:
        extract_json("no json here at all")
    msg = retry_message(exc.value.problems)
    assert "ONLY one JSON object" in msg and "parseable" in msg


def test_normalize_action_strips_grammar_padding():
    from rsched.engine.actions import normalize_action

    padded = {"say": "s", "kind": "spawn", "prompt": "do x", "label": "",
              "n": None, "all": False, "question": "", "options": [],
              "response_schema": {}, "append": False}
    cleaned = normalize_action(padded)
    assert cleaned == {"say": "s", "kind": "spawn", "prompt": "do x"}
    assert validate_action(cleaned) == []
    # required fields are never dropped, even when empty — the validator must see them
    missing = normalize_action({"say": "s", "kind": "write_file", "path": "f", "content": ""})
    assert "content" in missing and validate_action(missing)
    # meaningful values survive
    keep = normalize_action({"say": "s", "kind": "wait", "all": True, "timeout_s": 60})
    assert keep["all"] is True and keep["timeout_s"] == 60
    # a stray narration key folds into say and does not trip additionalProperties
    strayed = normalize_action({"kind": "util", "name": "list", "thought": "let me look"})
    assert strayed == {"say": "let me look", "kind": "util", "name": "list"}
    assert validate_action(strayed) == []
    # tool-call envelope unwraps to a flat action
    wrapped = normalize_action({"tool_name": "util", "parameters": {"say": "s", "name": "list"}})
    assert wrapped["kind"] == "util" and wrapped["name"] == "list"
