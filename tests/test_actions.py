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
        {"say": "s", "kind": "remove_util", "name": "obsolete-util"},
        {"say": "s", "kind": "read_file", "path": "LEDGER.md", "max_lines": 40},
        {"say": "s", "kind": "view_image", "path": "attachments/shot.png", "prompt": "what is shown"},
        {"say": "s", "kind": "write_file", "path": "state/x.json", "content": "{}", "append": False},
        {"say": "s", "kind": "edit_file", "path": "state/x.md", "anchor": "old text",
         "replacement": "new text"},
        {"say": "s", "kind": "memory_read", "name": "portal-quirks"},
        {"say": "s", "kind": "memory_write", "name": "portal-quirks", "content": "# note",
         "about": "per-portal gotchas — read before scanning"},
        {"say": "s", "kind": "read_trait", "name": "evidence-discipline"},
        {"say": "s", "kind": "llm", "prompt": "p", "system": "sys", "response_schema": {"type": "object"}},
        {"say": "s", "kind": "spawn", "prompt": "do x", "label": "research", "workflow": "general-task"},
        {"say": "s", "kind": "subtask", "prompt": "do step x", "label": "step-1",
         "workflow": "general-task", "turns": 8},
        {"say": "s", "kind": "detach", "prompt": "scrape it", "label": "scrape",
         "workflow": "general-task"},
        {"say": "s", "kind": "schedule_run", "target": "some-routine", "fire_at": "+3d",
         "reason": "re-check the thing"},
        {"say": "s", "kind": "subruns"},
        {"say": "s", "kind": "kill", "n": 2},
        {"say": "s", "kind": "wait", "all": True, "timeout_s": 120},
        {"say": "s", "kind": "ask_user", "question": "q?", "mode": "blocking", "options": ["a", "b"]},
        {"say": "s", "kind": "report_bug", "title": "schedule_run ate my args",
         "detail": "called X, got Y, expected Z"},
        {"say": "s", "kind": "finish", "status": "ok", "summary": "done"},
    ],
    ids=KINDS,
)
def test_valid_actions_pass_both_layers(action):
    assert parse_reply(json.dumps(action), ACTION_SCHEMA, validate_action) == action


@pytest.mark.parametrize(
    ("action", "fragment"),
    [
        ({"say": "s", "kind": "util"}, "name"),
        ({"say": "s", "kind": "util", "name": "   "}, "name"),
        ({"say": "s", "kind": "write_util", "name": "x"}, "content"),
        ({"say": "s", "kind": "write_file", "path": "x"}, "content"),
        ({"say": "s", "kind": "finish", "status": "ok"}, "summary"),
        ({"say": "s", "kind": "ask_user", "question": "q?", "args": ["ls"]}, "do not belong"),
        ({"say": "s", "kind": "llm", "prompt": "p", "path": "x"}, "do not belong"),
        # .memory/ is fenced off from the generic file actions …
        ({"say": "s", "kind": "read_file", "path": ".memory/INDEX.md"}, "memory_read"),
        ({"say": "s", "kind": "write_file", "path": "./.memory/x.md", "content": "y"}, "memory_write"),
        # … and the memory actions enforce topic slugs, the reserved index, and the cap
        ({"say": "s", "kind": "memory_read", "name": "Not A Slug"}, "kebab-case"),
        ({"say": "s", "kind": "memory_write", "name": "index", "content": "x", "about": "a"}, "reserved"),
        ({"say": "s", "kind": "memory_write", "name": "t", "content": "x"}, "about"),
        ({"say": "s", "kind": "memory_write", "name": "t", "about": "a"}, "content"),
        ({"say": "s", "kind": "memory_write", "name": "t", "about": "a",
          "content": "\n".join(f"l{i}" for i in range(101))}, "capped at 100"),
    ],
)
def test_semantic_violations(action, fragment):
    problems = validate_action(action)
    assert problems and any(fragment in p for p in problems)


def test_memory_write_delete_needs_no_content():
    assert validate_action({"say": "s", "kind": "memory_write", "name": "t", "delete": True}) == []
    # state/ paths stay open to the generic file actions
    assert validate_action({"say": "s", "kind": "read_file", "path": "state/memory-notes.md"}) == []


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


def test_every_kind_has_a_valid_example():
    from rsched.engine.actions import ACTION_SCHEMA, KIND_EXAMPLES, KINDS, validate_action
    from rsched.schema_guard import validate
    assert set(KIND_EXAMPLES) == set(KINDS)
    for kind, example in KIND_EXAMPLES.items():
        assert example["kind"] == kind
        problems = validate(example, ACTION_SCHEMA) or validate_action(example)
        assert not problems, f"{kind}: {problems}"


def test_write_util_content_must_be_string():
    from rsched.engine.actions import validate_action
    bad = {"say": "s", "kind": "write_util", "name": "x", "content": {"not": "a script"}}
    assert any("one string" in p for p in validate_action(bad))
    ok = {"say": "s", "kind": "write_file", "path": "p.json", "content": {"fine": True}}
    assert validate_action(ok) == []


def test_normalize_drops_nonempty_strays_when_complete():
    """glm's second failure shape: a complete write_file plus a non-empty foreign field.
    Strays are dropped when required fields are present — and kept when they're not, so
    the retry error still names them."""
    from rsched.engine.actions import normalize_action, validate_action
    complete = {"say": "s", "kind": "write_file", "path": "state/phase.json",
                "content": {"phase": "gather"}, "status": "ok", "workflow": "self-audit"}
    out = normalize_action(dict(complete))
    assert "status" not in out and "workflow" not in out
    assert validate_action(out) == []
    incomplete = {"say": "s", "kind": "write_file", "path": "p", "status": "ok"}
    out2 = normalize_action(dict(incomplete))
    assert "status" in out2                     # kept: the error must name it
    problems = validate_action(out2)
    assert any("content" in p for p in problems) and any("status" in p for p in problems)


def test_write_util_name_must_be_slug():
    """The name becomes a directory under the library — path shapes are rejected in the
    schema cycle (the engine-side traversal guard)."""
    for bad in ("../../evil", "a/b", "UPPER", "dots.py"):
        problems = validate_action({"say": "s", "kind": "write_util",
                                    "name": bad, "content": "# x"})
        assert any("kebab-case" in p for p in problems), bad
    assert validate_action({"say": "s", "kind": "write_util",
                            "name": "good-name", "content": "# x"}) == []
