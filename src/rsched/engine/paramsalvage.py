"""A tool parameter that SWALLOWED the ones written after it, put back before validation.

The defect: the model closes a long parameter with a mismatched tag (`</summary>` instead of
`</parameter>`), the provider's pseudo-XML reader runs on to the next real `</parameter>`, and
the following fields arrive glued inside the carrier and ABSENT on the action. The run is then
told a field it can see in its own reply is missing, and burns turns bisecting it. Rare, and
expensive when it happens, so it is repaired rather than merely counted — beside
`normalize_action`'s other transport-debris repairs, before the semantic validator.

THREE CONDITIONS, all required, because a false repair eats text the model meant to write:

1. The closer must name the field CARRYING it, immediately followed (whitespace aside) by
   `<parameter name="…">` — prose quoting the broken shape must survive untouched.
2. Every restored name must be a REAL action field (`ACTION_SCHEMA`'s properties).
3. The field being restored must be EMPTY on the action; the salvage only fills a hole.

Any failure abandons the WHOLE repair and returns the action untouched, by identity.

Each repair files `tool_param_salvaged`, one row per run. It is NOT blocked work
(`health_stream.BLOCKED_EVENTS`) nor a `wrong_turn_detected` reason: the action ran.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

from .actionschema import ACTION_SCHEMA

if TYPE_CHECKING:                                  # pragma: no cover - typing only
    from .run_context import RunContext

#: The health event this module files. In the machine-checked `health_vocab.EVENTS`.
EVENT = "tool_param_salvaged"

#: Every real action field name — condition 2. A name outside this set is prose, not a field.
_FIELDS: frozenset[str] = frozenset(ACTION_SCHEMA.get("properties") or {})

#: `</carrier>` + whitespace + the next parameter's opening tag. The carrier's own name is
#: filled in per field, which is condition 1: a closer naming some OTHER tag is not this defect.
_SWALLOW = '</{0}>[ \t\r\n]*(?=<parameter name=")'

#: The opening tag of a parameter the carrier swallowed, anchored at the start of what is left.
_OPEN = re.compile(r'^<parameter name="([A-Za-z_][A-Za-z0-9_]*)">')


def _value(text: str) -> object:
    """A swallowed parameter's text as the field's value: JSON when it parses (a list field
    arrives as its JSON rendering), else the text unchanged.
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

    Each parameter runs to its own closer + the next opener; the last runs to the end (it has
    no closer at all).
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

    Returns the action ITSELF (same object, empty list) when nothing is wrong or any condition
    fails — the hot path allocates nothing.
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
    """Whether this run already filed a salvage row. Fails OPEN, so it never silences the log."""
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

    One row per run, not per turn: the per-turn record is the landed action on the transcript.
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
