"""The two reminder SIDE FIELDS as schema — `remind` and `remind_feedback`.

Its own module for two reasons. `engine/actionschema.py` is over the repo's size bar and may
only shrink, exactly as `engine/remind.py` was when `engine/remindfields.py` split off its pure
checks — so the field pair follows its validator out.

And F680: **every cap these fields enforce is stated here, interpolated from the constants that
enforce it.** `reminder_checks.MAX_REGEX_CHARS` (200) and `MAX_DESCRIPTION_CHARS` (400) were
enforced on the write path and named in no field description, so the only way to learn a limit
was to be refused by it. Three routines hit that in one window, each losing a turn while
curating correctly; a fourth was the run that shipped this. The numbers are imported, never
retyped: a cap that drifts from its documentation is the same defect with a different number.
"""

from __future__ import annotations

from ..reminder_checks import MAX_DESCRIPTION_CHARS, MAX_REGEX_CHARS

#: The `remind` object — leave yourself (or another routine) a consequence reminder.
REMIND_FIELD = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "op": {"type": "string", "enum": ["add", "revise", "delete"],
               "description": "add a new reminder, revise one, or delete it"},
        "id": {"type": "string",
               "description": "revise/delete: the reminder's id (rem-…) — exactly as a fire "
                              "names it; anything else is refused"},
        "regex": {"type": "string",
                  "description": "the pattern, matched against what `kind` names — at most "
                                 f"{MAX_REGEX_CHARS} characters. For "
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
                                       "care is needed. One or two sentences, at most "
                                       f"{MAX_DESCRIPTION_CHARS} characters"},
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
}

#: The `remind_feedback` object — how a hold turned out, the only evidence that tunes a pattern.
REMIND_FEEDBACK_FIELD = {
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
}
