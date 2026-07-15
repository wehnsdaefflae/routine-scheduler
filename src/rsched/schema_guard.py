"""Tolerant JSON extraction + validation + retry-message construction.

Shared by all endpoint adapters: whatever native schema mode an endpoint offers, the reply
still passes through here so the engine gets either a validated object or a precise,
model-readable error to retry with.
"""

from __future__ import annotations

import json
import re

import jsonschema

_FENCE_RE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL)


class SchemaViolation(Exception):  # noqa: N818 — schema-retry control signal, not an error
    """Raised when a reply cannot be turned into a schema-valid object.
    .problems holds model-readable error lines for the retry prompt.
    """

    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def loads_tolerant(text: str):
    """json.loads for LLM-authored JSON: strict=False accepts raw control characters
    (unescaped newlines/tabs) inside strings — a common weak-model slip.
    """
    return json.loads(text, strict=False)


def extract_json(text: str) -> dict:
    """Pull one JSON object out of a model reply, tolerating code fences and
    surrounding prose. Raises SchemaViolation if nothing parses.
    """
    text = text.strip()
    candidates: list[str] = []
    try:
        loads_tolerant(text)
        candidates.append(text)
    except json.JSONDecodeError:
        candidates.extend(m.group(1).strip() for m in _FENCE_RE.finditer(text))
        start = text.find("{")
        if start != -1:
            # widest brace span first, then narrow from the right
            end = text.rfind("}")
            while end > start:
                candidates.append(text[start : end + 1])
                end = text.rfind("}", start, end)
    for cand in candidates:
        try:
            obj = loads_tolerant(cand)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    raise SchemaViolation(["reply did not contain a parseable JSON object"])


def validate(obj: dict, schema: dict) -> list[str]:
    """Validate against a JSON Schema; returns model-readable problem lines."""
    validator = jsonschema.Draft202012Validator(schema)
    problems = []
    for err in sorted(validator.iter_errors(obj), key=lambda e: list(e.absolute_path)):
        where = ".".join(str(p) for p in err.absolute_path) or "(root)"
        problems.append(f"{where}: {err.message}")
    return problems


def parse_reply(text: str, schema: dict, semantic=None) -> dict:
    """Full pipeline: extract → JSON-Schema validate → optional semantic check.
    Returns the object or raises SchemaViolation with everything that is wrong.
    """
    obj = extract_json(text)
    problems = validate(obj, schema)
    if not problems and semantic is not None:
        problems = semantic(obj)
    if problems:
        raise SchemaViolation(problems)
    return obj


def retry_message(problems: list[str], *, example: dict | None = None,
                  repeated: bool = False) -> str:
    lines = "\n".join(f"- {p}" for p in problems)
    parts = ["Your previous reply was not a valid action:", lines]
    if repeated:
        parts.append("You returned the SAME invalid action again — do not repeat it; "
                     "fix the problems listed above.")
    if example:
        parts.append(f"A valid kind={example.get('kind')} action has exactly this shape "
                     "(no other top-level fields):\n"
                     + json.dumps(example, ensure_ascii=False))
    parts.append("Reply again with ONLY one JSON object matching the action schema — "
                 "no prose outside the JSON.")
    return "\n".join(parts)
