"""Every action ONE model reply carried, in the order it wrote them — and which of them runs.

The engine executes exactly ONE action per turn: the FIRST the reply wrote. Every later one is
returned to the run unexecuted, by name, in the observation of the one that ran
(`unexecuted_note`). Nothing a model emits is dropped without a word.

**Why the first.** A thinking model chains: it writes an action, thinks on as though it had
run, and writes the next — whose `say` then reports the first one's effect ("Fields added. Now
computing it in run_probe"). Since 0.396.0 put the Anthropic action tool on `auto` so effort
reaches the model, Opus 5 does this on most multi-step moments: the FIRST action arrives as JSON
in a text block, the second as the tool call — and the adapter's `parsed` (the call) was all the
engine read. So the second ran, the first vanished, and the second's narration claimed a change
nobody had made (R2443-R2445: an import, test files, a pushed fix, an ask body — 17 false claims
in two llmsectest runs, and fleet-wide wherever an Anthropic model thinks). Replaying the real
turn 194 of llmsectest-weekday:20261010-040001 through the live proxy reproduced the shape five
times in five; a sharper tool description changed nothing, and with no tool offered at all the
model still wrote two actions as two text blocks. Running the LATER action is wrong whichever
channel carried it: it presupposes the earlier one. Running the first is what the model meant to
happen first, and it is the only one whose narration describes a world that exists.

**Order.** A reply's text precedes its tool call — the Messages API ends a turn at the call, and
an OpenAI-compatible reply's content precedes its tool calls — so the actions written as text come
first, in text order, then the call (`Completion.parsed`).

**What the run is told.** Each unexecuted action is stored on the observation of the one that ran
(`not_executed`: its canonical line and its `say`, `unexecuted_row`), and `format_observation`
appends `unexecuted_note` to that observation — so a resumed leg replays the same words, and the
transcript is the audit of how often a reply carried more than one.

**What counts.** A JSON object in the text whose `kind` is an action kind. An object differing
from another only in its narration (`say`, `note`, the reminder fields) is the SAME action
written twice — the scripted harness, and a model that writes its call out as text too, both do
that — and runs once with no note: telling the model a duplicate "did not run" would invite it
to repeat an edit.
"""

from __future__ import annotations

import json
import re
from typing import TypeGuard

from ..schema_guard import SchemaViolation, extract_json
from .actionschema import KINDS, canon

#: Fields that narrate an action rather than define it: two objects equal but for these are one
#: action written twice.
NARRATION_FIELDS = frozenset({"say", "note", "remind", "remind_feedback"})

#: A text that names an action kind but yields no readable action — the one shape in which the
#: engine cannot know what came first.
_NAMES_A_KIND = re.compile(r'"kind"\s*:\s*"(' + "|".join(KINDS) + r')"')

UNREADABLE_BESIDE_CALL = (
    "your reply wrote an action as text that could not be parsed, AND made an action call. "
    "The engine executes exactly ONE action per reply — the first — and it cannot tell what "
    "the unreadable one was, so it ran neither. Reply with ONLY the one action you want to run "
    "now.")

_DECODER = json.JSONDecoder(strict=False)   # tolerant of raw newlines inside strings, like
                                            # schema_guard.loads_tolerant


def _is_action(obj: object) -> TypeGuard[dict]:
    return isinstance(obj, dict) and obj.get("kind") in KINDS


def written_actions(text: str) -> list[dict]:
    """The action objects a reply's TEXT carries, in order. Scans every top-level `{` with the
    JSON decoder and skips past each object it reads, so an action quoted inside another
    action's string (a `content` holding JSON) is never counted as a second one.
    """
    found: list[dict] = []
    pos = 0
    while (start := text.find("{", pos)) != -1:
        try:
            obj, end = _DECODER.raw_decode(text, start)
        except json.JSONDecodeError:
            pos = start + 1
            continue
        if _is_action(obj):
            found.append(obj)
        pos = end
    return found


def _defining(action: dict) -> dict:
    return {k: v for k, v in action.items() if k not in NARRATION_FIELDS}


def _distinct(actions: list[dict]) -> list[dict]:
    """`actions` with each one that repeats an earlier one (narration aside) removed."""
    kept: list[dict] = []
    for action in actions:
        if all(_defining(action) != _defining(k) for k in kept):
            kept.append(action)
    return kept


def reply_actions(completion) -> list[dict]:
    """Every distinct action `completion` carried, in reply order — never empty. The engine
    runs element 0; the rest are reported back unexecuted.

    A text-only reply with no action-kind object falls back to `extract_json` (a reply whose
    one object is an envelope `normalize_action` unwraps, or none at all), so a reply that
    parses today still parses, and one that does not still raises SchemaViolation.
    """
    text = completion.text or ""
    written = written_actions(text)
    if completion.parsed is None:
        return _distinct(written) if written else [extract_json(text)]
    if not written and _NAMES_A_KIND.search(text):
        try:
            obj = extract_json(text)
        except SchemaViolation:
            obj = None
        if not _is_action(obj):
            raise SchemaViolation([UNREADABLE_BESIDE_CALL])
        written = [obj]
    return _distinct([*written, completion.parsed])


#: How much of an unexecuted action the run is shown again: enough to recognise it and re-emit it,
#: never its payload (a write's content is in the run's own reply, one message up).
ROW_CHARS = 200


def unexecuted_row(action: dict) -> dict:
    """What an observation keeps of an action that did not run: its canonical line and its say."""
    return {"action": canon(action)[:ROW_CHARS], "say": str(action.get("say") or "")[:ROW_CHARS]}


def unexecuted_note(rows: list[dict] | None) -> str:
    """The tail an observation carries when its reply held more actions than the one that ran;
    "" when it held one. Names each by its canonical line and its own say — the say is the part
    that claimed an effect, so it is the part the run has to see did not happen.
    """
    if not rows:
        return ""
    others = len(rows)
    lines = "\n".join(f"- {r.get('action', '?')}"
                      + (f' — it said: "{r["say"]}"' if r.get("say") else "")
                      for r in rows)
    they, it = ("it", "it") if others == 1 else ("they", "one of them")
    return (f"\n\n[NOT EXECUTED: your reply carried {others + 1} actions. The engine runs exactly "
            "ONE action per reply — the FIRST, whose result is above. "
            f"{'This one' if others == 1 else f'These {others}'} did NOT run, and nothing "
            f"{they} assumed has happened:\n{lines}\n"
            f"Emit ONE action per reply. If you still want {it}, emit it again now that you have "
            "this result.]")
