"""A SIDE FIELD written inside `say` instead of beside it (F679).

`say` is prose the console publishes verbatim. The side fields — `note`, `remind`,
`remind_feedback` — are structured fields the engine files at no turn cost. A run that writes
`<note>…</note>` INSIDE its `say` gets the worst of both: the markup is published as prose, and
the note never reaches `state/notes.md`, so the next run's digest carries nothing. Nothing told
the run either way. Silence is the defect — not the mistake, which is one sentence to correct.

So it is caught where every correctable mistake is caught: inside the schema-retry cycle
(`actions.validate_action`), which hands the problem back before the action becomes a turn. The
run then re-emits with the text in the field that keeps it. A detector that merely WARNED after
the fact would publish the markup and lose the note exactly as before.

Why a PAIRED element and not any angle bracket: `say` legitimately carries prose about XML, HTML
and this engine's own prompt sections — `<note>` is named in the docs this repo ships, and in
this very module's own tests. A check that fired on a bare mention would make the engine unable
to talk about itself. What a run writes when it believes the channel is markup is a whole
element, `<note>…</note>`, so that is what is caught: an opening tag AND its closing tag.
"""

from __future__ import annotations

import re

#: The side fields a run may write beside `say`. A tag named after one of these inside `say` is
#: the mistake this module exists for; `actions.SIDE_FIELDS` is the same list in its own module
#: and is not imported here, because this one is pure and must not import the action contract.
_FIELDS = ("note", "remind", "remind_feedback")
#: A whole ELEMENT named after a side field — `<note>…</note>`, attributes allowed, nothing or
#: anything between the tags. Neither tag alone qualifies: a run quoting `<note>` or `</note>`
#: in a sentence is describing the channel, not trying to use it.
_TAG = re.compile(r"<(" + "|".join(_FIELDS) + r")(?:\s[^>]*)?>.*?</\1\s*>",
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
