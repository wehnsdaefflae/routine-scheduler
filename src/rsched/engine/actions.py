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
from . import fieldshapes, notemeta, thenscript
from .actionschema import KINDS, PSEUDO_UTILS
from .askfields import ASK_OPTIONS_MAX
from .askfields import field_problems as ask_field_problems
from .decideaction import field_problems as decide_field_problems
from .goalops import field_problems as goal_field_problems
from .jobfields import field_problems as job_field_problems
from .kindaliases import rewrite as alias_rewrite
from .kindexamples import KIND_EXAMPLES  # noqa: F401 — re-exported: importers read it here
from .reportfields import field_problems as report_field_problems
from .sidefields import problems as side_field_problems
from .sidefieldset import SIDE_FIELDS  # the fields riding EVERY kind beside `say`

# Kinds available on EVERY turn regardless of the workflow's `tools:` allowlist and the
# capability layer: `finish` so a run can always end, `report` so work that is not the run's
# task can always be routed when noticed, and `list_models` (read-only) so the per-call
# `model` override is usable.
ALWAYS_KINDS = ("finish", "report", "list_models")


# The kinds a run may mark `background: true` (D118). Reads and fetches defer because their only
# effect IS the observation; the three MUTATIONS each declare the one target they write, and
# `engine/background_order.py` holds any later action touching it until the write lands. NOT here:
#   · delete, move, mkdir — not worth deferring, and `move` has two ends;
#   · write_util, write_rule — gated in `actionroute._route`, no `executor.DISPATCH` entry;
#   · the control kinds, which steer the run or are already asynchronous (children).
# `shell` declares no target, so a WRITING shell command stays the caller's ordering hazard.
BACKGROUNDABLE_KINDS = ("util", "websearch", "code_search", "script", "shell", "llm", "decide",
                        "read_file", "view_image", "memory_read", "read_rule",
                        "write_file", "edit_file", "memory_write")

# kind → (required fields, allowed extra fields beyond say/kind). `background` is appended below
# for BACKGROUNDABLE_KINDS, never listed here: that tuple is the one statement of which defer.
_OWN_FIELDS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "util": (("name",), ("args", "timeout_s")),
    # D290: SPELLINGS of the util call, rewritten into it before any gate (kindaliases.py).
    # `args` is REQUIRED so an alias naming no query is refused without spending a turn.
    "websearch": (("args",), ("timeout_s",)),
    "code_search": (("args",), ("timeout_s",)),
    "script": (("name",), ("args", "timeout_s")),
    "shell": (("command",), ("timeout_s", "path")),
    "write_util": (("name",), ("content", "path", "anchor", "replacement", "all")),
    "remove_util": (("name",), ()),
    "schedule_run": (("target",), ("fire_at", "reason", "cancel", "id")),
    "create_routine": (("target", "name", "prompt"),
                       ("workflow", "pattern", "setup", "done_when", "finish_line", "never",
                        "produces", "replaces", "revises")),   # D198: `revises` = that recipe
    "manage_lane": (("verb",), ("target", "name", "members", "on_failure", "cron", "paused")),
    "job": (("verb",), ("id", "title", "brief", "path", "state", "outcome", "summary",
                         "wake", "quiet_days", "accounting")),
    "goal": (("verb",), ("id", "goals", "quote", "evidence")),
    "read_file": ((), ("path", "paths", "start_line", "start_char", "max_lines")),
    "view_image": ((), ("path", "paths", "prompt")),
    "write_file": (("path", "content"), ("append", "then_script")),
    "delete": (("path",), ("recursive",)),
    "move": (("src", "dst"), ()),
    "mkdir": (("path",), ("parents",)),
    "edit_file": (("path", "anchor"), ("replacement", "all", "then_script")),
    "memory_read": ((), ("name", "args")),   # a note, or a past-run search
    "memory_write": (("name",), ("content", "about", "delete", "anchor", "replacement", "all",
                                 "stale_after", "verified")),   # D268: notemeta
    "read_rule": (("name",), ()),
    "write_rule": (("name",), ("content", "anchor", "replacement", "all")),
    "llm": (("prompt",), ("system", "response_schema", "model")),
    "decide": ((), ("question", "answer_type", "options", "questions", "evidence", "files",
                    "model")),
    # D241: ONE kind for a child run, its scheduling in a flat `mode` (childfields.py)
    "child": (("prompt",), ("mode", "workflow", "label", "turns", "model")),
    "detach": (("prompt",), ("workflow", "label")),
    "list_models": ((), ()),
    "children": ((), ()),
    # `n` (a sub-workflow) OR `handle` (a background call, D176); validate_action demands one
    "kill": ((), ("n", "handle")),
    "wait": ((), ("n", "all", "timeout_s")),
    # D212: `question` OR a `questions` batch; askfields.field_problems demands one
    "ask_user": ((), ("question", "questions", "mode", "options", "default", "config_patch",
                      "request", "blocks_work")),
    # `options` also files the report as a decision on the operator's Decisions page (F621)
    "report": (("title",), ("detail", "target", "answers", "closes", "supersedes", "settles",
                            "options")),
    # D186: no fields of its own — the reasoning IS `say` (its non-emptiness checked below)
    "do_nothing": ((), ()),
    "finish": (("status", "summary"), ("accounting", "reply_to", "final")),
}
KIND_FIELDS = {kind: (req, (*opt, "background") if kind in BACKGROUNDABLE_KINDS else opt)
               for kind, (req, opt) in _OWN_FIELDS.items()}


def normalize_action(obj: dict) -> dict:
    """Strip grammar-padding: constrained decoders (Ollama json_schema, OpenRouter strict)
    tend to emit OTHER kinds' fields as empty strings/false/null. Empty-valued fields that
    are not required for this kind carry no information — drop them so the semantic
    validator sees the model's intent, not the grammar's debris.

    An ALIAS KIND is rewritten FIRST into the util action it spells (D290): its payload often
    sits in an undeclared field the stripping below would delete, and every gate then meets
    it as the ordinary util call it is.
    """
    if (aliased := alias_rewrite(obj)) is not None:
        obj = aliased
    # weak models sometimes wrap the action in a generic tool-call envelope — unwrap it
    if "kind" not in obj and isinstance(obj.get("action"), dict):   # {"action": {...}}
        obj = obj["action"]
    # …and only an object STILL without a kind is a tool-call envelope (else `util name=report`
    # would read as a `report` action)
    if "kind" not in obj:
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
        elif key == "final" and kind == "finish" and isinstance(val, bool):
            # the one boolean whose `false` is a DECLARATION (not final), never padding
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
            # …except `background` on a kind that may not defer: dropped, the action would run
            # synchronously while the model believes it deferred; kept, validate_action refuses
            # it with the reason.
            if obj.get("background") is not None and kind not in BACKGROUNDABLE_KINDS:
                allowed.add("background")
            out = {k: v for k, v in out.items() if k in allowed}
    return fieldshapes.canonical(out)   # an evidently-intended shape, made canonical


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
        return [(f"kind={kind} is not available in this workflow — it permits only "
                 f"{sorted(allowed_kinds | set(ALWAYS_KINDS))}; use one of those")]
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
        # The name becomes a directory under the library — a non-slug (path separators,
        # dots) would write OUTSIDE utils/; rejected here like every permission problem.
        if not is_slug(str(obj.get("name") or "")):
            problems.append("kind=write_util requires 'name' to be a kebab-case util name")
        elif obj["name"] in PSEUDO_UTILS:
            problems.append(f"kind=write_util: {obj['name']!r} is one of the util action's own "
                            f"catalog verbs ({', '.join(PSEUDO_UTILS)}) — a util by that name "
                            "could never be called; pick another name")
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
    if kind == "decide":
        problems += decide_field_problems(obj)
    if kind == "ask_user":   # D212: the whole shape lives in `askfields`
        problems += ask_field_problems(obj)
    # the schema's `options` cap is decide's; a pick-list the OPERATOR reads stays short
    if kind == "report" and len(obj.get("options") or []) > ASK_OPTIONS_MAX:
        problems.append(f"kind={kind}: at most {ASK_OPTIONS_MAX} 'options' — a longer list "
                        "is a question to rephrase, not a menu")
    if kind == "create_routine" and not is_slug(str(obj.get("target") or "")):
        problems.append("kind=create_routine requires 'target' to be a kebab-case slug for the "
                        "new routine")
    # A `model` override is validated at DISPATCH: only the executor sees the catalog.
    if kind == "manage_lane":
        verb = str(obj.get("verb") or "").strip()
        verbs = ("list", "create", "update", "delete", "set-default", "run")
        if verb not in verbs:
            problems.append(f"kind=manage_lane requires 'verb' to be one of {list(verbs)}")
        elif verb == "create" and not str(obj.get("name") or "").strip():
            problems.append("kind=manage_lane verb=create requires 'name' (the lane's name)")
        elif verb in ("update", "delete", "run") and not str(obj.get("target") or "").strip():
            problems.append(f"kind=manage_lane verb={verb} requires 'target' (the lane id)")
        elif verb == "set-default" and not str(obj.get("on_failure") or "").strip():
            problems.append("kind=manage_lane verb=set-default requires 'on_failure' "
                            "('stop' or 'continue')")
    if kind == "job":
        problems += job_field_problems(obj)
    if kind == "goal":
        problems += goal_field_problems(obj)
    # D186: `do_nothing` IS its reasoning, so it is the one kind that checks `say` non-empty.
    if kind == "do_nothing" and not str(obj.get("say") or "").strip():
        problems.append("kind=do_nothing requires a non-empty 'say' — it IS the action: what "
                        "you checked, and why nothing is due. A do_nothing with no reasoning "
                        "records that the run skipped a turn and nothing about why")
    if kind == "finish" and "final" in obj and not isinstance(obj["final"], bool):
        problems.append("kind=finish: 'final' is true or false — is this reply final?")
    if kind == "kill":
        # One kill, two namespaces (D176): a sub-workflow NUMBER or a background HANDLE.
        has_n = obj.get("n") is not None
        has_handle = bool(str(obj.get("handle") or "").strip())
        if has_n and has_handle:
            problems.append("kind=kill addresses ONE thing: 'n' (a sub-workflow number) or "
                            "'handle' (a background call's bg1/bg2 …), never both — drop the "
                            "one you did not mean")
        elif not has_n and not has_handle:
            problems.append("kind=kill requires 'n' (the sub-workflow number — `children` "
                            "lists them) or 'handle' (a background call's handle, bg1/bg2 …, "
                            "from the started observation that named it)")
    if kind in ("read_file", "view_image"):
        # the schema bounds `paths`; it cannot say none of them is blank
        paths = obj.get("paths")
        if paths is not None and not all(p.strip() for p in paths):
            problems.append(f"kind={kind}: 'paths' must be a list of non-empty path strings")
            paths = None
        if not str(obj.get("path") or "").strip() and not paths:
            problems.append(f"kind={kind} requires 'path' (one file) or 'paths' (several)")
        elif str(obj.get("path") or "").strip() and paths:
            problems.append(f"kind={kind} takes 'path' OR 'paths', not both")
    if kind == "report":
        problems += report_field_problems(obj)
    # .memory/ is reachable ONLY through the memory actions — the engine owns INDEX.md there;
    # generic file access would silently bypass it.
    if kind in ("read_file", "view_image", "write_file", "edit_file",
                "delete", "move", "mkdir"):
        multi = obj.get("paths") or [] if kind in ("read_file", "view_image") else []
        raws = [obj.get("path"), *multi]
        if kind == "move":
            raws += [obj.get("src"), obj.get("dst")]
        for raw in raws:
            rel = str(raw or "")
            while rel.startswith("./"):
                rel = rel[2:]
            if rel == ".memory" or rel.startswith(".memory/"):
                problems.append(f"kind={kind} may not touch .memory/ — use memory_read / "
                                "memory_write (the engine maintains .memory/INDEX.md for you)")
                break
    # A rule is read by its catalog SLUG: the name is joined onto the library dir, so a path
    # would read past the run's fs jail.
    if kind == "read_rule" and (name := str(obj.get("name") or "")) and not is_slug(name):
        problems.append(f"kind=read_rule: 'name' must be a rule's kebab-case slug as the "
                        f'catalog lists it, or "list" for the catalog — got {name!r}')
    if kind in ("memory_read", "memory_write"):
        name = str(obj.get("name") or "")
        if kind == "memory_read" and bool(name) == bool(obj.get("args")):
            problems.append("memory_read: pass EITHER 'name' (a note) OR 'args' (search words)")
        if name and not is_slug(name) and not notemeta.index_read(kind, name):
            problems.append(f"kind={kind}: 'name' must be a kebab-case topic slug, got {name!r}")
        if kind == "memory_write" and notemeta.is_index_name(name):
            problems.append("memory_write: 'index' is reserved — the engine maintains "
                            ".memory/INDEX.md from each note's 'about' line")
        if kind == "memory_write" and not obj.get("delete"):
            # THREE modes: delete (above), EDIT MODE (`anchor` + optional `replacement`), and
            # the whole note (`content` + `about`). Mixing edit mode with `content` is refused.
            if obj.get("anchor") is not None:
                if obj.get("content") is not None:
                    problems.append("memory_write: pass EITHER 'content' (the whole note) OR "
                                    "'anchor'/'replacement' (an in-place edit), not both")
                if not str(obj.get("anchor") or ""):
                    problems.append("memory_write: 'anchor' must be the exact text to replace, "
                                    "copied verbatim from memory_read")
            elif obj.get("replacement") is not None or obj.get("all") is not None:
                problems.append("memory_write: 'replacement'/'all' belong to edit mode — pass "
                                "the 'anchor' (the exact text to replace) with them")
            else:
                content = obj.get("content")
                if not isinstance(content, str) or not content.strip():
                    problems.append("memory_write requires 'content' (the note's full markdown, "
                                    "one string) unless delete: true, or 'anchor' + "
                                    "'replacement' to edit the note in place")
                if not str(obj.get("about") or "").strip():
                    problems.append("memory_write requires 'about' (the note's one-line INDEX "
                                    "entry) unless delete: true")
    # A script riding a write or edit is validated as the script action it is: the recipe's
    # `tools:` list and the capability layer judge it exactly as they would judge it alone.
    if kind in thenscript.HOSTS:
        problems += thenscript.problems(obj, allowed_kinds, grants)
    # The side fields are gated on their own terms (the capability rides the FIELD), so this
    # runs outside the ALWAYS_KINDS exemption above.
    problems += side_field_problems(obj, grants)   # the fields riding ANY kind (engine/sidefields)
    # `background` on a kind that may not defer is refused with the REASON, which the generic
    # stray-field message below would not teach.
    if obj.get("background") is not None and kind not in BACKGROUNDABLE_KINDS:
        problems.append(
            f"kind={kind} cannot run in the background: only a read, a fetch, or a mutation "
            f"that declares the one target it writes may be deferred "
            f"({', '.join(BACKGROUNDABLE_KINDS)}) — anything else would leave a later action "
            f"reading state it has not changed yet. Drop 'background' and run it synchronously")
    allowed = {"say", "kind", *SIDE_FIELDS, *required, *optional}   # side fields ride ANY kind
    if obj.get("background") is not None and kind not in BACKGROUNDABLE_KINDS:
        allowed.add("background")   # the specific refusal above said it; don't say it twice
    if stray := [k for k in obj if k not in allowed]:
        problems.append(
            f"fields {stray} do not belong to kind={kind} (allowed: {sorted(allowed)})")
    return problems
