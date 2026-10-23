"""A tool parameter that SWALLOWED the ones written after it, put back before validation.

MEASURED on `birthday-admin:20261022-080004`. The model closed its `summary` parameter with a
MISMATCHED tag — `</summary>` instead of `</parameter>` — and the provider's pseudo-XML
tool-call reader then ran on to the next real `</parameter>`. So `summary` arrived carrying its
own text PLUS its own closing tag followed by `<parameter name="accounting">[...]`, and the
`accounting` field arrived ABSENT. The engine answered "your `accounting` is incomplete: no
entry for d1…g1" — truthfully, about a field the model had written and could still see in its
own reply.

WHAT IT COST, which is why this is repaired rather than merely counted: that run spent turns
84-95 bisecting the field it could see — re-sending the same accounting, stripping unicode,
shortening entries, then one entry at a time — twelve turns at full prompt price. It ended by
shipping a 47-character diagnostic stub as the run's permanent summary ("Test call to diagnose
accounting field parsing.") with placeholder entries ("d2 met: test"), which the accounting
check accepted. The run's work was sound; only its record was destroyed.

THE SHAPE, from that transcript: the swallow hit every finish whose summary ran long
(1470-5170 chars, 9 of them) and no short one (47-103 chars, 5 of them) — the longer the
parameter, the likelier the model loses its closing tag. A scan of 149 fleet transcripts over
14 days found it in that one run: rare, and expensive when it happens.

WHY HERE. `normalize_action` already exists to strip provider and grammar debris before the
semantic validator judges the model's intent — an empty-string field a constrained decoder
minted, a tool-call envelope, a stray `thinking` key. A parameter the transport glued onto its
neighbour is the same class of damage, one layer earlier.

THREE CONDITIONS, all required, because a false repair eats text the model meant to write:

1. The closer must name the field CARRYING it — `</summary>` inside `summary`, immediately
   followed (whitespace aside) by `<parameter name="…">`. A run auditing this very defect
   quotes the broken shape in its own prose, and that prose must survive untouched.
2. Every restored name must be a REAL action field (`ACTION_SCHEMA`'s properties). Anything
   else is the model's own words about something that is not a field.
3. The field being restored must be EMPTY on the action. A value the provider did deliver is
   the model's; the salvage only ever fills a hole.

Any one of them failing abandons the WHOLE repair and returns the action untouched, by
identity — a partial guess is worse than the defect, which at least announces itself.

NOT SILENT. Each repair files `tool_param_salvaged`, one row per run, so the fleet rate of a
transport defect is readable instead of hidden behind a fix. It is deliberately NOT blocked
work (`readmodels/health_stream.BLOCKED_EVENTS`): the turn happened and its action ran —
`git_lock_cleared` is the precedent. It is deliberately NOT a `wrong_turn_detected` reason
either: that event IS blocked work, and its rows are the corpus of turns that produced nothing.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from .actionschema import ACTION_SCHEMA

if TYPE_CHECKING:                                  # pragma: no cover - typing only
    from .run_context import RunContext

#: The health event this module files. In `health_events.py`'s machine-checked header enum.
EVENT = "tool_param_salvaged"

#: Every real action field name — condition 2. A name outside this set is prose, not a field.
_FIELDS: frozenset[str] = frozenset(ACTION_SCHEMA.get("properties") or {})

#: `</carrier>` + whitespace + the next parameter's opening tag. The carrier's own name is
#: filled in per field, which is condition 1: a closer naming some OTHER tag is not this defect.
_SWALLOW = '</{0}>[ \t\r\n]*(?=<parameter name=")'

#: The opening tag of a parameter the carrier swallowed, anchored at the start of what is left.
_OPEN = re.compile(r'^<parameter name="([A-Za-z_][A-Za-z0-9_]*)">')


def _value(text: str) -> object:
    """A swallowed parameter's text as the field's value: JSON when it is JSON, else the text.

    The transport carries every parameter as text, so a list field (`accounting`, `args`,
    `paths`) arrives as its JSON rendering and must be read back as one. Anything that does not
    parse stays the string it is — a summary that merely opens with a bracket is not a list.
    """
    stripped = text.strip()
    if stripped[:1] in ("[", "{"):
        try:
            return json.loads(stripped)
        except ValueError:
            return text
    return text


def _chain(rest: str) -> list[tuple[str, str]] | None:
    """The `(name, text)` pairs packed into `rest`, or None if it is not that shape.

    Each parameter runs to its own closing tag followed by the next opening one, and the last
    runs to the end — the reply was cut where the model stopped writing, so the final field has
    no closer at all.
    """
    pairs: list[tuple[str, str]] = []
    while (opening := _OPEN.match(rest)) is not None:
        name = opening.group(1)
        body = rest[opening.end():]
        closer = re.search(_SWALLOW.format(re.escape(name)), body)
        if closer is None:
            pairs.append((name, body))
            return pairs
        pairs.append((name, body[:closer.start()]))
        rest = body[closer.end():]
    return pairs or None


def carrier(action: dict) -> str:
    """The field whose value swallowed the ones after it, or "" — the name the event records."""
    for key, val in action.items():
        if isinstance(val, str) and re.search(_SWALLOW.format(re.escape(key)), val):
            return key
    return ""


def repair(action: dict) -> tuple[dict, list[str]]:
    """`action` with any swallowed parameters put back, and the names that were restored.

    Returns the action ITSELF (same object, empty list) when nothing is wrong or when any of
    the three conditions fails — the hot path allocates nothing and a doubtful case is left
    exactly as the model sent it.
    """
    key = carrier(action)
    if not key:
        return action, []
    text = str(action[key])
    split = re.search(_SWALLOW.format(re.escape(key)), text)
    if split is None:                              # pragma: no cover - carrier() just matched
        return action, []
    pairs = _chain(text[split.end():])
    if not pairs:
        return action, []
    for name, _ in pairs:
        if name not in _FIELDS or action.get(name) not in ("", None, [], {}):
            return action, []                      # conditions 2 and 3: abandon the whole repair
    fixed = {**action, key: text[:split.start()].rstrip()}
    for name, body in pairs:
        fixed[name] = _value(body)
    return fixed, [name for name, _ in pairs]


def _already_filed(ctx: RunContext) -> bool:
    """Whether this run already filed a salvage row. Fails OPEN: a suppressor that cannot read
    its own history must not become the thing that silences the log.
    """
    from pathlib import Path

    try:
        from ..readmodels.health_stream import health_records

        run_id = str(getattr(ctx, "run_id", "") or "")
        return any(rec.get("event") == EVENT and rec.get("run_id") == run_id
                   for rec in reversed(health_records(Path(ctx.server.routines_home))))
    except Exception:
        return False


def record(ctx: RunContext, before: dict, fields: list[str]) -> None:
    """File one `tool_param_salvaged` row for this run.

    ONE row per run, not per turn: what the row answers is WHICH RUN met the transport defect,
    and the per-turn record is already on the transcript — the landed action carries the
    restored fields, so the repair is visible where the turn is read.

    Best-effort: a measurement must never cost the turn it measures.
    """
    from pathlib import Path

    if not fields:
        return
    try:
        if _already_filed(ctx):
            return
        from ..health_events import log_health_event

        named = ", ".join(fields)
        log_health_event(
            Path(ctx.server.routines_home), EVENT,
            routine=getattr(ctx.routine, "slug", "") or "",
            run_id=str(getattr(ctx, "run_id", "") or ""),
            detail=(f"the tool call's `{carrier(before)}` swallowed {named}: the model closed "
                    f"it with its own tag name instead of `</parameter>`. Restored before "
                    f"validation."),
            carrier=carrier(before), fields=named, turn=getattr(ctx, "turn", 0) + 1,
            model=getattr(ctx, "main_model", "") or None)
    except Exception:
        return
