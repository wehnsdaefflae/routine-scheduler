"""The two reminder SIDE FIELDS as schema — `remind` and `remind_feedback`.

**Every cap these fields enforce is stated here, interpolated from the constants that enforce
it** (F680), so a run learns a limit without being refused by it. The numbers are imported,
never retyped, so the documentation cannot drift from the cap.
"""

from __future__ import annotations

from ..reminder_checks import MAX_DESCRIPTION_CHARS, MAX_REGEX_CHARS

#: The `remind` object — leave yourself (or another routine) a consequence reminder.
REMIND_FIELD = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "op": {"type": "string", "enum": ["add", "revise", "delete", "dead"],
               "description": "add a new reminder, revise one, delete it, or — with "
                              "`target`, as curator — mark a row in ANOTHER routine's "
                              "store dead: it keeps its tally and stops firing"},
        "id": {"type": "string",
               "description": "revise/delete/dead: the reminder's id (rem-…) — exactly as a "
                              "fire names it; anything else is refused"},
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
                                       f"{MAX_DESCRIPTION_CHARS} characters. On "
                                       "op=dead/delete with a `target`: the REASON the row "
                                       "is retired, stored and required"},
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
                   "description": "ANOTHER routine's slug. op=add SETS this hook on it: it "
                                  "fires in ITS runs from its next one on, and that routine "
                                  "decides on each fire whether to keep, mute or remove it; "
                                  "you cannot revise it, and a pattern it removed you cannot "
                                  "set again. Use it for a consequence YOU observed that "
                                  "lands on THAT routine's work. op=dead/delete DISPOSES of "
                                  "a row already in its store — the curator's act, needing "
                                  "`reminders: global`, and the one channel that reaches a "
                                  "routine that never runs again"},
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
                   "it and whose disposition over it outranks yours — or, as curator, "
                   "retires a row already in that routine's store (`op=dead`).",
}

#: One `remind_feedback` entry — how a hold turned out, the only evidence that tunes a pattern.
#: The field itself takes this object OR a LIST of them (F925): there is no cap on how many
#: reminders fire on one turn and the engine asks for EVERY fire to be labelled, so a run whose
#: single action met two holds had to drop one piece of evidence or spend a turn being refused.
_FEEDBACK_ENTRY = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "id": {"type": "string", "description": "the reminder that fired"},
        "label": {"type": "string",
                  "enum": ["could_not", "would_have", "did", "didnt"],
                  "description": "could_not = the consequence was impossible for that "
                                 "action (the pattern is too broad) · would_have = it was on "
                                 "track and you avoided it · did = you went ahead and it "
                                 "happened · didnt = you went ahead and nothing happened"},
        "disposition": {"type": "string", "enum": ["keep", "mute", "remove"],
                        "description": "only for a hook ANOTHER routine set on you (the fire "
                                       "says who), and your say outranks it: keep (leave it "
                                       "live), mute (silence it for the rest of THIS run; its "
                                       "owner still sees it), remove (delete it, and its owner "
                                       "cannot set that pattern on you again)"},
    },
}

#: The field: one entry, or a LIST of them when several reminders fired on the same turn.
#:
#: ONE schema accepting both types rather than a `oneOf` over two: `properties` applies only to
#: an object and `items` only to an array, so each shape is still constrained — and the entry's
#: description prose is emitted ONCE. A `oneOf` rendered it twice, which measured +1,568 chars
#: on the always-on prompt contract (the shrink-only ratchet caught it) for a field that says
#: the same thing either way. The entries of a LIST are judged by `remindfields`, which judges
#: every entry exactly as it judges a lone object.
REMIND_FEEDBACK_FIELD = {
    **_FEEDBACK_ENTRY,
    "type": ["object", "array"],
    # No `items`/`minItems`/`maxItems`: they render ~46 chars of the always-on schema dump to
    # say what `remindfields` already enforces better — it judges EVERY entry exactly as it
    # judges a lone object and refuses a repeated id, which no JSON Schema keyword can express.
    "description": "OPTIONAL, on any action: label how a reminder's HOLD turned out — one "
                   "{id, label}, or a LIST of them when SEVERAL fired on one turn (each fire "
                   "wants its own). Costs no turn and is the only evidence that tunes the "
                   "pattern: carry it as soon as you know.",
}
