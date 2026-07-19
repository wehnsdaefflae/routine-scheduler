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

KINDS = ("util", "write_util", "remove_util", "read_file", "view_image", "write_file",
         "edit_file",
         "memory_read", "memory_write", "llm", "spawn", "subtask", "detach", "schedule_run",
         "subruns", "kill", "wait", "ask_user", "report_bug", "finish")

# Kinds available on EVERY turn regardless of the workflow's `tools:` allowlist: `finish`
# so a run can always end, and `report_bug` so any routine can always flag a scheduler
# defect (the ungated, default-on bug channel). Neither is a GATED_KIND, so both also pass
# the capability layer for every routine.
ALWAYS_KINDS = ("finish", "report_bug")

READ_PATHS_MAX = 8

MEMORY_NOTE_MAX_LINES = 100

ACTION_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["say", "kind"],
    "properties": {
        "say": {
            "type": "string",
            "description": "Your narration: lead with what the last observation taught you, then "
                           "why this action. A few words suffice for routine steps; spend 2-3 "
                           "sentences on decisions, direction changes, and surprises. "
                           "Simple Markdown (bold, `code`, links) renders in the UI.",
        },
        "note": {
            "type": "string",
            "description": "OPTIONAL, on any action: 1-3 lines worth keeping beyond this context "
                           "window — a confirmed finding, a dead end, a fallback plan, an "
                           "unresolved doubt. SELF-CONTAINED: a reader with only this line must "
                           "understand it (name things — never 'it' or 'that approach'). The "
                           "engine files it to state/notes.md with a turn stamp, costing no "
                           "turn; don't repeat it in say.",
        },
        "kind": {"type": "string", "enum": list(KINDS)},
        # util / write_util (the ONLY way to run code — there is no shell)
        "name": {
            "type": "string",
            "description": "util/write_util/remove_util: the global util's name (kebab-case) · "
                           "memory_read/memory_write: the note's topic (kebab-case)",
        },
        "args": {
            "type": "array", "items": {"type": "string"},
            "description": "util: command-line arguments passed to the util "
                           "(append '--json' for structured output)",
        },
        "timeout_s": {
            "type": "integer", "minimum": 1, "maximum": 600,
            "description": "util: seconds before the util is killed (default 300) · "
                           "wait: max seconds to block (default 600)",
        },
        # read_file / view_image / write_file / edit_file
        "path": {
            "type": "string",
            "description": "read_file/view_image/write_file/edit_file: path relative to the "
                           "routine dir (or an allowed root)",
        },
        "paths": {
            "type": "array", "items": {"type": "string"}, "maxItems": READ_PATHS_MAX,
            "description": "read_file/view_image: act on SEVERAL files in one action (instead "
                           "of `path`) — batch related reads/images",
        },
        "start_line": {"type": "integer", "minimum": 1,
                       "description": "read_file: first line (default 1)"},
        "max_lines": {
            "type": "integer", "minimum": 1, "maximum": 500,
            "description": "read_file: line cap (default 200)",
        },
        "anchor": {
            "type": "string",
            "description": "edit_file: exact text to find in the file (must be unique unless "
                           "all: true) — copy it verbatim, whitespace included",
        },
        "replacement": {
            "type": "string",
            "description": 'edit_file: the text that replaces the anchor (omit or "" to delete '
                           "it) — edit in place instead of rewriting whole files with write_file",
        },
        "content": {"type": ["string", "object", "array"],
                    "description": "write_file: the full new content — a string, or a JSON "
                                   "object/array (written pretty-printed; no escaping needed) · "
                                   "write_util: the complete PEP 723 script as a string · "
                                   "memory_write: the note's full markdown (one string, "
                                   "≤100 lines)"},
        # schedule_run — arm/cancel a one-shot time trigger on a routine (gated: scheduling)
        "target": {"type": "string",
                   "description": "schedule_run: the routine slug to arm/cancel a one-shot on "
                                  "(self-target always allowed)"},
        "fire_at": {"type": "string",
                    "description": "schedule_run: when to fire ONCE — an absolute ISO-8601 UTC "
                                   "instant, or a relative offset like '+3d' / '+2h' / '+30m'"},
        "reason": {"type": "string",
                   "description": "schedule_run: the provenance line injected into the target's "
                                  "inbox just before the one-shot fires"},
        "cancel": {"type": "boolean",
                   "description": "schedule_run: cancel armed one-shot(s) on target instead of "
                                  "arming (with id: cancel that one; without: cancel all)"},
        "id": {"type": "string",
               "description": "schedule_run: the one-shot id (so-XXXX) to cancel"},
        "append": {"type": "boolean",
                   "description": "write_file: append instead of overwrite (default false)"},
        # memory_write (memory_read needs only `name`)
        "about": {"type": "string",
                  "description": "memory_write: one-line INDEX entry — what this note holds + "
                                 "when to consult it (the engine maintains .memory/INDEX.md "
                                 "from it)"},
        "delete": {"type": "boolean",
                   "description": "memory_write: remove the note and its INDEX line "
                                  "(content/about not needed)"},
        # llm / spawn / subtask / detach / view_image
        "prompt": {"type": "string",
                   "description": "llm: the prompt · spawn/subtask/detach: the child's full "
                                  "self-contained instruction (subtask: fold in the previous "
                                  "subtask's result) · view_image: what to look for (used only if "
                                  "the file falls back to the vision util)"},
        "system": {"type": "string", "description": "llm: optional system prompt"},
        "response_schema": {"type": "object",
                            "description": "llm: optional JSON schema constraining the reply"},
        "workflow": {"type": "string",
                     "description": "spawn/subtask/detach: library workflow slug for the child "
                                    "(default general-task) — pick the pattern matching its "
                                    "purpose"},
        "label": {"type": "string",
                  "description": "spawn/subtask/detach: short name shown in the run tree"},
        "turns": {"type": "integer", "minimum": 1,
                  "description": "subtask: turn budget for this sequential child (default: half "
                                 "your remaining turns)"},
        # subruns / kill / wait
        "n": {"type": "integer", "minimum": 1, "description": "kill/wait: the sub-workflow number"},
        "all": {"type": "boolean",
                "description": "wait: wait for ALL running sub-workflows (default: any next) · "
                               "edit_file: replace EVERY occurrence of the anchor (default: the "
                               "anchor must be unique)"},
        # ask_user
        "question": {"type": "string",
                     "description": "ask_user: the question, self-contained (simple Markdown "
                                    "renders in the UI)"},
        "mode": {
            "type": "string", "enum": ["blocking", "deferred"],
            "description": "ask_user: wait for the answer vs file it and continue "
                           "(default deferred)",
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
        # report_bug — the ungated, default-on bug channel every routine holds
        "title": {
            "type": "string",
            "description": "report_bug: a one-line summary of the scheduler bug or friction "
                           "you hit",
        },
        "detail": {
            "type": "string",
            "description": "report_bug: the full description — what you did, what happened, "
                           "what you expected; enough for the self-audit routine to reproduce "
                           "and fix it",
        },
        # finish
        "status": {"type": "string", "enum": ["ok", "partial", "failed"],
                   "description": "finish: run outcome"},
        "summary": {
            "type": "string",
            "description": "finish: a DETAILED 8-20 line result summary — concrete outcomes "
                           "(numbers, names, links), decisions taken + why, files changed, "
                           "open ends and what the next run should pick up (becomes result.md, "
                           "the dashboard's last-outcome, and the next run's context; Markdown "
                           "— bold, lists, `code`, links, pipe tables, > quotes — renders in "
                           "the UI)",
        },
    },
}

# The one field that best identifies a turn of each kind — the one-line "briefs" used by
# turn records, compaction digests, and transcript replay.
BRIEF_FIELD = {"util": "name", "write_util": "name", "remove_util": "name", "read_file": "path",
               "view_image": "path",
               "write_file": "path", "edit_file": "path", "memory_read": "name",
               "memory_write": "name", "llm": "prompt", "spawn": "label", "subtask": "label",
               "detach": "label", "schedule_run": "target", "kill": "n", "wait": "n",
               "ask_user": "question", "report_bug": "title", "finish": "status"}

# kind → a minimal VALID action, shown to the model when a reply fails validation. Weak
# models merge payload keys into the action object (file bodies, finish fields at top
# level); an abstract error alone often doesn't correct them — a concrete shape does.
KIND_EXAMPLES: dict[str, dict] = {
    "util": {"say": "<why this util now>", "kind": "util", "name": "list"},
    "write_util": {"say": "<why a new util>", "kind": "write_util", "name": "my-util",
                   "content": "<the complete PEP 723 script as ONE string>"},
    "remove_util": {"say": "<why remove this util>", "kind": "remove_util",
                    "name": "obsolete-util"},
    "schedule_run": {"say": "<why arm a one-shot>", "kind": "schedule_run",
                     "target": "some-routine", "fire_at": "+3d",
                     "reason": "<what the fired run should pick up>"},
    "read_file": {"say": "<why this file>", "kind": "read_file", "path": "state/notes.md"},
    "view_image": {"say": "<why look at it>", "kind": "view_image",
                   "path": "attachments/shot.png",
                   "prompt": "<what to look for, if it falls back to the vision util>"},
    "write_file": {"say": "<why this write>", "kind": "write_file", "path": "state/phase.json",
                   "content": {"phase": "<structured data may be a plain JSON object — "
                                        "text files take one string instead>"}},
    "edit_file": {"say": "<why this edit>", "kind": "edit_file", "path": "state/notes.md",
                  "anchor": "<exact text to find (verbatim)>",
                  "replacement": "<what replaces it>"},
    "memory_read": {"say": "<why this note now>", "kind": "memory_read", "name": "topic-slug"},
    "memory_write": {"say": "<what surprised you>", "kind": "memory_write", "name": "topic-slug",
                     "content": "<the note's full markdown, at most 100 lines>",
                     "about": "<one line: what this note holds + when to consult it>"},
    "llm": {"say": "<why delegate>", "kind": "llm", "prompt": "<the subtask prompt>"},
    "spawn": {"say": "<why a child>", "kind": "spawn",
              "prompt": "<self-contained instruction>", "label": "child-1"},
    "subtask": {"say": "<why this sequential step>", "kind": "subtask",
                "prompt": "<self-contained brief; fold in the previous subtask's result>",
                "label": "step-1"},
    "detach": {"say": "<why detach this long job>", "kind": "detach",
               "prompt": "<self-contained brief for the background task>", "label": "scrape"},
    "subruns": {"say": "<why check children>", "kind": "subruns"},
    "kill": {"say": "<why stop it>", "kind": "kill", "n": 1},
    "wait": {"say": "<why block>", "kind": "wait"},
    "ask_user": {"say": "<why ask>", "kind": "ask_user",
                 "question": "<one self-contained question>", "mode": "deferred"},
    "report_bug": {"say": "<the scheduler defect you hit>", "kind": "report_bug",
                   "title": "<one-line summary>",
                   "detail": "<what you did, what happened, what you expected>"},
    "finish": {"say": "<what was achieved>", "kind": "finish", "status": "ok",
               "summary": "<detailed 8-20 line result summary>"},
}

# kind → (required fields, allowed extra fields beyond say/kind)
_KIND_FIELDS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "util": (("name",), ("args", "timeout_s")),
    "write_util": (("name", "content"), ()),
    "remove_util": (("name",), ()),
    "schedule_run": (("target",), ("fire_at", "reason", "cancel", "id")),
    "read_file": ((), ("path", "paths", "start_line", "max_lines")),
    "view_image": ((), ("path", "paths", "prompt")),
    "write_file": (("path", "content"), ("append",)),
    "edit_file": (("path", "anchor"), ("replacement", "all")),
    "memory_read": (("name",), ()),
    "memory_write": (("name",), ("content", "about", "delete")),
    "llm": (("prompt",), ("system", "response_schema")),
    "spawn": (("prompt",), ("workflow", "label")),
    "subtask": (("prompt",), ("workflow", "label", "turns")),
    "detach": (("prompt",), ("workflow", "label")),
    "subruns": ((), ()),
    "kill": (("n",), ()),
    "wait": ((), ("n", "all", "timeout_s")),
    "ask_user": (("question",), ("mode", "options", "default")),
    "report_bug": (("title",), ("detail",)),
    "finish": (("status", "summary"), ()),
}


def normalize_action(obj: dict) -> dict:
    """Strip grammar-padding: constrained decoders (Ollama json_schema, OpenRouter strict)
    tend to emit OTHER kinds' fields as empty strings/false/null. Empty-valued fields that
    are not required for this kind carry no information — drop them so the semantic
    validator sees the model's intent, not the grammar's debris.
    """
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
    kind_fields = _KIND_FIELDS.get(kind) if isinstance(kind, str) else None
    required = set(kind_fields[0]) if kind_fields else set()
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
        complete = all((val := out.get(f)) is not None
                       and not (isinstance(val, str) and not val.strip())
                       for f in req)
        if complete:
            allowed = {"say", "kind", "note", *req, *opt}   # note rides ANY kind, like say
            out = {k: v for k, v in out.items() if k in allowed}
    return out


# One flat per-kind checker on purpose: this function IS the action contract's single home;
# splitting it per kind would scatter what a turn may do across files.
def validate_action(obj: dict, allowed_kinds: set[str] | None = None,  # noqa: C901, PLR0912
                    grants=None) -> list[str]:
    """Semantic per-kind checks on an object that already passed the JSON Schema.
    `allowed_kinds` narrows the vocabulary to a workflow's `tools:` allowlist; `grants`
    (a grants.GrantPolicy) enforces the routine's user-set CAPABILITIES (write_util,
    reserved utils, runs/ access, own-recipe/config writes) — so allowed kinds =
    workflow tools ∩ (base ∪ capabilities). `finish` is always permitted so a run can
    end. Both rejections happen here, inside the schema-retry cycle, so a denied call is
    corrected and never becomes a turn. Returns a list of problems (empty = valid).
    """
    problems: list[str] = []
    kind = obj.get("kind")
    if kind not in _KIND_FIELDS:
        return [f"unknown kind {kind!r}"]
    if allowed_kinds is not None and kind not in ALWAYS_KINDS and kind not in allowed_kinds:
        return [f"kind={kind} is not available in this workflow — it permits only "
                f"{sorted(allowed_kinds | set(ALWAYS_KINDS))}; use one of those"]
    if grants is not None and kind not in ALWAYS_KINDS and (denial := grants.deny(obj)):
        return [denial]
    required, optional = _KIND_FIELDS[kind]
    for field in required:
        val = obj.get(field)
        if val is None or (isinstance(val, str) and not val.strip()):
            problems.append(f"kind={kind} requires a non-empty {field!r} field")
    if kind == "write_util" and not isinstance(obj.get("content"), str | None):
        problems.append("kind=write_util requires 'content' to be the script text (one string)")
    if kind == "remove_util" and not is_slug(str(obj.get("name") or "")):
        problems.append("kind=remove_util requires 'name' to be a kebab-case util name")
    if kind == "schedule_run":
        if not is_slug(str(obj.get("target") or "")):
            problems.append("kind=schedule_run requires 'target' to be a kebab-case routine slug")
        if not obj.get("cancel"):
            if not str(obj.get("fire_at") or "").strip():
                problems.append("kind=schedule_run requires 'fire_at' (an ISO instant or a "
                                "relative offset like '+3d') unless cancel: true")
            if not str(obj.get("reason") or "").strip():
                problems.append("kind=schedule_run requires 'reason' (why the one-shot fires) "
                                "unless cancel: true")
    if kind in ("read_file", "view_image"):
        paths = obj.get("paths")
        if paths is not None and (not isinstance(paths, list)
                                  or not all(isinstance(p, str) and p.strip() for p in paths)):
            problems.append(f"kind={kind}: 'paths' must be a list of non-empty path strings")
            paths = None
        if not str(obj.get("path") or "").strip() and not paths:
            problems.append(f"kind={kind} requires 'path' (one file) or 'paths' (several)")
        elif str(obj.get("path") or "").strip() and paths:
            problems.append(f"kind={kind} takes 'path' OR 'paths', not both")
        elif paths and len(paths) > READ_PATHS_MAX:
            problems.append(f"kind={kind}: at most {READ_PATHS_MAX} paths per action")
    if kind == "edit_file" and "replacement" in obj and not isinstance(obj["replacement"], str):
        problems.append("kind=edit_file: 'replacement' must be a string (\"\" deletes the anchor)")
    # .memory/ is reachable ONLY through the memory actions — the engine owns INDEX.md and
    # enforces the note cap there; generic file access would silently bypass both.
    if kind in ("read_file", "view_image", "write_file", "edit_file"):
        multi = obj.get("paths") or [] if kind in ("read_file", "view_image") else []
        for raw in [obj.get("path"), *multi]:
            rel = str(raw or "")
            while rel.startswith("./"):
                rel = rel[2:]
            if rel == ".memory" or rel.startswith(".memory/"):
                problems.append(f"kind={kind} may not touch .memory/ — use memory_read / "
                                "memory_write (the engine maintains .memory/INDEX.md for you)")
                break
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
    allowed = {"say", "kind", "note", *required, *optional}   # note rides ANY kind, like say
    stray = [k for k in obj if k not in allowed]
    if stray:
        problems.append(
            f"fields {stray} do not belong to kind={kind} (allowed: {sorted(allowed)})"
        )
    return problems


def util_rejection_outcome(obj: dict, allowed_kinds: set[str] | None = None,
                           grants=None) -> tuple[str, str] | None:
    """Classify a REJECTED util action for per-util telemetry (RunContext.count_util):
    returns (util name, "denied" | "rejected") or None when the rejection is not
    attributable to a util. "denied" = a permission refusal (a reserved util switched
    off, the util kind excluded by the workflow's tools:) — Mark's "permission problem";
    "rejected" = a malformed call (schema/field problems). A denial never reaches the
    executor — it is corrected inside the schema-retry cycle and never becomes a turn —
    so it MUST be counted here at the validation seam or it would never be counted at
    all. The catalog pseudo-utils (list/show) are discovery, not execution: skipped.
    """
    name = str(obj.get("name") or "").strip()
    if obj.get("kind") != "util" or not name or name in ("list", "show"):
        return None
    denied = ((allowed_kinds is not None and "util" not in allowed_kinds)
              or (grants is not None and grants.deny(obj) is not None))
    return name, ("denied" if denied else "rejected")


def example_action() -> dict:
    """The few-shot example embedded in the harness contract — models on-demand step
    reading with a finding-first `say` (NOT util discovery: the catalog is already in
    CAPABILITIES, so opening a run by re-listing it just re-buys known information).
    """
    return {
        "say": "Digest puts this run at the scan stage — reading its module before acting.",
        "kind": "read_file",
        "path": "stages/scan.md",
    }
