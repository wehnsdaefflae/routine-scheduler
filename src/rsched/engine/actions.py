"""The action schema — the single source of truth for what an orchestrator turn may do.

Deliberately FLAT (one object, `kind` enum, optional fields, no oneOf): weak local models and
Ollama's grammar conversion handle flat schemas far better. Per-kind required-field checks
happen in code (`validate_action`) so the JSON-Schema layer stays permissive and the model
gets precise, actionable error messages.

`say` comes first on purpose: giving the model its narration outlet inside the JSON reduces
prose-outside-JSON failures.
"""

from __future__ import annotations

import re
from datetime import date

from ..ids import is_slug
from ..reports import REPORT_ID_RE
from . import thenscript
from .actionschema import KINDS, PSEUDO_UTILS
from .decideaction import field_problems as decide_field_problems
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
#: ask_user's pick-one list. The shared schema field allows decide's longer lists, so this cap
#: is held here rather than by the schema.
ASK_OPTIONS_MAX = 5


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
                       "workflow": "general-task", "pattern": "watcher",
                       "setup": ["<the user's answer to one of the pattern's questions>"],
                       "done_when": [("<what one finished run leaves behind, in the user's "
                                      "words>")],
                       "never": ["<what a run must never do, in the user's words>"]},
    "manage_lane": {"say": "<why this lane change now>", "kind": "manage_lane",
                     "verb": "create", "name": "Morning jobs",
                     "members": ["weight-coach", "news-digest"]},
    "task": {"say": "<what this run did for the open task>", "kind": "task",
             "verb": "checkpoint", "id": "client-onboarding", "outcome": "advanced",
             "summary": "<what landed, what the next run picks up>",
             "accounting": ["d1 met: <evidence>"]},

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
    "delete": {"say": "<why delete this>", "kind": "delete", "path": "state/stale-cache/",
                "recursive": True},
    "move": {"say": "<why move it>", "kind": "move", "src": "artifacts/draft.md",
              "dst": "state/final/draft.md"},
    "mkdir": {"say": "<why this dir>", "kind": "mkdir", "path": "state/weekly-digests",
               "parents": True},
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
    "decide": {"say": "<why this decision now>", "kind": "decide",
               "question": "<what to decide>",
               "options": ["<value>: <what it means>", "<other value>: <what it means>"],
               "evidence": "<the text or facts the answer depends on>"},
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

# The kinds a run may mark `background: true` (D118 phase 1). Every member is a READ or a
# FETCH whose only effect is the observation it returns, which is what makes deferring it safe:
# the one-action-per-turn contract exists to keep STATE CHANGES ordered, and these change no
# state a later action could read stale. Deliberately NOT here:
#   · the mutations — write_file, edit_file, write_util, remove_util, memory_write, write_rule,
#     delete, move, mkdir — where a later synchronous action would read what the background
#     call has not written yet (that needs the dependency/barrier model, D118 phase 3);
#   · the control kinds — finish, ask_user, report, spawn, subtask, wait, kill, detach,
#     create_routine, manage_lane, schedule_run — which either steer the run (so they must be
#     synchronous) or are already asynchronous in their own right (the children).
# `shell` IS here, as the one member worth arguing about: a shell command can obviously write.
# It is included because the design's motivating example — a ten-minute test run — reaches the
# host through `shell`/`script`, and because the alternative is a run that backgrounds its
# reads and still sits for the one call that actually takes minutes. The honest statement of
# the rule, then: these kinds are backgroundable, and marking a WRITING shell command
# `background` is the caller's own ordering hazard, the same one it already owns when it runs a
# write through a util.
BACKGROUNDABLE_KINDS = ("util", "script", "shell", "llm", "decide",
                        "read_file", "view_image", "memory_read", "read_rule")

# kind → (required fields, allowed extra fields beyond say/kind)
KIND_FIELDS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "util": (("name",), ("args", "timeout_s", "background")),
    "script": (("name",), ("args", "timeout_s", "background")),
    "shell": (("command",), ("timeout_s", "path", "background")),
    "write_util": (("name",), ("content", "path", "anchor", "replacement", "all")),
    "remove_util": (("name",), ()),
    "schedule_run": (("target",), ("fire_at", "reason", "cancel", "id")),
    "create_routine": (("target", "name", "prompt"),
                       ("workflow", "pattern", "setup", "done_when", "finish_line", "never")),
    "manage_lane": (("verb",), ("target", "name", "members", "on_failure", "cron",
                                 "paused")),
    "task": (("verb",), ("id", "title", "brief", "path", "state", "outcome", "summary",
                         "wake", "quiet_days", "accounting")),
    "read_file": ((), ("path", "paths", "start_line", "max_lines", "background")),
    "view_image": ((), ("path", "paths", "prompt", "background")),
    "write_file": (("path", "content"), ("append", "then_script")),
    "delete": (("path",), ("recursive",)),
    "move": (("src", "dst"), ()),
    "mkdir": (("path",), ("parents",)),
    "edit_file": (("path", "anchor"), ("replacement", "all", "then_script")),
    "memory_read": (("name",), ("background",)),
    "memory_write": (("name",), ("content", "about", "delete")),
    "read_rule": (("name",), ("background",)),
    "write_rule": (("name",), ("content", "anchor", "replacement", "all")),
    "llm": (("prompt",), ("system", "response_schema", "model", "background")),
    "decide": ((), ("question", "answer_type", "options", "questions", "evidence", "files",
                    "model", "background")),
    "spawn": (("prompt",), ("workflow", "label", "model")),
    "subtask": (("prompt",), ("workflow", "label", "turns", "model")),
    "detach": (("prompt",), ("workflow", "label")),
    "list_models": ((), ()),
    "subruns": ((), ()),
    "kill": (("n",), ()),
    "wait": ((), ("n", "all", "timeout_s")),
    "ask_user": (("question",), ("mode", "options", "default", "config_patch", "request")),
    "report": (("title",), ("detail", "target", "answers", "closes", "supersedes", "settles")),
    "finish": (("status", "summary"), ("accounting", "reply_to")),
}


def normalize_action(obj: dict) -> dict:
    """Strip grammar-padding: constrained decoders (Ollama json_schema, OpenRouter strict)
    tend to emit OTHER kinds' fields as empty strings/false/null. Empty-valued fields that
    are not required for this kind carry no information — drop them so the semantic
    validator sees the model's intent, not the grammar's debris.
    """
    # weak models sometimes wrap the action in a generic tool-call envelope — unwrap it
    if "kind" not in obj and isinstance(obj.get("action"), dict):   # {"action": {...}}
        obj = obj["action"]
    # …and only an object STILL without a kind is a tool-call envelope: reading an unwrapped
    # action's own `name` as the tool turned `util name=report` into a `report` action
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
            # …except `background` on a kind that may not defer (D118 phase 1). Dropping it
            # here is the one silent failure this field must never have: the action would run
            # SYNCHRONOUSLY while the model believes it was deferred, and nothing would say
            # so — measured on this very path, where `write_file` + background validated
            # clean. Keeping it makes `validate_action` refuse it with the reason, inside the
            # schema-retry cycle, so the turn is corrected rather than quietly reinterpreted.
            if obj.get("background") is not None and kind not in BACKGROUNDABLE_KINDS:
                allowed.add("background")
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
    # The schema's `options` cap is decide's (26); ask_user's pick-list stays a short one.
    if kind == "ask_user" and len(obj.get("options") or []) > ASK_OPTIONS_MAX:
        problems.append(f"kind=ask_user: at most {ASK_OPTIONS_MAX} 'options' — a longer list "
                        "is a question to rephrase, not a menu")
    if kind == "create_routine" and not is_slug(str(obj.get("target") or "")):
        problems.append("kind=create_routine requires 'target' to be a kebab-case slug for the "
                        "new routine")
    # A `model` override (llm/spawn/subtask) is validated at DISPATCH, not here: it may
    # name a role OR a catalog model, and only the executor sees the catalog — its
    # teaching rejection lists the real alternatives (list_models shows the same).
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
    if kind == "task":
        problems += task_field_problems(obj)
    if kind in ("read_file", "view_image"):
        # The schema already holds `paths` to a list of at most READ_PATHS_MAX strings; what it
        # cannot say is that none of them is blank.
        paths = obj.get("paths")
        if paths is not None and not all(p.strip() for p in paths):
            problems.append(f"kind={kind}: 'paths' must be a list of non-empty path strings")
            paths = None
        if not str(obj.get("path") or "").strip() and not paths:
            problems.append(f"kind={kind} requires 'path' (one file) or 'paths' (several)")
        elif str(obj.get("path") or "").strip() and paths:
            problems.append(f"kind={kind} takes 'path' OR 'paths', not both")
    # `closes` is a property OF a disposal — a terminal acknowledgment. `answers` disposes of
    # ONE exchange, `settles` of several; with neither there is nothing to complete, so a bare
    # closes is a contradiction, not a no-op. Before D134 only `answers` counted, which is why
    # a report that asked nothing back could not be born settled at all: it answers no single
    # row, so `closes` was refused and the carrier stayed open forever (F497).
    if (kind == "report" and obj.get("closes")
            and not str(obj.get("answers") or "").strip()
            and not obj.get("settles")):
        problems.append("kind=report: 'closes' is valid only together with 'answers' or "
                        "'settles' — it marks the disposal as completing those exchanges")
    # `answers` is ONE report id, held to the grammar `settles` and `supersedes` are. The ledger
    # matches it against ids, so free text there disposed of nothing — while the run, and its
    # observation, believed the exchange answered (the settled-in-prose, open-in-the-ledger
    # shape F497 measured).
    if (kind == "report" and (answered := str(obj.get("answers") or "").strip())
            and not REPORT_ID_RE.match(answered.upper())):
        problems.append(f"kind=report: 'answers' takes the ONE report id you received and are "
                        f"replying to, like R123 — not {answered[:60]!r}")
    # Settling rows CLAIMS they are finished. It needs no `target` (each settled row already has
    # its own raiser) but it does need well-formed ids, and a row cannot be both answered and
    # settled by one reply, nor both settled and folded — those say opposite things about who
    # holds the work next.
    if kind == "report" and obj.get("settles"):
        if bad := [str(i) for i in obj["settles"]
                   if not REPORT_ID_RE.match(str(i).strip().upper())]:
            problems.append(f"kind=report: 'settles' takes report ids like R123 — not "
                            f"{', '.join(bad[:3])}")
        elif (answered := str(obj.get("answers") or "").strip().upper()) and answered in [
                str(i).strip().upper() for i in obj["settles"]]:
            problems.append(f"kind=report: {answered} is already settled by 'answers' — naming "
                            "it in 'settles' too says the same thing twice. List only the "
                            "OTHER rows this reply disposes of")
        elif overlap := sorted({str(i).strip().upper() for i in obj["settles"]}
                               & {str(i).strip().upper() for i in (obj.get("supersedes") or [])}):
            # Folding a row makes this report its thread, so it stays live until this one
            # settles; settling it declares it finished now. Both at once on the same id is a
            # contradiction the read model would have to break one way or the other.
            problems.append(f"kind=report: {', '.join(overlap[:3])} cannot be both 'settles' "
                            "and 'supersedes' — settling DECLARES a row finished, taking it "
                            "over CONTINUES it here. Pick one")
    # Taking a row over means becoming its OWNER's thread. Without a target there is no owner
    # to become, and the folded rows would leave triage for nowhere.
    if kind == "report" and obj.get("supersedes"):
        if not str(obj.get("target") or "").strip():
            problems.append("kind=report: 'supersedes' needs 'target' — folding rows into this "
                            "report hands them to that owner, so name who is taking them")
        elif bad := [str(i) for i in obj["supersedes"]
                     if not REPORT_ID_RE.match(str(i).strip().upper())]:
            problems.append(f"kind=report: 'supersedes' takes report ids like R123 — not "
                            f"{', '.join(bad[:3])}")
        elif (answered := str(obj.get("answers") or "").strip().upper()) and answered in [
                str(i).strip().upper() for i in obj["supersedes"]]:
            # Answering a thread ends it; absorbing one continues it here. Both at once on the
            # same id is a contradiction, and the fold would win silently — leaving the row
            # reading THIS report's status while the run believed it had closed it.
            problems.append(f"kind=report: {answered} cannot be both 'answers' and "
                            "'supersedes' — answering ENDS that thread, taking it over "
                            "CONTINUES it here. Pick one")
    # .memory/ is reachable ONLY through the memory actions — the engine owns INDEX.md and
    # enforces the note cap there; generic file access would silently bypass both.
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
    # A rule is read by its catalog SLUG. The name is joined onto the library dir, so a path
    # here read any .md file on the host past the run's fs jail (memops.do_read_rule).
    if kind == "read_rule" and (name := str(obj.get("name") or "")) and not is_slug(name):
        problems.append(f"kind=read_rule: 'name' must be a rule's kebab-case slug as the "
                        f'catalog lists it, or "list" for the catalog — got {name!r}')
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
    # A script riding a write or edit is validated as the script action it is: the recipe's
    # `tools:` list and the capability layer judge it exactly as they would judge it alone.
    if kind in thenscript.HOSTS:
        problems += thenscript.problems(obj, allowed_kinds, grants)
    # The side fields are gated on their own terms (the capability rides the FIELD, not the
    # kind), so this runs outside the ALWAYS_KINDS exemption above: a `remind` on a `report`
    # must meet the same bar as one on a `util`.
    problems += reminder_field_problems(obj, grants)
    # `background` is refused on the kinds that may not defer, with the REASON and the list
    # (D118 phase 1). The stray-field check below would already reject it — but as one name
    # inside "fields [...] do not belong to kind=write_file (allowed: [thirty names])", which
    # teaches the model nothing about why deferring a write is unsafe, and a retry that does
    # not know the rule spends the same turn again.
    if obj.get("background") is not None and kind not in BACKGROUNDABLE_KINDS:
        problems.append(
            f"kind={kind} cannot run in the background: only reads and fetches may be "
            f"deferred ({', '.join(BACKGROUNDABLE_KINDS)}), because a later action would "
            f"otherwise read state this one has not finished changing. Drop 'background' and "
            f"run it synchronously")
    allowed = {"say", "kind", *SIDE_FIELDS, *required, *optional}   # side fields ride ANY kind
    if obj.get("background") is not None and kind not in BACKGROUNDABLE_KINDS:
        allowed.add("background")   # the specific refusal above said it; don't say it twice
    stray = [k for k in obj if k not in allowed]
    if stray:
        problems.append(
            f"fields {stray} do not belong to kind={kind} (allowed: {sorted(allowed)})"
        )
    return problems


#: The `task` action's verbs (engine/taskops.py) — the shared `verb` property's enum carries
#: manage_lane's too, so the per-kind check is what holds a task turn to its own.
TASK_VERBS = ("list", "create", "update", "open", "checkpoint", "delete")


def task_field_problems(obj: dict) -> list[str]:
    """The shape a `task` action must have. Whether the task EXISTS, is open or is due is the
    handler's to judge against the store (engine/taskops.py) — this is only the grammar, so a
    malformed call is corrected inside the schema-retry cycle and never costs a turn.
    """
    verb = str(obj.get("verb") or "")
    if verb not in TASK_VERBS:
        return [f"kind=task requires 'verb' to be one of {list(TASK_VERBS)}"]
    out = []
    tid = str(obj.get("id") or "")
    if verb in ("create", "update", "checkpoint", "delete") and not tid:
        out.append(f"kind=task verb={verb} requires 'id' (the task's kebab-case id)")
    elif tid and not is_slug(tid):
        out.append(f"kind=task: 'id' must be a kebab-case task id, got {tid!r}")
    if verb == "create":
        out += [f"kind=task verb=create requires a non-empty {f!r}"
                for f in ("title", "brief") if not str(obj.get(f) or "").strip()]
    if verb == "checkpoint":
        if not obj.get("outcome"):
            out.append("kind=task verb=checkpoint requires 'outcome' (advanced, no-work, "
                       "blocked or deferred)")
        if not str(obj.get("summary") or "").strip():
            out.append("kind=task verb=checkpoint requires 'summary' — what this run did for "
                       "the task and what the next run picks up")
    if (wake := obj.get("wake")) not in (None, ""):
        try:
            date.fromisoformat(str(wake))
        except ValueError:
            out.append(f"kind=task: 'wake' is a date YYYY-MM-DD (or \"\" to clear it), got "
                       f"{wake!r}")
    stray = {"outcome", "summary", "accounting"} & set(obj) if verb != "checkpoint" else set()
    if stray:
        out.append(f"kind=task: {sorted(stray)} belong to verb=checkpoint only")
    return out


#: A name that could BE a util: the library's kebab-case rule, narrowed by the one thing
#: `ids.is_slug` does not require — at least one LETTER. Every util in the catalog satisfies
#: it; a bare timeout number, a path, a quoted JSON fragment and a paragraph of prose do not.
_NAMEABLE_UTIL_RE = re.compile(r"^(?=[a-z0-9-]*[a-z])[a-z0-9][a-z0-9-]*$")


def could_be_util(name: str) -> bool:
    """Could `name` name a util at all? The F546 test every per-util telemetry tick passes —
    a value that cannot is a field shift, and attributing it invents a permanent phantom row
    on the Stats tab — and the signature `field_shift_diagnosis` names.
    """
    return bool(_NAMEABLE_UTIL_RE.match(name))


def util_rejection_outcome(obj: dict, allowed_kinds: set[str] | None = None,
                           grants=None) -> tuple[str, str] | None:
    """Classify a REJECTED util action for per-util telemetry (RunContext.count_util):
    returns (util name, "denied" | "rejected") or None when the rejection is not
    attributable to a util. "denied" = a permission refusal (a reserved util switched
    off, the util kind excluded by the workflow's tools:) — Mark's "permission problem";
    "rejected" = a malformed call (schema/field problems). A denial never reaches the
    executor — it is corrected inside the schema-retry cycle and never becomes a turn —
    so it MUST be counted here at the validation seam or it would never be counted at
    all. The catalog pseudo-utils (PSEUDO_UTILS) are discovery, not execution: skipped.
    """
    name = str(obj.get("name") or "").strip()
    if obj.get("kind") != "util" or not name or name in PSEUDO_UTILS:
        return None
    # …and the name must be able to BE a util. A malformed action is by definition one whose
    # fields cannot be trusted, and the commonest malformation is a FIELD SHIFT — values
    # sliding into the wrong keys, so `name` holds prose, a timeout number or a path. That
    # string is not merely wrong here: count_util writes it to status.json, the DURABLE
    # workflow-usage stream carries it, and readmodels/util_stats builds its rows from
    # `set(catalog) | set(merged)` — so it becomes a permanent phantom util on the Stats tab
    # (~25 by 2026-09-24, incl. a paragraph of prose and a bare `300`; the specimen is
    # weightloss:20260923-220004, served by a fallback model after a 503 and a 402).
    #
    # The test is the util NAMING rule, not the slug rule: `ids.is_slug` admits `300` and
    # `c-20260821-060305` (digits and run ids are legal slugs), and the policy carries no
    # catalog of util names (write_util's create-vs-revise split asks the library per call),
    # so neither separates a name from a shifted value. What every real util name has and no
    # shifted value did: kebab-case with a LETTER in it. Unattributable IS what None means.
    if not could_be_util(name):
        return None
    denied = ((allowed_kinds is not None and "util" not in allowed_kinds)
              or (grants is not None and grants.deny(obj) is not None))
    return name, ("denied" if denied else "rejected")


def field_shift_diagnosis(obj: dict) -> str:
    """Name a whole-object FIELD SHIFT when the schema's own problem lines cannot (D146-C).

    A field shift is a degraded model's characteristic failure — values sliding one key over —
    and most of a shifted action stays schema-VALID, so the validator reports whichever field
    happened to carry a constraint. weightloss:20260923-220004 was told
    `timeout_s: 0 is less than the minimum of 1` eight times while the actual candidates were
    `kind='util'` with `name='300'`, `name='180'` and a whole sentence of prose in `name`. The
    correction described the symptom; the fault went unnamed, and the run concluded the failure
    was its own inability to hold the schema.

    The detectable signature is the same one F546's telemetry guard uses — `could_be_util`,
    the util naming rule — so the two cannot drift: a `kind: "util"` whose `name` could not be
    a util name at all is not a wrong util, it is a misplaced value. Returns "" when there is
    nothing specific to say, because a diagnosis that fires on ordinary mistakes would teach
    the model to ignore it.
    """
    if str(obj.get("kind") or "") != "util":
        return ""
    name = str(obj.get("name") or "").strip()
    if not name or could_be_util(name):
        return ""
    shown = name if len(name) <= 80 else name[:77] + "…"
    return (f"LOOK AT THE WHOLE OBJECT, not only the field named above: `name` holds "
            f"{shown!r}, which cannot be a util name (utils are kebab-case and contain a "
            f"letter). That is the signature of a FIELD SHIFT — your values are one key out "
            f"of place, and most of the object is still schema-valid, so the problem listed "
            f"above is a symptom rather than the fault. Rebuild the action from scratch: "
            f"`name` is the util's name, `args` is a list of strings, `timeout_s` is a number "
            f"of seconds. Do not patch the one field you were told about.")


