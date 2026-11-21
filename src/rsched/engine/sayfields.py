"""A SIDE FIELD written inside `say` instead of beside it (F679).

`say` is prose the console publishes verbatim. The side fields — `note`, `remind`,
`remind_feedback` — are structured fields the engine files at no turn cost. `<note>…</note>`
written INSIDE `say` is published as markup and never filed.

So it is caught inside the schema-retry cycle (`actions.validate_action`), before the action
becomes a turn, and the run re-emits with the text in the real field; a warning after the fact
would still publish the markup and lose the note.

Only a PAIRED element is caught, not any angle bracket: `say` may legitimately talk about
`<note>`, and a check firing on a bare mention would leave the engine unable to describe itself.
"""

from __future__ import annotations

import re

from .sidefieldset import FILED_FIELDS

#: A whole ELEMENT named after a side field the engine files — `<note>…</note>`, attributes
#: allowed, nothing or anything between the tags. Neither tag alone qualifies: a run quoting
#: `<note>` or `</note>` in a sentence is describing the channel, not trying to use it.
_TAG = re.compile(r"<(" + "|".join(FILED_FIELDS) + r")(?:\s[^>]*)?>.*?</\1\s*>",
                  re.IGNORECASE | re.DOTALL)


def problems(action: dict) -> list[str]:
    """The one check: a side field's tag written inside `say`.

    Returns a correction naming the field and what to do — the run re-emits with the text in
    the real field, and nothing is published or lost in the meantime.
    """
    say = str(action.get("say") or "")
    if not (hit := _TAG.search(say)):
        return []
    field = hit.group(1).lower()
    return [(f"`say` contains a <{field}> tag. `say` is PROSE and is published verbatim, so "
             f"the markup would appear to the reader and the {field} would never be filed. "
             f"Put that text in the action's own `{field}` field (it rides any action at no "
             f"turn cost) and leave `say` as the narration.")]
