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

#: How many report ids one report may take over at once. Generous — a triage pass folding a
#: whole queue into one hand-off is exactly the intended use, and the alternative is the batch
#: of separate reports this field exists to prevent.
SUPERSEDES_MAX = 20

#: How many rows one reply may terminally SETTLE (D134). Same generous cap as `supersedes` and
#: for the same reason: a reply that disposes of a whole drained queue is the intended use, and
#: the alternative — one reply per row — is the shape that left rows settled-in-prose and
#: open-in-the-ledger (F497). `settles` differs from `supersedes` in WHO ends up holding the
#: work: folding moves rows into this thread, settling declares them finished.
SETTLES_MAX = 20


#: The catalog's own verbs, answered by the `util` action itself (executor.do_util: the live
#: catalog, one util's source, a keyword search) before the library is consulted. So they are
#: discovery, never an execution to count or gate — and no library util may take one of
#: these names, because the action would never reach it.
PSEUDO_UTILS = ("list", "show", "search")

KINDS = ("util", "write_util", "remove_util", "read_file", "view_image", "write_file",
         "delete", "move", "mkdir", "edit_file",
         "memory_read", "memory_write", "read_rule", "write_rule",
         "script", "shell",
         "llm", "decide", "spawn", "subtask", "detach",
         "schedule_run", "create_routine", "manage_lane",
         "task", "goal",
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
                          "description": "the pattern, matched against what `kind` names. For "
                                         "the default kind (action) that is the CANONICAL "
                                         "one-line rendering of an action: "
                                         "'util:<name> <args…>', 'script:<name> <args…>', "
                                         "'shell: <command>', 'write_file path=<path>', "
                                         "'read_file paths=<a,b>', '<kind> <field>=<value>' — "
                                         "and nothing else: a file's content or an edit's "
                                         "anchor is never part of it. Anchor it to the class "
                                         "of calls that can cause the consequence, e.g. "
                                         '"^util:fs-ops mv "'},
                "kind": {"type": "string",
                         "enum": ["action", "result", "prose", "turn:first", "turn:finish",
                                  "turn:question", "turn:answer", "turn:error"],
                         "description": "what the pattern WATCHES (default action). action = "
                                        "the canonical action string, tested BEFORE the "
                                        "action runs — the only kind that can HOLD it. The "
                                        "others watch what has already happened, so they "
                                        "cost no turn and ride the observation instead: "
                                        "result = the observation that came back (an exit "
                                        "code, an error); prose = this turn's own `say`; "
                                        "turn:<moment> = that special turn (first, finish, "
                                        "question, answer, error)"},
                "description": {"type": "string",
                                "description": "the caution shown when it fires — what the "
                                               "CONSEQUENCE is and what to check, not that "
                                               "care is needed"},
                "scope": {"type": "string", "enum": ["local", "global"],
                          "description": "local (default) = yours alone; global = the shared "
                                         "library store, for a consequence that would follow "
                                         "for ANY routine making that call"},
                "reach": {"type": "string", "enum": ["universal", "listed"],
                          "description": "global only: universal = held for every routine "
                                         "whose action matches (a consequence any caller "
                                         "meets); listed = held only for routines whose "
                                         "settings list it (a caution for one kind of work)"},
                "target": {"type": "string",
                           "description": "op=add only: ANOTHER routine's slug — set this hook "
                                          "on that routine instead of yourself. It fires in ITS "
                                          "runs from its next one on, and that routine decides "
                                          "on each fire whether to keep, mute or remove it; you "
                                          "cannot revise or delete it, and a pattern it removed "
                                          "you cannot set again. Use it for a consequence YOU "
                                          "observed that will land on THAT routine's work"},
            },
            "description": "OPTIONAL, on any action: leave yourself a CONSEQUENCE REMINDER "
                           "the same turn you notice an action had an unintended effect. "
                           "From then on, an action matching `regex` is HELD before it runs "
                           "and you are shown `description` to decide again. `kind` chooses "
                           "what the pattern watches instead: a result, this turn's prose, or "
                           "a special turn — those fire on the observation and never hold. "
                           "Costs no turn "
                           "to write. Also revises or deletes one (op + id) as your own "
                           "tally teaches you which patterns earn their interruptions. With "
                           "`target` it sets the hook on ANOTHER routine, whose runs then meet "
                           "it and whose disposition over it outranks yours.",
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
                "disposition": {"type": "string", "enum": ["keep", "mute", "remove"],
                                "description": "only for a hook ANOTHER routine set on you "
                                               "(the fire says who): your say over its fate — "
                                               "keep (leave it live), mute (silence it for the "
                                               "rest of THIS run; its owner still sees it), "
                                               "remove (delete it, and its owner cannot set "
                                               "that pattern on you again). Your decision "
                                               "outranks the routine that set it"},
            },
            "description": "OPTIONAL, on any action: label how a reminder's HOLD turned out. "
                           "Costs no turn and is the only evidence that tunes the pattern — "
                           "carry it as soon as you know the outcome. For a hook another "
                           "routine set on you, `disposition` is where you keep, mute or "
                           "remove it.",
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
                           "create_routine: the NEW routine's human display name · "
                           "manage_lane create/update: the lane's display name",
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
        "background": {
            "type": "boolean",
            "description": "util/script/shell/llm/decide/read_file/view_image/memory_read/"
                           "read_rule: "
                           "this call is a READ or a FETCH, so run it in the "
                           "BACKGROUND and get the turn back at once. The observation you "
                           "receive names a handle and says the call is running; the REAL "
                           "observation is appended at a later turn boundary, tagged with "
                           "that handle. Use it for a call that would make a waiting human "
                           "sit — a heavy fetch, a long `llm`, a slow test run — and keep "
                           "working or keep talking meanwhile. NOT available on anything that "
                           "writes (write_file, edit_file, write_util, memory_write) or that "
                           "steers the run (finish, ask_user, report, spawn, subtask, wait, "
                           "kill): a later action would read state the background call has "
                           "not written yet. Do not background a call whose result your VERY "
                           "NEXT action needs — you would only have to wait for it anyway. At "
                           "most THREE may be in flight at once: a fourth is refused naming the "
                           "live handles, so you choose what to drop or take in the foreground",
        },
        "command": {
            "type": "string",
            "description": "shell: the ONE command line to run, as a single string — it is "
                           "handed to `bash -c`, so pipes, redirection and `&&` work. It runs "
                           "non-interactively inside your sandbox: nothing that waits for a "
                           "keystroke will ever return",
        },
        # read_file / view_image / write_file / delete / move / mkdir / edit_file
        "path": {
            "type": "string",
            "description": "read_file/view_image/write_file/delete/mkdir/edit_file: path "
                           "relative to your working directory (or an allowed root) · task "
                           "create/update: the task's workspace, relative to the routine (default "
                           "tasks/<id>) · write_util: "
                           "install the util script from this file's EXACT bytes "
                           "(byte-faithful; instead of inline content) · shell: OPTIONAL "
                           "working directory for the command (default: your working "
                           "directory)",
        },
        "src": {
            "type": "string",
            "description": "move: the path being moved/renamed (relative to the routine dir "
                           "or an allowed root) — the same write-root jail as write_file",
        },
        "dst": {
            "type": "string",
            "description": "move: the destination path (same jail as src) — refuses to "
                           "overwrite an existing destination; creates missing parent "
                           "directories",
        },
        "recursive": {
            "type": "boolean",
            "description": "delete: true removes a whole directory tree — a directory "
                           "without it is refused (a stray call cannot wipe a tree it did "
                           "not mean)",
        },
        "parents": {
            "type": "boolean",
            "description": "mkdir: true makes intermediate directories too (like mkdir -p) "
                           "and treats an existing path as success",
        },
        "paths": {
            "type": "array", "items": {"type": "string"}, "maxItems": READ_PATHS_MAX,
            "description": "read_file/view_image: act on SEVERAL files in one action (instead "
                           "of `path`) — batch related reads/images",
        },
        "then_script": {
            "type": "array", "items": {"type": "string"}, "minItems": 1,
            "description": "write_file/edit_file: OPTIONAL — run one of your scripts/ the moment "
                           'the change lands: ["<script-name>", ...args]. Both results come '
                           "back in ONE observation, saving the turn you would spend calling "
                           "it; skipped if the change fails. Use it whenever your next action "
                           "would be running that script (to test, render or check the file)",
        },
        "start_line": {"type": "integer", "minimum": 1,
                       "description": "read_file: first line (default 1)"},
        "start_char": {"type": "integer", "minimum": 0,
                       "description": "read_file: begin this many characters into start_line "
                                      "(default 0) — continues a line longer than one "
                                      "observation, exactly where a truncation marker says"},
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
                                  "(this routine or another) · "
                                  "create_routine: the NEW routine's kebab-case slug · "
                                  "manage_lane: the lane id to update/delete/run — take it "
                                  "from a `list`; ids are opaque handles · "
                                  "report: OPTIONAL — the slug of the routine that OWNS this "
                                  "problem. With it, the report is delivered to that routine "
                                  "and read on its next scheduled run; without it, the report "
                                  "goes to triage. Omit it rather than guess"},
        "answers": {"type": "string",
                    "description": "report: OPTIONAL — the id (R<n>) of a report you RECEIVED "
                                   "that this one answers: what you did about it, or why you "
                                   "will not. That is how a report gets closed"},
        "settles": {
            "type": "array", "items": {"type": "string"}, "maxItems": SETTLES_MAX,
            "description": "report: OPTIONAL — every report id (R<n>) this one TERMINALLY "
                           "SETTLES. `answers` records the ONE exchange this reply belongs to; "
                           "`settles` is the many-rows claim: list every OTHER row this reply "
                           "disposes of and each becomes settled, naming this report as what "
                           "settled it. Use it when one reply genuinely answers several rows, "
                           "and on a report that asks nothing back — with `settles` a reply "
                           "needs no `answers` to carry `closes`"},
        "closes": {"type": "boolean",
                   "description": "report: with `answers` or `settles` — this reply COMPLETES "
                                  "the exchange: it settles its target(s) AND is itself born "
                                  "settled, asking nothing back. Set it whenever your answer "
                                  "needs no reply; a closure is reopened only by a NEW report "
                                  "that names it"},
        "supersedes": {
            "type": "array", "items": {"type": "string"}, "maxItems": SUPERSEDES_MAX,
            "description": "report: OPTIONAL — existing report ids (R<n>) this one TAKES OVER. "
                           "Needs `target`. Use it to hand rows to their owner in one report, "
                           "and to add to a thread you already have open instead of opening "
                           "another. Each id folds into this report: it leaves triage now and "
                           "settles when this one settles"},
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
               "description": "schedule_run: the one-shot id (so-XXXX) to cancel · task: the "
                              "task's kebab-case id (create: the new one's; open without it "
                              "opens the next due task) · goal change/check/drop: the goal's "
                              "id (b1, b2, …)"},
        # manage_lane — CRUD/fire routine LANES from a conversation (D61). Offered to every
        # depth-0 run; a root conversation applies a verb, any other run queues a proposal (F328)
        "verb": {"type": "string",
                 "enum": ["list", "create", "update", "delete", "set-default", "run", "open",
                          "checkpoint", "set", "add", "change", "check", "drop"],
                 # EVERY clause carries its kind's lead. `kindsurface._project_description`
                 # splits on " · " and KEEPS a clause that names no kind, so an un-led
                 # continuation of task's list survives into a run that holds manage_lane and
                 # not task — and reads there as a manage_lane verb. Measured in a live
                 # self-audit prompt, which rendered task's create/update/open/checkpoint/delete
                 # as manage_lane's own (the lead clause was dropped correctly; its orphans
                 # were not). The shared `enum` above is deliberate (actions.py validates each
                 # kind's verbs separately); only the prose must be per-kind.
                 "description": "manage_lane: the operation — list (the whole store) · "
                                "manage_lane: create (needs name) · manage_lane: update (needs "
                                "target) · manage_lane: delete (needs target) · manage_lane: "
                                "set-default (needs on_failure) · manage_lane: run (needs "
                                "target; arms a sequential fire of the lane) · task: the "
                                "operation — list · task: create (id + title + brief) · task: "
                                "update (id + what changes) · task: open (id, or none for the "
                                "next due task) · task: checkpoint (id + outcome + summary: "
                                "closes the open task and opens the next due one) · task: "
                                "delete (id) · goal: the operation — set (goals: the opening "
                                "list, while none is open) · goal: add (goals: one or more "
                                "new ones) · goal: change (id + goals: [one, reworded]) · "
                                "goal: drop (id + quote) · goal: check (id + evidence: met) · "
                                "goal: list"},
        # goal — the run's GOALS, transcribed from what a person asked (engine/goals.py)
        "goals": {
            "type": "array", "maxItems": 12,
            "items": {"type": "object", "additionalProperties": False, "properties": {
                "text": {"type": "string",
                         "description": "the goal as ONE checkable end state — what is true "
                                        "when it is met, not a step toward it"},
                "quote": {"type": "string",
                          "description": "the person's OWN words it came from, verbatim (an "
                                         "excerpt; join two excerpts with …)"}}},
            "description": "goal set/add: the goals, one per entry · goal change: ONE entry — "
                           "the new wording, quoting the words they wrote AFTER the goal was "
                           "set that changed it",
        },
        "quote": {"type": "string",
                  "description": "goal drop: the person's own words, written AFTER the goal "
                                 "was set, that withdraw it — verbatim"},
        "members": {"type": "array", "items": {"type": "string"},
                    "description": "manage_lane create/update: the ORDERED routine slugs in the "
                                   "lane (deduped; each must name a real routine) — the fire "
                                   "order a lane run uses"},
        "paused": {"type": "boolean",
                   "description": "manage_lane update: true pauses the LANE's cron (nothing "
                                  "in the lane auto-fires; an explicit run still works and "
                                  "members stay lane-managed), false resumes it"},
        "on_failure": {"type": "string", "enum": ["stop", "continue"],
                       "description": "manage_lane: mid-chain-failure policy — 'stop' aborts the "
                                      "rest of the chain, 'continue' fires the remaining members. "
                                      "Required for set-default; optional on create/update (omit "
                                      "to inherit the instance default)"},
        "cron": {"type": "string",
                 "description": "manage_lane create/update: the LANE's cron schedule (server "
                                "tz), e.g. '0 10 * * *' — member 0 fires on it, the rest chain "
                                "on completion, and every member's own cron is suppressed while "
                                "it is set. Empty string clears it (members fire on their own "
                                "crons again); omit to leave unchanged"},
        "append": {"type": "boolean",
                   "description": "write_file: append instead of overwrite (default false); a "
                                  "structured `content` appends as ONE compact JSON line "
                                  "(a JSONL record), never pretty-printed"},
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
        # decide — a typed question to a DECISION model (engine/decideaction.py). `question`,
        # `options` and `model` are shared with ask_user / llm and described there too.
        "answer_type": {"type": "string", "enum": ["yes_no", "choice", "score"],
                        "description": "decide: yes_no (the answer is P(yes); no options) · "
                                       "choice (one of `options`) · score (a position on "
                                       "`options` read as ordered levels, LOWEST first). "
                                       "Default: choice when options are given, else yes_no"},
        "questions": {
            "type": "array", "maxItems": 16,
            "items": {"type": "object", "additionalProperties": False, "properties": {
                "name": {"type": "string",
                         "description": "snake_case id the answer is filed under"},
                "question": {"type": "string", "description": "what to decide"},
                "answer_type": {"type": "string", "enum": ["yes_no", "choice", "score"]},
                "options": {"type": "array", "items": {"type": "string"}, "maxItems": 26}}},
            "description": "decide: SEVERAL independent questions over the same evidence, in "
                           "one call (instead of question/answer_type/options) — each with "
                           "its own name, question, answer_type and options. One evidence, "
                           "many questions is what these models are fastest and cheapest at",
        },
        "evidence": {"type": ["string", "object", "array"],
                     "description": "decide: what the answer depends on — text, or a JSON "
                                    "object/array of the facts. Self-contained: the model "
                                    "sees nothing else of your context · goal check: what "
                                    "shows the goal is met — the observation or artefact, as "
                                    "text"},
        "files": {"type": "array", "items": {"type": "string"}, "maxItems": 16,
                  "description": "decide: files whose CONTENT is evidence — images "
                                 "(png/jpeg/webp/gif) are shown to a decision model that takes "
                                 "them (several judged together), text files are read in"},
        "response_schema": {"type": "object",
                            "description": "llm: optional JSON schema constraining the reply"},
        "model": {"type": "string",
                  "description": "llm/spawn/subtask: OPTIONAL model override — a ROLE (main, "
                                 "tool_call; uncensored targets the routine's uncensored model "
                                 "for a step the default refuses, rejected if unconfigured) OR "
                                 "a catalog model NAME from `list_models`. Defaults: children "
                                 "(spawn/subtask) run the routine's MAIN model, llm runs "
                                 "tool_call · decide: OPTIONAL — a DECISION model's name "
                                 "(list_models shows them; default: the instance's decision "
                                 "model, its image default when the call carries images)"},
        "workflow": {"type": "string",
                     "description": "spawn/subtask/detach: library workflow slug for the child "
                                    "(default general-task) — pick the pattern matching its "
                                    "purpose · create_routine: the library workflow pattern the "
                                    "new routine is materialized from (default general-task)"},
        "pattern": {"type": "string",
                    "description": "create_routine: the SETTINGS pattern the new routine "
                                   "follows — one the preview lists for its workflow (default: "
                                   "the one that fits)"},
        "setup": {"type": "array", "items": {"type": "string"}, "maxItems": 12,
                  "description": "create_routine: the user's answers to the settings "
                                 "pattern's questions — which folders, which mailbox and "
                                 "senders, which cadence — one per entry, in their words. They "
                                 "become the new routine's proposed settings, which the user "
                                 "accepts on its page; they never go into the instruction"},
        "done_when": {"type": "array", "items": {"type": "string"}, "maxItems": 8,
                      "description": "create_routine: what ONE finished run of the new routine "
                                     "leaves behind, in the USER's own words — one outcome per "
                                     'entry ("the digest is published and the link works"). '
                                     "They become lines of its recipe's `## Done when`, which "
                                     "every run accounts for at its finish. Carry the user's "
                                     "answer verbatim; omit it rather than inventing outcomes "
                                     "they did not state"},
        "finish_line": {"type": "array", "items": {"type": "string"}, "maxItems": 6,
                        "description": "create_routine: when the ROUTINE is done for good, one "
                                       "outcome per entry, each led by its judge — `run: "
                                       "<an outcome a run can prove>`, `you: <an outcome only "
                                       "the user decides>`, `YYYY-MM-DD: <reached on that "
                                       "date>` — plus `until YYYY-MM-DD` to stop after a date "
                                       "either way. Reaching it stops the routine and asks the "
                                       "user to confirm. A routine that never ends (a monitor, "
                                       "a digest) takes none, which is the common case: omit it "
                                       "rather than inventing an ending"},
        "never": {"type": "array", "items": {"type": "string"}, "maxItems": 8,
                  "description": "create_routine: what a run of the new routine must NEVER "
                                 "do, in the USER's own words — one prohibition per entry "
                                 '("never mail the funder", "never touch the Drafts folder"). '
                                 "Each becomes a line of its recipe's `## Never`; its "
                                 "proposed settings leave off the permission, or add the rule "
                                 "or reminder, that stops the action before it happens. Omit "
                                 "it when they named none"},
        "label": {"type": "string",
                  "description": "spawn/subtask/detach: short name shown in the run tree"},
        "turns": {"type": "integer", "minimum": 1,
                  "description": "subtask: turn budget for this sequential child (default: half "
                                 "your remaining turns)"},
        # subruns / kill / wait
        "n": {"type": "integer", "minimum": 1, "description": "kill/wait: the sub-workflow number"},
        "all": {"type": "boolean",
                "description": "wait: wait for ALL running sub-workflows (default: any next) · "
                               "edit_file/write_util/write_rule edit mode: replace EVERY "
                               "occurrence of the anchor (default: the anchor must be unique)"},
        # ask_user
        "question": {"type": "string",
                     "description": "ask_user: the question, self-contained (simple Markdown "
                                    "renders in the UI) · decide: what to decide, phrased as "
                                    "the question the options answer"},
        "mode": {
            "type": "string", "enum": ["blocking", "deferred"],
            "description": "ask_user: wait for the answer vs file it and continue "
                           "(default deferred)",
        },
        "options": {
            "type": "array", "items": {"type": "string"}, "maxItems": 26,
            "description": "ask_user: optional pick-one choices (at most 5) · decide: the "
                           "answers to choose between, or a score's levels LOWEST first — "
                           "each 'value: what it means' (the value is what the answer names)",
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
                           "change it cannot make itself. To propose the change for ANOTHER "
                           'routine, name it: {"routine": "suedlink-wlf", "budgets": '
                           '{"max_turns": 120}} — the apply then patches that routine, not you. '
                           "A slug naming no installed routine is refused on this turn.",
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
            "description": "report: a one-line summary of the problem you are raising · task "
                           "create/update: the task's one-line name",
        },
        "detail": {
            "type": "string",
            "description": "report: the full description — the exact file or artefact, what "
                           "is wrong, the evidence (a run id, a path:line, an error), and what "
                           "'done' looks like. Whoever picks this up has none of your context, "
                           "so write it to stand alone",
        },
        # task — the routine's standing units of work (only with the task layer switched on)
        "brief": {"type": "string",
                  "description": "task create/update: what the task is, what processing it "
                                 "each run means and what done looks like — self-contained, "
                                 "since a later run reads nothing else about it"},
        "state": {"type": "string", "enum": ["active", "paused", "done"],
                  "description": "task create/update: active (processed when due) · paused "
                                 "(kept, never due) · done (finished for good)"},
        "outcome": {"type": "string", "enum": ["advanced", "no-work", "blocked", "deferred"],
                    "description": "task checkpoint: what became of the task this run — "
                                   "advanced (work landed) · no-work (looked; nothing was due) · "
                                   "blocked (waits on something outside your reach, named in "
                                   "the summary) · deferred (work exists and you set it aside: "
                                   "the next run is owed it)"},
        "wake": {"type": "string",
                 "description": "task create/update/checkpoint: YYYY-MM-DD — let the task rest "
                                "until then unless one of its gate checks finds work; an "
                                "empty string clears it"},
        "quiet_days": {"type": "integer", "minimum": 1, "maximum": 365,
                       "description": "task create/update: the task is due whenever it has "
                                      "not been processed for this many days"},
        # finish
        "status": {"type": "string", "enum": ["ok", "partial", "failed"],
                   "description": "finish: ok = everything this run could do is done (a "
                                  "decision now waiting on the user does not make it partial); "
                                  "partial = something outside your reach stopped feasible "
                                  "work, named in the summary; failed = the job could not be "
                                  "done"},
        "summary": {
            "type": "string",
            "description": "task checkpoint: what this run did for the task and what the next "
                           "run should pick up — the task's whole memory of this run · "
                           "finish: a DETAILED 8-20 line result summary — concrete outcomes "
                           "(numbers, names, links), decisions taken + why, files changed, "
                           "open ends and what the next run should pick up (becomes result.md, "
                           "the dashboard's last-outcome, and the next run's context; Markdown "
                           "— bold, lists, `code`, links, pipe tables, > quotes — renders in "
                           "the UI)",
        },
        "accounting": {
            "type": "array", "items": {"type": "string"},
            "description": "task checkpoint: one entry per line of the open task's own "
                           "recipe `## Done when` (`d1 met: <evidence>`, `d2 not due: <how "
                           "established>` …) · finish: your verdict on each thing this run "
                           "answers for — one "
                           "entry per line of your recipe's `## Done when` (`d1 met: "
                           "<evidence>`, `d2 unmet: <what remains>`, `d3 not due: <how that "
                           "was established>`), per open outcome of the routine's finish "
                           "line (`g1 distance: <what remains>`, or `g1 met: <evidence>` for "
                           "one the run proves) and per open GOAL (`b1 met: <evidence>`, `b2 "
                           "unmet: <what remains>`). Omit when your digest names none of them",
        },
        "reply_to": {
            "type": "string",
            "description": "finish (conversations only): OPTIONAL — an earlier message THIS "
                           "reply addresses. Quote it or name it (e.g. the message's opening "
                           "words); it renders as a '↩ …' reference chip above your reply in "
                           "the chat, the way the user replying to a message does. Ignored "
                           "outside a conversation.",
        },
        "final": {
            "type": "boolean",
            "description": "finish (conversations only): is this reply FINAL? true = it "
                           "delivers what the person asked: every open goal is accounted met "
                           "in `accounting` (or was checked off) · false = you are handing back "
                           "BEFORE that — progress, a question only they can answer, a blocker "
                           "— and the goals stay open for the next reply. Declare it on every "
                           "reply; it is required while a goal is open. Ignored outside a "
                           "conversation.",
        },
    },
}

# The one field that best identifies a turn of each kind — the one-line "briefs" used by
# turn records, compaction digests, and transcript replay.
BRIEF_FIELD = {"util": "name", "write_util": "name", "remove_util": "name", "read_file": "path",
               "view_image": "path", "script": "name", "shell": "command",
               "write_file": "path", "delete": "path", "move": "src",
               "mkdir": "path", "edit_file": "path", "memory_read": "name",
               "memory_write": "name", "read_rule": "name", "write_rule": "name",
               "llm": "prompt", "decide": "question", "spawn": "label", "subtask": "label",
               "detach": "label", "schedule_run": "target", "create_routine": "target",
               "manage_lane": "verb", "task": "id", "goal": "id",
               "kill": "n", "wait": "n",
               "ask_user": "question", "report": "title", "finish": "status"}
#: The kinds that may name several files at once (`paths`) where BRIEF_FIELD names one — what
#: `canon` renders, and the reminder gate accepts, as `<kind> paths=<a,b>`.
LIST_KINDS = ("read_file", "view_image")


def brief_value(action: dict) -> str:
    """The VALUE of the action's most identifying field — no kind, no truncation.

    The sites that record a turn (`compaction.turn_record` — shared by the live loop and the
    resume replay — and the admin audit line in `loop`) store the kind SEPARATELY and want only
    this value, and each carried its own copy of the same lookup with a different truncation.
    The widths stay theirs; the derivation is now one function.
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
        script:store stage --note x   the routine's own script, the same way and for the same
                                      reason: `script name=store` cannot tell `stage` from `drop`
        task:checkpoint nanogeofeld   a task turn by its verb and the task it acts on
        shell: rm -rf build/          the command IS the action; a `command=` label adds nothing
        read_file paths=a.md,b.md     `read_file` and `view_image` carry a LIST (`paths`), not
                                      the singular field
        write_file path=state/x.json  every other kind names its field, so the string says what
                                      it is (`finish status=ok` too)
        subruns                       a kind with no identifying field is just itself

    Untruncated on purpose: a caller needing a width applies its own. Matching a pre-truncated
    string would silently change what a regex can see as an action's arguments grow.
    """
    kind = str(action.get("kind") or "?")
    if kind in ("task", "goal"):
        # the verb is what the turn DID to the task — `task id=x` cannot tell an open from a
        # checkpoint, the one distinction a reader of the turn needs (a goal's add from its drop)
        tid = str(action.get("id") or "")
        return f"{kind}:{action.get('verb') or '?'}{f' {tid}' if tid else ''}"
    if kind in ("util", "script"):
        args = action.get("args")
        tail = " ".join(str(a) for a in args) if isinstance(args, list) else ""
        return f"{kind}:{action.get('name') or '?'}{f' {tail}' if tail else ''}"
    if kind == "shell":
        return f"shell: {action.get('command') or ''}".rstrip()
    if kind in LIST_KINDS and isinstance(action.get("paths"), list) and action["paths"]:
        return f"{kind} paths={','.join(str(x) for x in action['paths'])}"
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
