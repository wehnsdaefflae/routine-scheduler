"""The action CONTRACT as data — the one flat JSON schema the orchestrator emits against.

Split from the checker that enforces it (`actions.py`, F393): this file is what the contract
IS, that one is what happens when a turn violates it.

The schema is FLAT on purpose and must stay so. Weak models and Ollama grammars handle a flat
object far better than `oneOf`, so every kind shares one property bag and the checker sorts out
which fields that kind actually needs. Adding a variant-shaped schema here would buy tidiness
and cost the small-model support the whole design is built around.
"""

from __future__ import annotations

READ_PATHS_MAX = 8


KINDS = ("util", "write_util", "remove_util", "read_file", "view_image", "write_file",
         "edit_file",
         "memory_read", "memory_write", "read_rule", "write_rule",
         "script", "shell",
         "llm", "spawn", "subtask", "detach",
         "schedule_run", "create_routine", "manage_group",
         "list_models", "subruns", "kill", "wait", "ask_user", "report", "finish")

ACTION_SCHEMA: dict = {
    "type": "object",
    "additionalProperties": False,
    "required": ["say", "kind"],
    "properties": {
        # HOW MUCH to say is the routine's deliberation level, worded ONCE in the harness
        # contract (engine/deliberation.py, the user's knob). Restating a length here shipped
        # a contradiction at every stop but `standard` — the model read "ONE terse clause"
        # and "2-3 sentences" in one prompt. This description owns only the field's mechanics.
        "say": {
            "type": "string",
            "description": "Your narration for this action, at the length the say contract "
                           "above sets. Simple Markdown (bold, `code`, links) renders in the UI.",
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
        # The consequence-reminder side fields (rsched/reminders.py) — like `note`, they
        # ride ANY kind at no turn cost, and like `note` they exist because the moment of
        # realisation and the moment of recording have to be the same turn. Projected out of
        # the schema entirely when the reminders capability is off (kindsurface).
        "remind": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "op": {"type": "string", "enum": ["add", "revise", "delete"],
                       "description": "add a new reminder, revise one, or delete it"},
                "id": {"type": "string",
                       "description": "revise/delete: the reminder's id (rem-…)"},
                "regex": {"type": "string",
                          "description": "the pattern, matched against the CANONICAL "
                                         "one-line rendering of an action: "
                                         "'util:<name> <args…>', 'shell: <command>', "
                                         "'write_file path=<path>', 'read_file "
                                         "paths=<a,b>', '<kind> <field>=<value>'. Anchor it "
                                         "to the class of calls that can cause the "
                                         'consequence, e.g. "^util:fs-ops mv "'},
                "description": {"type": "string",
                                "description": "the caution shown when it fires — what the "
                                               "CONSEQUENCE is and what to check, not that "
                                               "care is needed"},
                "scope": {"type": "string", "enum": ["local", "global"],
                          "description": "local (default) = yours alone; global = the shared "
                                         "library store, for a consequence that would follow "
                                         "for ANY routine making that call"},
            },
            "description": "OPTIONAL, on any action: leave yourself a CONSEQUENCE REMINDER "
                           "the same turn you notice an action had an unintended effect. "
                           "From then on, an action matching `regex` is HELD before it runs "
                           "and you are shown `description` to decide again. Costs no turn "
                           "to write. Also revises or deletes one (op + id) as your own "
                           "tally teaches you which patterns earn their interruptions.",
        },
        "remind_feedback": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "id": {"type": "string", "description": "the reminder that fired"},
                "label": {"type": "string",
                          "enum": ["could_not", "would_have", "did", "didnt"],
                          "description": "could_not = the consequence was impossible for "
                                         "that action (the pattern is too broad) · "
                                         "would_have = it was on track and you avoided it · "
                                         "did = you went ahead and it happened · didnt = you "
                                         "went ahead and nothing bad happened"},
            },
            "description": "OPTIONAL, on any action: label how a reminder's HOLD turned out. "
                           "Costs no turn and is the only evidence that tunes the pattern — "
                           "carry it as soon as you know the outcome.",
        },
        "kind": {"type": "string", "enum": list(KINDS)},
        # util / write_util (how a run executes code)
        "name": {
            "type": "string",
            "description": "util/write_util/remove_util: the global util's name (kebab-case) · "
                           "script: this routine's own scripts/<name>.py helper · "
                           "memory_read/memory_write: the note's topic (kebab-case) · "
                           "read_rule/write_rule: a general rule in the shared library "
                           '(read_rule "list" = the catalog) · '
                           "create_routine: the NEW routine's human display name",
        },
        "args": {
            "type": "array", "items": {"type": "string"},
            "description": "util/script: command-line arguments passed to the script "
                           "(append '--json' for structured output)",
        },
        "timeout_s": {
            "type": "integer", "minimum": 1, "maximum": 1800,
            "description": "util/script: seconds before the script is killed "
                           "(default 300; raise it — up to 1800 — for a genuinely long job "
                           "the 300s default would kill, e.g. running a full test suite) · "
                           "shell: seconds before the command is killed (default 120) · "
                           "wait: max seconds to block (default 600)",
        },
        "command": {
            "type": "string",
            "description": "shell: the ONE command line to run, as a single string — it is "
                           "handed to `bash -c`, so pipes, redirection and `&&` work. It runs "
                           "non-interactively inside your sandbox: nothing that waits for a "
                           "keystroke will ever return",
        },
        # read_file / view_image / write_file / edit_file
        "path": {
            "type": "string",
            "description": "read_file/view_image/write_file/edit_file: path relative to the "
                           "routine dir (or an allowed root) · write_util: install the util "
                           "script from this file's EXACT bytes (byte-faithful; instead of "
                           "inline content) · shell: OPTIONAL working directory for the "
                           "command (default: your working directory)",
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
                           "all: true) — copy it verbatim, whitespace included · "
                           "write_util edit mode: exact text to find in the util's current "
                           "source (read it with util show <name> --full) · "
                           "write_rule edit mode: exact text to find in the rule's current "
                           "prose (read it with read_rule first)",
        },
        "replacement": {
            "type": "string",
            "description": 'edit_file/write_util/write_rule edit mode: the text that replaces '
                           'the anchor (omit or "" to delete it) — edit in place instead of '
                           "re-emitting whole files/scripts/rules",
        },
        "content": {"type": ["string", "object", "array"],
                    "description": "write_file: the full new content — a string, or a JSON "
                                   "object/array (written pretty-printed; no escaping needed) · "
                                   "write_util: the complete PEP 723 script as a string "
                                   "(or omit content and pass anchor/replacement to patch "
                                   "the existing script in place) · "
                                   "write_rule: the complete rule markdown as a string — "
                                   "frontmatter tags + a '# rule: <name> — <summary>' heading "
                                   "+ the principle (or omit content and pass "
                                   "anchor/replacement to revise the existing rule in place) · "
                                   "memory_write: the note's full markdown (one string, "
                                   "≤100 lines)"},
        # schedule_run — arm/cancel a one-shot time trigger on a routine (gated: scheduling)
        "target": {"type": "string",
                   "description": "schedule_run: the routine slug to arm/cancel a one-shot on "
                                  "(self-target always allowed) · "
                                  "create_routine: the NEW routine's kebab-case slug · "
                                  "manage_group: the group id (grp-XXXX) to update/delete/run · "
                                  "report: OPTIONAL — the slug of the routine that OWNS this "
                                  "problem. With it, the report is delivered to that routine "
                                  "and read on its next scheduled run; without it, the report "
                                  "goes to triage. Omit it rather than guess"},
        "answers": {"type": "string",
                    "description": "report: OPTIONAL — the id (R<n>) of a report you RECEIVED "
                                   "that this one answers: what you did about it, or why you "
                                   "will not. That is how a report gets closed"},
        "closes": {"type": "boolean",
                   "description": "report: with `answers` — this reply COMPLETES the exchange: "
                                  "it settles its target AND is itself born settled, asking "
                                  "nothing back. Set it whenever your answer needs no reply; a "
                                  "closure is reopened only by a NEW report that names it"},
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
        # manage_group — CRUD/fire routine groups from a conversation (D61); root-conversation only
        "verb": {"type": "string",
                 "enum": ["list", "create", "update", "delete", "set-default", "run"],
                 "description": "manage_group: the operation — list (the whole store) · create "
                                "(needs name) · update (needs target) · delete (needs target) · "
                                "set-default (needs on_failure) · run (needs target; arms a "
                                "sequential fire of the group)"},
        "members": {"type": "array", "items": {"type": "string"},
                    "description": "manage_group create/update: the ORDERED routine slugs in the "
                                   "group (deduped; each must name a real routine) — the fire "
                                   "order a group run uses"},
        "paused": {"type": "boolean",
                   "description": "manage_group update: true pauses the GROUP's cron (nothing "
                                  "in the group auto-fires; an explicit run still works, and "
                                  "members stay group-managed), false resumes it"},
        "on_failure": {"type": "string", "enum": ["stop", "continue"],
                       "description": "manage_group: mid-chain-failure policy — 'stop' aborts the "
                                      "rest of the chain, 'continue' fires the remaining members. "
                                      "Required for set-default; optional on create/update (omit "
                                      "to inherit the instance default)"},
        "cron": {"type": "string",
                 "description": "manage_group create/update: the GROUP's cron schedule (server "
                                "tz), e.g. '0 10 * * *' — member 0 fires on it, the rest chain "
                                "on completion, and every member's own cron is suppressed while "
                                "it is set. Empty string clears it (members fire on their own "
                                "crons again); omit to leave unchanged"},
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
                                  "the file falls back to the vision util) · create_routine: the "
                                  "clarified task instruction, decomposed into the new routine's "
                                  "stages (say WHAT it should do, not when it runs)"},
        "system": {"type": "string", "description": "llm: optional system prompt"},
        "response_schema": {"type": "object",
                            "description": "llm: optional JSON schema constraining the reply"},
        "model": {"type": "string",
                  "description": "llm/spawn/subtask: OPTIONAL model override — a ROLE (main, "
                                 "tool_call; uncensored targets the routine's uncensored model "
                                 "for a step the default refuses, rejected if unconfigured) OR "
                                 "a catalog model NAME from `list_models`. Defaults: children "
                                 "(spawn/subtask) run the routine's MAIN model, llm runs "
                                 "tool_call"},
        "workflow": {"type": "string",
                     "description": "spawn/subtask/detach: library workflow slug for the child "
                                    "(default general-task) — pick the pattern matching its "
                                    "purpose · create_routine: the library workflow pattern the "
                                    "new routine is materialized from (default general-task)"},
        "stopping": {"type": "array", "items": {"type": "string"}, "maxItems": 6,
                     "description": "create_routine: what DONE looks like for ONE run of the "
                                    "new routine, in the USER's own words — one condition per "
                                    'entry ("the digest is published and the link works"). '
                                    "These become its RUN-scoped stopping conditions: every "
                                    "run must account for each one in its finish summary. "
                                    "Carry the user's answer here verbatim; omit it rather "
                                    "than inventing conditions they did not state"},
        "goal": {"type": "array", "items": {"type": "string"}, "maxItems": 4,
                 "description": "create_routine: the state after which this ROUTINE is "
                                "FINISHED and should stop running altogether, in the USER's "
                                'own words ("the application is submitted", "the folder '
                                'reorganisation is live"). A different question from '
                                "`stopping`, and it has teeth: when every goal condition is "
                                "met the scheduler stops firing the routine and asks the user "
                                "to confirm its retirement. Name a literal DATE where the task "
                                "has one. A routine that genuinely never ends — a monitor, a "
                                "digest — takes NO goal, and that is the common case: omit it "
                                "rather than inventing an ending, because a wrong goal "
                                "switches a working routine off"},
        "label": {"type": "string",
                  "description": "spawn/subtask/detach: short name shown in the run tree"},
        "turns": {"type": "integer", "minimum": 1,
                  "description": "subtask: turn budget for this sequential child (default: half "
                                 "your remaining turns)"},
        # subruns / kill / wait
        "n": {"type": "integer", "minimum": 1, "description": "kill/wait: the sub-workflow number"},
        "all": {"type": "boolean",
                "description": "wait: wait for ALL running sub-workflows (default: any next) · "
                               "edit_file/write_util edit mode: replace EVERY occurrence of "
                               "the anchor (default: the anchor must be unique)"},
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
        "config_patch": {
            "type": "object",
            "description": "ask_user: OPTIONAL — a proposed routine.yaml CONFIG change the user "
                           "can one-click apply from the Decisions page (a run can never edit its "
                           "own config). Shape = the PATCH /routines body, e.g. "
                           '{"budgets": {"max_turns": 100}} or {"schedule": {"friendly": '
                           '{"frequency": "hourly", "minute": 0}}}. Use it when a revise-recipe '
                           "run is asked for a schedule / budget / model / permission / fs-roots "
                           "change it cannot make itself.",
        },
        "request": {
            "type": "string",
            "description": "ask_user: OPTIONAL — a typed ACCESS REQUEST, one grant-entity id "
                           '"<class>:<name>" (e.g. "util:discord", "fs-write:~/project", '
                           '"secret:FOO_KEY", "machine:gpu-box"). The user decides allow/deny, '
                           "once (this run) or "
                           "forever; the engine applies the decision — your question just says "
                           "WHY. Use it when a denial names a requestable entity.",
        },
        # report — the ungated channel every routine holds
        "title": {
            "type": "string",
            "description": "report: a one-line summary of the problem you are raising",
        },
        "detail": {
            "type": "string",
            "description": "report: the full description — the exact file or artefact, what "
                           "is wrong, the evidence (a run id, a path:line, an error), and what "
                           "'done' looks like. Whoever picks this up has none of your context, "
                           "so write it to stand alone",
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
        "reply_to": {
            "type": "string",
            "description": "finish (conversations only): OPTIONAL — an earlier message THIS "
                           "reply addresses. Quote it or name it (e.g. the message's opening "
                           "words); it renders as a '↩ …' reference chip above your reply in "
                           "the chat, the way the user replying to a message does. Ignored "
                           "outside a conversation.",
        },
    },
}

# The one field that best identifies a turn of each kind — the one-line "briefs" used by
# turn records, compaction digests, and transcript replay.
BRIEF_FIELD = {"util": "name", "write_util": "name", "remove_util": "name", "read_file": "path",
               "view_image": "path", "script": "name", "shell": "command",
               "write_file": "path", "edit_file": "path", "memory_read": "name",
               "memory_write": "name", "read_rule": "name", "write_rule": "name",
               "llm": "prompt", "spawn": "label", "subtask": "label",
               "detach": "label", "schedule_run": "target", "create_routine": "target",
               "manage_group": "verb",
               "kill": "n", "wait": "n",
               "ask_user": "question", "report": "title", "finish": "status"}


def brief_value(action: dict) -> str:
    """The VALUE of the action's most identifying field — no kind, no truncation.

    The three sites that record a turn (`loop._record_turn`, the admin audit line,
    `history.read_transcript`) store the kind SEPARATELY and want only this value, and each
    carried its own copy of the same lookup with a different truncation. The widths stay theirs;
    the derivation is now one function.
    """
    kind = str(action.get("kind") or "")
    return str(action.get(BRIEF_FIELD.get(kind, ""), "") or "")


def canon(action: dict) -> str:
    """THE canonical one-line rendering of an action — the stable, legible string identifying what
    a turn actually did.

    This is the documented MATCH TARGET. Anything deciding "is this the action I meant?" — a
    reminder regex, a rule's relevance trigger, a history-recall keyword overlap — matches against
    this string, and anything showing a person or a model WHAT matched renders the same string.
    That is the entire reason it lives in one place: precision and recall are only tunable if the
    thing being matched is stable and legible, and before this the same line was derived six
    different ways (three truncations of the field value, a separate name/path/paths rule in
    `notes.py`, and a richer JS version in the transcript component that had already drifted ten
    kinds behind once).

    The forms, and why they differ:

        util:codemap --json           a util call is identified by its name AND its arguments —
                                      `util:fs-ops` alone cannot tell `mv` from `rm`
        shell: rm -rf build/          the command IS the action; a `command=` label adds nothing
        read_file paths=a.md,b.md     `read_file` carries a LIST (`paths`), not the singular field
        write_file path=state/x.json  every other kind names its field, so the string says what
        finish                        it is; a kind with no identifying field is just itself

    Untruncated on purpose: a caller needing a width applies its own. Matching a pre-truncated
    string would silently change what a regex can see as an action's arguments grow.
    """
    kind = str(action.get("kind") or "?")
    if kind == "util":
        args = action.get("args")
        tail = " ".join(str(a) for a in args) if isinstance(args, list) else ""
        return f"util:{action.get('name') or '?'}{f' {tail}' if tail else ''}"
    if kind == "shell":
        return f"shell: {action.get('command') or ''}".rstrip()
    if kind == "read_file" and isinstance(action.get("paths"), list) and action["paths"]:
        return f"read_file paths={','.join(str(x) for x in action['paths'])}"
    field = BRIEF_FIELD.get(kind, "")
    value = str(action.get(field, "") or "") if field else ""
    return f"{kind} {field}={value}" if value else kind


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
