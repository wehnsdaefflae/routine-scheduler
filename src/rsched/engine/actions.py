"""The action schema — the single source of truth for what an orchestrator turn may do.

Deliberately FLAT (one object, `kind` enum, optional fields, no oneOf): weak local models and
Ollama's grammar conversion handle flat schemas far better. Per-kind required-field checks
happen in code (`validate_action`) so the JSON-Schema layer stays permissive and the model
gets precise, actionable error messages.

`say` comes first on purpose: giving the model its narration outlet inside the JSON reduces
prose-outside-JSON failures.
"""

from __future__ import annotations

KINDS = ("util", "write_util", "read_file", "write_file", "llm", "spawn", "subruns",
         "kill", "wait", "ask_user", "finish")

ACTION_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["say", "kind"],
    "properties": {
        "say": {
            "type": "string",
            "description": "1-3 sentences: what you observed, what you decided, why this action now.",
        },
        "kind": {"type": "string", "enum": list(KINDS)},
        # util / write_util (the ONLY way to run code — there is no shell)
        "name": {
            "type": "string",
            "description": "util/write_util: the global util's name (kebab-case) · also kill/wait target uses 'n'",
        },
        "args": {
            "type": "array", "items": {"type": "string"},
            "description": "util: command-line arguments passed to the util (append '--json' for structured output)",
        },
        "timeout_s": {
            "type": "integer", "minimum": 1, "maximum": 600,
            "description": "util: seconds before the util is killed (default 300) · wait: max seconds to block (default 600)",
        },
        # read_file / write_file
        "path": {
            "type": "string",
            "description": "read_file/write_file: path relative to the routine dir (or an allowed root)",
        },
        "start_line": {"type": "integer", "minimum": 1, "description": "read_file: first line (default 1)"},
        "max_lines": {
            "type": "integer", "minimum": 1, "maximum": 500,
            "description": "read_file: line cap (default 200)",
        },
        "content": {"type": "string",
                    "description": "write_file: full new content · write_util: the complete PEP 723 script (see contract)"},
        "append": {"type": "boolean", "description": "write_file: append instead of overwrite (default false)"},
        # llm / spawn
        "prompt": {"type": "string",
                   "description": "llm: the prompt · spawn: the sub-workflow's full self-contained instruction"},
        "system": {"type": "string", "description": "llm: optional system prompt"},
        "role": {
            "type": "string", "enum": ["subcall", "cheap"],
            "description": "llm: which configured model role (default subcall)",
        },
        "response_schema": {"type": "object", "description": "llm: optional JSON schema constraining the reply"},
        "workflow": {"type": "string",
                     "description": "spawn: library workflow slug for the child (default general-task)"},
        "label": {"type": "string", "description": "spawn: short name shown in the run tree"},
        # subruns / kill / wait
        "n": {"type": "integer", "minimum": 1, "description": "kill/wait: the sub-workflow number"},
        "all": {"type": "boolean", "description": "wait: wait for ALL running sub-workflows (default: any next)"},
        # ask_user
        "question": {"type": "string", "description": "ask_user: the question, self-contained"},
        "mode": {
            "type": "string", "enum": ["blocking", "deferred"],
            "description": "ask_user: wait for the answer vs file it and continue (default deferred)",
        },
        "options": {
            "type": "array", "items": {"type": "string"}, "maxItems": 5,
            "description": "ask_user: optional pick-one choices",
        },
        # finish
        "status": {"type": "string", "enum": ["ok", "partial", "failed"], "description": "finish: run outcome"},
        "summary": {
            "type": "string",
            "description": "finish: 3-10 line result summary (becomes result.md and the dashboard's last-outcome)",
        },
    },
}

# kind → (required fields, allowed extra fields beyond say/kind)
_KIND_FIELDS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "util": (("name",), ("args", "timeout_s")),
    "write_util": (("name", "content"), ()),
    "read_file": (("path",), ("start_line", "max_lines")),
    "write_file": (("path", "content"), ("append",)),
    "llm": (("prompt",), ("system", "role", "response_schema")),
    "spawn": (("prompt",), ("workflow", "label")),
    "subruns": ((), ()),
    "kill": (("n",), ()),
    "wait": ((), ("n", "all", "timeout_s")),
    "ask_user": (("question",), ("mode", "options")),
    "finish": (("status", "summary"), ()),
}


def normalize_action(obj: dict) -> dict:
    """Strip grammar-padding: constrained decoders (Ollama json_schema, OpenRouter strict)
    tend to emit OTHER kinds' fields as empty strings/false/null. Empty-valued fields that
    are not required for this kind carry no information — drop them so the semantic
    validator sees the model's intent, not the grammar's debris."""
    kind = obj.get("kind")
    required = set(_KIND_FIELDS.get(kind, ((), ()))[0])
    out = {}
    for key, val in obj.items():
        if key in ("say", "kind") or key in required:
            out[key] = val
        elif val in ("", None, [], {}) or val is False:
            continue
        else:
            out[key] = val
    return out


def validate_action(obj: dict) -> list[str]:
    """Semantic per-kind checks on an object that already passed the JSON Schema.
    Returns a list of problems (empty = valid)."""
    problems: list[str] = []
    kind = obj.get("kind")
    if kind not in _KIND_FIELDS:
        return [f"unknown kind {kind!r}"]
    required, optional = _KIND_FIELDS[kind]
    for field in required:
        val = obj.get(field)
        if val is None or (isinstance(val, str) and not val.strip()):
            problems.append(f"kind={kind} requires a non-empty {field!r} field")
    allowed = {"say", "kind", *required, *optional}
    stray = [k for k in obj if k not in allowed]
    if stray:
        problems.append(
            f"fields {stray} do not belong to kind={kind} (allowed: {sorted(allowed)})"
        )
    return problems


def example_action() -> dict:
    """The few-shot example embedded in the harness contract."""
    return {
        "say": "The instruction needs current web results, and the util catalog lists a websearch util. I run it for structured output.",
        "kind": "util",
        "name": "websearch",
        "args": ["LLM agent papers 2026", "--json"],
    }
