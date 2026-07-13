"""The action schema — the single source of truth for what an orchestrator turn may do.

Deliberately FLAT (one object, `kind` enum, optional fields, no oneOf): weak local models and
Ollama's grammar conversion handle flat schemas far better. Per-kind required-field checks
happen in code (`validate_action`) so the JSON-Schema layer stays permissive and the model
gets precise, actionable error messages.

`say` comes first on purpose: giving the model its narration outlet inside the JSON reduces
prose-outside-JSON failures.
"""

from __future__ import annotations

from ..ids import is_slug

KINDS = ("util", "write_util", "read_file", "write_file", "memory_read", "memory_write",
         "llm", "spawn", "subruns", "kill", "wait", "ask_user", "finish")

MEMORY_NOTE_MAX_LINES = 100

ACTION_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["say", "kind"],
    "properties": {
        "say": {
            "type": "string",
            "description": "1-3 sentences: what you observed, what you decided, why this action now. "
                           "Simple Markdown (bold, `code`, links) renders in the UI.",
        },
        "kind": {"type": "string", "enum": list(KINDS)},
        # util / write_util (the ONLY way to run code — there is no shell)
        "name": {
            "type": "string",
            "description": "util/write_util: the global util's name (kebab-case) · "
                           "memory_read/memory_write: the note's topic (kebab-case)",
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
        "content": {"type": ["string", "object", "array"],
                    "description": "write_file: the full new content — a string, or a JSON object/array "
                                   "(written pretty-printed; no escaping needed) · "
                                   "write_util: the complete PEP 723 script as a string · "
                                   "memory_write: the note's full markdown (one string, ≤100 lines)"},
        "append": {"type": "boolean", "description": "write_file: append instead of overwrite (default false)"},
        # memory_write (memory_read needs only `name`)
        "about": {"type": "string",
                  "description": "memory_write: one-line INDEX entry — what this note holds + when to "
                                 "consult it (the engine maintains .memory/INDEX.md from it)"},
        "delete": {"type": "boolean",
                   "description": "memory_write: remove the note and its INDEX line (content/about not needed)"},
        # llm / spawn
        "prompt": {"type": "string",
                   "description": "llm: the prompt · spawn: the sub-workflow's full self-contained instruction"},
        "system": {"type": "string", "description": "llm: optional system prompt"},
        "response_schema": {"type": "object", "description": "llm: optional JSON schema constraining the reply"},
        "workflow": {"type": "string",
                     "description": "spawn: library workflow slug for the child (default general-task)"},
        "label": {"type": "string", "description": "spawn: short name shown in the run tree"},
        # subruns / kill / wait
        "n": {"type": "integer", "minimum": 1, "description": "kill/wait: the sub-workflow number"},
        "all": {"type": "boolean", "description": "wait: wait for ALL running sub-workflows (default: any next)"},
        # ask_user
        "question": {"type": "string",
                     "description": "ask_user: the question, self-contained (simple Markdown renders in the UI)"},
        "mode": {
            "type": "string", "enum": ["blocking", "deferred"],
            "description": "ask_user: wait for the answer vs file it and continue (default deferred)",
        },
        "options": {
            "type": "array", "items": {"type": "string"}, "maxItems": 5,
            "description": "ask_user: optional pick-one choices",
        },
        "default": {
            "type": "string",
            "description": "ask_user: what you will DO without an answer — a blocking question "
                           "that times out continues on this stated default; shown to the user "
                           "with the question",
        },
        # finish
        "status": {"type": "string", "enum": ["ok", "partial", "failed"], "description": "finish: run outcome"},
        "summary": {
            "type": "string",
            "description": "finish: a DETAILED 8-20 line result summary — concrete outcomes (numbers, "
                           "names, links), decisions taken + why, files changed, open ends and what the "
                           "next run should pick up (becomes result.md, the dashboard's last-outcome, and "
                           "the next run's context; simple Markdown — bold, lists, `code`, links — renders "
                           "in the UI)",
        },
    },
}

# The one field that best identifies a turn of each kind — the one-line "briefs" used by
# turn records, compaction digests, and transcript replay.
BRIEF_FIELD = {"util": "name", "write_util": "name", "read_file": "path", "write_file": "path",
               "memory_read": "name", "memory_write": "name",
               "llm": "prompt", "spawn": "label", "kill": "n", "wait": "n",
               "ask_user": "question", "finish": "status"}

# kind → a minimal VALID action, shown to the model when a reply fails validation. Weak
# models merge payload keys into the action object (file bodies, finish fields at top
# level); an abstract error alone often doesn't correct them — a concrete shape does.
KIND_EXAMPLES: dict[str, dict] = {
    "util": {"say": "<why this util now>", "kind": "util", "name": "list"},
    "write_util": {"say": "<why a new util>", "kind": "write_util", "name": "my-util",
                   "content": "<the complete PEP 723 script as ONE string>"},
    "read_file": {"say": "<why this file>", "kind": "read_file", "path": "state/notes.md"},
    "write_file": {"say": "<why this write>", "kind": "write_file", "path": "state/phase.json",
                   "content": {"phase": "<structured data may be a plain JSON object — "
                                        "text files take one string instead>"}},
    "memory_read": {"say": "<why this note now>", "kind": "memory_read", "name": "topic-slug"},
    "memory_write": {"say": "<what surprised you>", "kind": "memory_write", "name": "topic-slug",
                     "content": "<the note's full markdown, at most 100 lines>",
                     "about": "<one line: what this note holds + when to consult it>"},
    "llm": {"say": "<why delegate>", "kind": "llm", "prompt": "<the subtask prompt>"},
    "spawn": {"say": "<why a child>", "kind": "spawn",
              "prompt": "<self-contained instruction>", "label": "child-1"},
    "subruns": {"say": "<why check children>", "kind": "subruns"},
    "kill": {"say": "<why stop it>", "kind": "kill", "n": 1},
    "wait": {"say": "<why block>", "kind": "wait"},
    "ask_user": {"say": "<why ask>", "kind": "ask_user",
                 "question": "<one self-contained question>", "mode": "deferred"},
    "finish": {"say": "<what was achieved>", "kind": "finish", "status": "ok",
               "summary": "<detailed 8-20 line result summary>"},
}

# kind → (required fields, allowed extra fields beyond say/kind)
_KIND_FIELDS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "util": (("name",), ("args", "timeout_s")),
    "write_util": (("name", "content"), ()),
    "read_file": (("path",), ("start_line", "max_lines")),
    "write_file": (("path", "content"), ("append",)),
    "memory_read": (("name",), ()),
    "memory_write": (("name",), ("content", "about", "delete")),
    "llm": (("prompt",), ("system", "response_schema")),
    "spawn": (("prompt",), ("workflow", "label")),
    "subruns": ((), ()),
    "kill": (("n",), ()),
    "wait": ((), ("n", "all", "timeout_s")),
    "ask_user": (("question",), ("mode", "options", "default")),
    "finish": (("status", "summary"), ()),
}


def normalize_action(obj: dict) -> dict:
    """Strip grammar-padding: constrained decoders (Ollama json_schema, OpenRouter strict)
    tend to emit OTHER kinds' fields as empty strings/false/null. Empty-valued fields that
    are not required for this kind carry no information — drop them so the semantic
    validator sees the model's intent, not the grammar's debris."""
    # weak models sometimes wrap the action in a generic tool-call envelope — unwrap it
    if "kind" not in obj:
        if isinstance(obj.get("action"), dict):        # {"action": {...}}
            obj = obj["action"]
        inner = (obj.get("parameters") or obj.get("arguments")
                 or obj.get("tool_input") or obj.get("input"))
        tool = obj.get("tool_name") or obj.get("tool") or obj.get("name")
        if isinstance(inner, dict):
            obj = {**inner, **({"kind": tool} if tool and "kind" not in inner else {})}
        elif tool in KINDS:
            obj = {**{k: v for k, v in obj.items() if k not in ("tool_name", "tool", "name")},
                   "kind": tool}
    # weak models often add a stray narration key alongside `say` — fold it in, don't fail on it
    for stray in ("thought", "thinking", "reasoning"):
        if stray in obj:
            if not obj.get("say"):
                obj["say"] = obj[stray]
            obj = {k: v for k, v in obj.items() if k != stray}

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
    # Weak models also merge NON-empty foreign fields into an otherwise-complete action
    # (e.g. a stray status:"ok" on a write_file). When every required field is present,
    # unknown fields carry no per-kind meaning — drop them instead of failing the turn.
    # When a required field is missing, keep the strays so the retry error names them.
    if kind in _KIND_FIELDS:
        req, opt = _KIND_FIELDS[kind]
        complete = all(out.get(f) is not None
                       and not (isinstance(out.get(f), str) and not out.get(f).strip())
                       for f in req)
        if complete:
            allowed = {"say", "kind", *req, *opt}
            out = {k: v for k, v in out.items() if k in allowed}
    return out


def validate_action(obj: dict, allowed_kinds: set[str] | None = None,
                    grants=None) -> list[str]:
    """Semantic per-kind checks on an object that already passed the JSON Schema.
    `allowed_kinds` narrows the vocabulary to a workflow's `tools:` allowlist; `grants`
    (a grants.GrantPolicy) gates capabilities the routine's permissions must unlock
    (write_util, reserved utils, runs/ access, own-recipe/config writes) — so allowed
    kinds = workflow tools ∩ (base ∪ grants). `finish` is always permitted so a run can
    end. Both rejections happen here, inside the schema-retry cycle, so a denied call is
    corrected and never becomes a turn. Returns a list of problems (empty = valid)."""
    problems: list[str] = []
    kind = obj.get("kind")
    if kind not in _KIND_FIELDS:
        return [f"unknown kind {kind!r}"]
    if allowed_kinds is not None and kind != "finish" and kind not in allowed_kinds:
        return [f"kind={kind} is not available in this workflow — it permits only "
                f"{sorted(allowed_kinds | {'finish'})}; use one of those"]
    if grants is not None and kind != "finish" and (denial := grants.deny(obj)):
        return [denial]
    required, optional = _KIND_FIELDS[kind]
    for field in required:
        val = obj.get(field)
        if val is None or (isinstance(val, str) and not val.strip()):
            problems.append(f"kind={kind} requires a non-empty {field!r} field")
    if kind == "write_util" and not isinstance(obj.get("content"), str | None):
        problems.append("kind=write_util requires 'content' to be the script text (one string)")
    # .memory/ is reachable ONLY through the memory actions — the engine owns INDEX.md and
    # enforces the note cap there; generic file access would silently bypass both.
    if kind in ("read_file", "write_file"):
        rel = str(obj.get("path") or "")
        while rel.startswith("./"):
            rel = rel[2:]
        if rel == ".memory" or rel.startswith(".memory/"):
            problems.append(f"kind={kind} may not touch .memory/ — use memory_read / "
                            "memory_write (the engine maintains .memory/INDEX.md for you)")
    if kind in ("memory_read", "memory_write"):
        name = str(obj.get("name") or "")
        if name and not is_slug(name):
            problems.append(f"kind={kind}: 'name' must be a kebab-case topic slug, got {name!r}")
        if kind == "memory_write" and name.lower() == "index":
            problems.append("memory_write: 'index' is reserved — the engine maintains "
                            ".memory/INDEX.md from each note's 'about' line")
        if kind == "memory_write" and not obj.get("delete"):
            content = obj.get("content")
            if not isinstance(content, str) or not content.strip():
                problems.append("memory_write requires 'content' (the note's full markdown, "
                                "one string) unless delete: true")
            elif len(content.splitlines()) > MEMORY_NOTE_MAX_LINES:
                problems.append(f"memory_write: content is {len(content.splitlines())} lines — "
                                f"notes are capped at {MEMORY_NOTE_MAX_LINES}; split the topic "
                                "into more notes")
            if not str(obj.get("about") or "").strip():
                problems.append("memory_write requires 'about' (the note's one-line INDEX "
                                "entry) unless delete: true")
    allowed = {"say", "kind", *required, *optional}
    stray = [k for k in obj if k not in allowed]
    if stray:
        problems.append(
            f"fields {stray} do not belong to kind={kind} (allowed: {sorted(allowed)})"
        )
    return problems


def example_action() -> dict:
    """The few-shot example embedded in the harness contract — also models tool discovery."""
    return {
        "say": "Before choosing a tool I list what global utils exist, so I use the right one.",
        "kind": "util",
        "name": "list",
    }
