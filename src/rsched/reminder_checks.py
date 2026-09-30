"""What a reminder may say — the write gate's checks, shared by the engine's `remind` op
(`engine/remind.py`, inside the schema-retry cycle) and the library linter (`workflows/lint.py`),
so a pattern the engine would refuse can never sit in the library looking valid.
"""

from __future__ import annotations

import re

from .reminders import REACHES

MAX_REGEX_CHARS = 200
MAX_DESCRIPTION_CHARS = 400
_LEAD = re.compile(r"^\^([a-z_]+)([^a-z_]?)")
_LITERAL = re.compile(r"[^.^$*+?{}\[\]\\|()]*")
_FORMS = ("the forms are 'util:<name> <args…>', 'script:<name> <args…>', 'shell: <command>' "
          "and '<kind> <field>=<value>'")


def regex_problem(pattern: object) -> str | None:
    """Why this pattern may not be stored — or None when it is usable.

    Checked at the WRITE gate (inside the schema-retry cycle) so a malformed pattern is
    corrected before it becomes a turn, never silently dropped afterwards.
    """
    if not isinstance(pattern, str) or not pattern.strip():
        return "remind.regex must be a non-empty pattern over the canonical action string"
    if len(pattern) > MAX_REGEX_CHARS:
        return (f"remind.regex is {len(pattern)} characters — at most {MAX_REGEX_CHARS}; "
                "a pattern that long is matching a whole command, not a class of them")
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        return f"remind.regex is not a valid regular expression ({exc})"
    if compiled.search(""):
        return ("remind.regex matches the EMPTY string, so it would hold every action you "
                'take — anchor it to the action class you mean (e.g. "^util:fs-ops mv ")')
    return canon_problem(pattern)


def canon_problem(pattern: str) -> str | None:
    """A pattern anchored on a form no action renders as can never fire — the commonest way a
    reminder died (`^script name=…`, a script's args after `name=`, an edit's anchor after its
    path). Only a literal lead is judged; a pattern that opens with a group is the author's.
    """
    from .engine.actionschema import KINDS

    m = _LEAD.match(pattern)
    if not m:
        return None
    kind, sep = m[1], m[2]
    if kind not in KINDS:
        # a prefix of kind names is fine: `^write` holds write_file and write_util alike
        prefix = not sep and any(k.startswith(kind) for k in KINDS)
        return None if prefix else (f"remind.regex starts with {kind!r}; no action renders "
                                    f"that way — {_FORMS}")
    if kind in ("util", "script", "shell"):
        return _colon_form(kind, sep)
    return _field_form(kind, pattern[m.end():]) if sep == " " else None


def _colon_form(kind: str, sep: str) -> str | None:
    if sep in ("", ":"):
        return None
    what = "<command>" if kind == "shell" else "<name> <args…>"
    return f"a {kind} call renders as '{kind}:{' ' if kind == 'shell' else ''}{what}'"


def _field_form(kind: str, tail: str) -> str | None:
    """After `<kind> `, the literal text must be on its way to `<field>=`; a pattern that turns
    to regex syntax there (`.*`) is the author's to aim.
    """
    from .engine.actionschema import BRIEF_FIELD

    fields = ("path", "paths") if kind == "read_file" else (BRIEF_FIELD.get(kind, ""),)
    if not fields[0]:
        return f"a {kind} action renders as just '{kind}' — nothing follows it to match"
    lit = _LITERAL.match(tail)
    lead = lit[0] if lit else ""
    if lead and not any(f"{f}=".startswith(lead) or lead.startswith(f"{f}=") for f in fields):
        return (f"a {kind} action renders as '{kind} {fields[0]}=<value>' and carries nothing "
                "else (no content, no anchor text) — match on the value")
    return None


def reach_problem(reach: object) -> str | None:
    if reach in REACHES:
        return None
    return ('remind.reach must be "universal" (every routine whose action matches — a '
            'consequence any caller meets) or "listed" (only the routines whose settings list '
            "it — a caution for one kind of work)")


def description_problem(text: object) -> str | None:
    if not isinstance(text, str) or not text.strip():
        return ("remind.description must say what the consequence IS — the caution is what "
                "the hold shows you")
    if len(text) > MAX_DESCRIPTION_CHARS:
        return (f"remind.description is {len(text)} characters — at most "
                f"{MAX_DESCRIPTION_CHARS}; one or two sentences")
    return None
