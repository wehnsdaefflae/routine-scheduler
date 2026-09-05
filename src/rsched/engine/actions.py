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
from .actionschema import KINDS, READ_PATHS_MAX
from .remind import field_problems as reminder_field_problems

# The fields that ride EVERY kind alongside `say`, each a no-turn side effect the engine
# files rather than an argument to the action: the note channel and the two halves of the
# consequence-reminder layer.
SIDE_FIELDS = ("note", "remind", "remind_feedback")

# Kinds available on EVERY turn regardless of the workflow's `tools:` allowlist: `finish`
# so a run can always end, and `report` so any routine can always raise work that is not its
# own task — unaddressed for triage, or addressed to the routine that owns it. Neither is a
# GATED_KIND, so both also pass the capability layer for every routine. Routing only works if
# the channel is present at the moment the run notices the problem. `list_models` rides along
# because the per-call `model` override is only usable where the run can SEE the catalog —
# read-only discovery of user config, never a mutation, so gating it would only cost turns.
ALWAYS_KINDS = ("finish", "report", "list_models")


MEMORY_NOTE_MAX_LINES = 100


# kind → a minimal VALID action, shown to the model when a reply fails validation. Weak
# models merge payload keys into the action object (file bodies, finish fields at top
# level); an abstract error alone often doesn't correct them — a concrete shape does.
KIND_EXAMPLES: dict[str, dict] = {
    "util": {"say": "<why this util now>", "kind": "util", "name": "list"},
    "list_models": {"say": "<why model discovery now>", "kind": "list_models"},
    "script": {"say": "<why this deterministic step now>", "kind": "script",
               "name": "poll-inbox", "args": ["--json"]},
    "shell": {"say": "<why an ad-hoc command instead of a util>", "kind": "shell",
              "command": "<the ONE command line, as a single string>"},
    "write_util": {"say": "<why a new util>", "kind": "write_util", "name": "my-util",
                   "content": "<the complete PEP 723 script as ONE string>"},
    "remove_util": {"say": "<why remove this util>", "kind": "remove_util",
                    "name": "obsolete-util"},
    "schedule_run": {"say": "<why arm a one-shot>", "kind": "schedule_run",
                     "target": "some-routine", "fire_at": "+3d",
                     "reason": "<what the fired run should pick up>"},
    "create_routine": {"say": "<why create this routine now>", "kind": "create_routine",
                       "target": "arxiv-reading-list", "name": "Arxiv reading list",
                       "prompt": "<the clarified task, decomposed into the routine's stages>",
                       "workflow": "general-task",
                       "stopping": ["<what DONE looks like for one run, in the user's words>"]},
    "manage_group": {"say": "<why this group change now>", "kind": "manage_group",
                     "verb": "create", "name": "Morning jobs",
                     "members": ["weight-coach", "news-digest"]},

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
    "read_rule": {"say": "<why this rule now>", "kind": "read_rule",
                  "name": "test-design"},
    "write_rule": {"say": "<the evidence that this wording is the cause>", "kind": "write_rule",
                   "name": "test-design",
                   "anchor": "<the exact sentence(s) to replace, copied verbatim>",
                   "replacement": "<the new wording, in the rule's own voice>"},
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
    "report": {"say": "<the problem you are raising>", "kind": "report",
               "title": "<one-line summary>",
               "detail": "<the artefact, what is wrong, the evidence, what done looks like>",
               "target": "<the routine that owns it, or omit for triage>"},
    "finish": {"say": "<what was achieved>", "kind": "finish", "status": "ok",
               "summary": "<detailed 8-20 line result summary>"},
}

# kind → (required fields, allowed extra fields beyond say/kind)
KIND_FIELDS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "util": (("name",), ("args", "timeout_s")),
    "script": (("name",), ("args", "timeout_s")),
    "shell": (("command",), ("timeout_s", "path")),
    "write_util": (("name",), ("content", "path", "anchor", "replacement", "all")),
    "remove_util": (("name",), ()),
    "schedule_run": (("target",), ("fire_at", "reason", "cancel", "id")),
    "create_routine": (("target", "name", "prompt"), ("workflow", "stopping")),
    "manage_group": (("verb",), ("target", "name", "members", "on_failure", "cron",
                                 "paused")),
    "read_file": ((), ("path", "paths", "start_line", "max_lines")),
    "view_image": ((), ("path", "paths", "prompt")),
    "write_file": (("path", "content"), ("append",)),
    "edit_file": (("path", "anchor"), ("replacement", "all")),
    "memory_read": (("name",), ()),
    "memory_write": (("name",), ("content", "about", "delete")),
    "read_rule": (("name",), ()),
    "write_rule": (("name",), ("content", "anchor", "replacement", "all")),
    "llm": (("prompt",), ("system", "response_schema", "model")),
    "spawn": (("prompt",), ("workflow", "label", "model")),
    "subtask": (("prompt",), ("workflow", "label", "turns", "model")),
    "detach": (("prompt",), ("workflow", "label")),
    "list_models": ((), ()),
    "subruns": ((), ()),
    "kill": (("n",), ()),
    "wait": ((), ("n", "all", "timeout_s")),
    "ask_user": (("question",), ("mode", "options", "default", "config_patch", "request")),
    "report": (("title",), ("detail", "target", "answers", "closes")),
    "finish": (("status", "summary"), ("reply_to",)),
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
    kind_fields = KIND_FIELDS.get(kind) if isinstance(kind, str) else None
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
    if kind in KIND_FIELDS:
        req, opt = KIND_FIELDS[kind]
        complete = all((val := out.get(f)) is not None
                       and not (isinstance(val, str) and not val.strip())
                       for f in req)
        if complete:
            allowed = {"say", "kind", *SIDE_FIELDS, *req, *opt}   # side fields ride ANY kind
            out = {k: v for k, v in out.items() if k in allowed}
    return out


# One flat per-kind checker on purpose: this function IS the action contract's single home;
# splitting it per kind would scatter what a turn may do across files.
def validate_action(obj: dict, allowed_kinds: set[str] | None = None,  # noqa: C901, PLR0912, PLR0915 — the action contract's single home, one flat checker per kind
                    grants=None) -> list[str]:
    """Semantic per-kind checks on an object that already passed the JSON Schema.
    `allowed_kinds` narrows the vocabulary to a workflow's `tools:` allowlist; `grants`
    (a grantpolicy.GrantPolicy) enforces the routine's user-set CAPABILITIES (write_util,
    reserved utils, runs/ access, own-recipe/config writes) — so allowed kinds =
    workflow tools ∩ (base ∪ capabilities). `finish` is always permitted so a run can
    end. Both rejections happen here, inside the schema-retry cycle, so a denied call is
    corrected and never becomes a turn. Returns a list of problems (empty = valid).
    """
    problems: list[str] = []
    kind = obj.get("kind")
    if kind not in KIND_FIELDS:
        return [f"unknown kind {kind!r}"]
    if allowed_kinds is not None and kind not in ALWAYS_KINDS and kind not in allowed_kinds:
        return [f"kind={kind} is not available in this workflow — it permits only "
                f"{sorted(allowed_kinds | set(ALWAYS_KINDS))}; use one of those"]
    if grants is not None and kind not in ALWAYS_KINDS and (denial := grants.deny(obj)):
        return [denial]
    required, optional = KIND_FIELDS[kind]
    for field in required:
        val = obj.get(field)
        if val is None or (isinstance(val, str) and not val.strip()):
            problems.append(f"kind={kind} requires a non-empty {field!r} field")
    if kind == "write_util":
        has_content = obj.get("content") is not None
        has_anchor = obj.get("anchor") is not None
        has_path = bool(obj.get("path"))
        if has_content and not isinstance(obj["content"], str):
            problems.append("kind=write_util requires 'content' to be the script text "
                            "(one string)")
        if not has_content and not has_anchor and not has_path:
            problems.append("kind=write_util needs 'content' (the COMPLETE script), or "
                            "'path' (a readable file the engine installs BYTE-FAITHFULLY "
                            "as the script — for large pre-built utils), or — to patch an "
                            "EXISTING util in place — 'anchor' + 'replacement' "
                            "(edit mode, like edit_file; no full re-emit needed)")
        if has_content and has_anchor:
            problems.append("kind=write_util takes 'content' OR 'anchor'/'replacement', not "
                            "both — a full rewrite and an in-place edit are different intents")
        if has_path and (has_content or has_anchor):
            problems.append("kind=write_util takes 'path' ALONE — it IS the content source "
                            "(the file's exact bytes); drop 'content'/'anchor'")
        if has_anchor and not isinstance(obj["anchor"], str):
            problems.append("kind=write_util: 'anchor' must be a string (the exact text to "
                            "find in the util's current source)")
        if "replacement" in obj and not isinstance(obj["replacement"], str):
            problems.append("kind=write_util: 'replacement' must be a string "
                            '("" deletes the anchor)')
        # The name becomes a directory under the library — a non-slug (path separators,
        # dots) would write OUTSIDE utils/; rejected here like every permission problem.
        if not is_slug(str(obj.get("name") or "")):
            problems.append("kind=write_util requires 'name' to be a kebab-case util name")
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
    if kind == "create_routine" and not is_slug(str(obj.get("target") or "")):
        problems.append("kind=create_routine requires 'target' to be a kebab-case slug for the "
                        "new routine")
    # A `model` override (llm/spawn/subtask) is validated at DISPATCH, not here: it may
    # name a role OR a catalog model, and only the executor sees the catalog — its
    # teaching rejection lists the real alternatives (list_models shows the same).
    if kind == "manage_group":
        verb = str(obj.get("verb") or "").strip()
        verbs = ("list", "create", "update", "delete", "set-default", "run")
        if verb not in verbs:
            problems.append(f"kind=manage_group requires 'verb' to be one of {list(verbs)}")
        elif verb == "create" and not str(obj.get("name") or "").strip():
            problems.append("kind=manage_group verb=create requires 'name' (the group's name)")
        elif verb in ("update", "delete", "run") and not str(obj.get("target") or "").strip():
            problems.append(f"kind=manage_group verb={verb} requires 'target' (the group id)")
        elif verb == "set-default" and not str(obj.get("on_failure") or "").strip():
            problems.append("kind=manage_group verb=set-default requires 'on_failure' "
                            "('stop' or 'continue')")
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
    # `closes` is a property OF an answer — a terminal acknowledgment. Without `answers`
    # there is no exchange to complete, so a bare closes is a contradiction, not a no-op.
    if kind == "report" and obj.get("closes") and not str(obj.get("answers") or "").strip():
        problems.append("kind=report: 'closes' is valid only together with 'answers' — it "
                        "marks the ANSWER as completing that exchange")
    if kind == "ask_user" and "request" in obj and not isinstance(obj["request"], str):
        problems.append('kind=ask_user: \'request\' must be ONE entity id string, "<class>:'
                        '<name>" (e.g. "util:discord") — file one request per ask')
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
    # The side fields are gated on their own terms (the capability rides the FIELD, not the
    # kind), so this runs outside the ALWAYS_KINDS exemption above: a `remind` on a `report`
    # must meet the same bar as one on a `util`.
    problems += reminder_field_problems(obj, grants)
    allowed = {"say", "kind", *SIDE_FIELDS, *required, *optional}   # side fields ride ANY kind
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


