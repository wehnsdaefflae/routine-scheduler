"""What the user ANSWERED, read from the records his answer actually writes.

Answering an audit decision writes two things (`web/api_audit.write_feedback`, under a lock):

1. a durable marker in `<self-audit>/audit/decisions-answered.json` — `{id: iso}`, which says
   only THAT he answered, and outlives the inbox message a mid-run delivery consumes instantly;
2. a queued inbox message `[AUDIT decision · <id>] selected: <his words>` — the only place
   WHAT he chose exists until self-audit's next run copies it into the report's `resolution`.

WHY THIS MODULE EXISTS. Between his click and that next run, the report still says
`status: "open"` with an empty `resolution`, so every surface reading the report alone shows an
answered decision as awaiting him. On 2026-10-25 he answered D210-D215 between 02:07 and 02:17;
the report generated at 02:40 listed all six `open` with no resolution, and a routine reported
them back to him as outstanding. His instruction: *"also make the report record a resolution for
an answer that arrived mid-run"*. The report file belongs to self-audit, so the engine cannot
write its field — what the engine owns is the read model every surface reads, and that is where
the answer is merged in (`readmodels/items._report_item`).

It is a separate module rather than three more functions in `items.py` because that file sits at
the house 350-line bar: adding the reader there crossed it (378), and the ratchet refuses to
baseline growth. One responsibility — reading an answer — one file.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..paths import read_json

#: The text shape `web/api_audit._format_feedback` writes for an answered decision. Group 1 is
#: the decision id, group 2 is what he chose. DOTALL because a free-text answer spans lines.
_ANSWER_TEXT_RE = re.compile(r"\[AUDIT decision · ([^\]]+)\] selected: (.*)", re.DOTALL)


def queued_answer_words(routine_dir: Path) -> dict[str, str]:
    """`{decision id: what the user chose}` for every answer still queued in the audit inbox.

    Best-effort by construction: a mid-run delivery consumes the message, after which the
    durable marker is all that is left — callers fall back to it (and say "answered <ts>"
    rather than inventing words nobody wrote).
    """
    out: dict[str, str] = {}
    inbox = routine_dir / "inbox"
    if not inbox.is_dir():
        return out
    for path in sorted(inbox.glob("msg-*.json")):
        obj = read_json(path)
        if not isinstance(obj, dict):
            continue
        if (m := _ANSWER_TEXT_RE.match(str(obj.get("text") or ""))):
            out[m.group(1).strip().upper()] = m.group(2).strip()
    return out
