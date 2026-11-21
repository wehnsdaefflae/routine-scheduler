"""The reminder store as a DERIVED VIEW of the hook stream — the second half of D152 step 4.

The report half of this step made `reports.jsonl` a projection outright (`reportproj`): one
writer, the row computed from the hook. The reminder half cannot go that far and the reason is
not timidity — it is that a reminder's record holds something the stream must NOT hold:

- the **TALLY** (`fires`, and the four labels). It is per-routine evidence, written by
  `reminders.record` on every fire with its own read-modify-write, and it is what tunes a
  pattern. A stream of definitions cannot carry a counter that a different process increments;
  mirroring it would give one number two writers, which is the defect this campaign exists to
  remove, not to spread.
- a **mute's run stamp**, which expires with the run that made it.

So the DEFINITIONS are derived here and the tallies stay where they are written: `definitions`
is what `hooks/reminderread` serves a run, joined there with the store's tallies. Nothing
compares the two stores any more — the drift check that measured them equal before the read was
cut over (0 drifting ids over all 31 live routines as of 0.549.0) was retired with the report
side's comparison, and the stream is the only source a definition is read from.

The fold is by id, last row wins, because the stream is append-only: a revise, a mute and a
remove are three later facts about one hook, and the last one is what is true now.
"""

from __future__ import annotations

from pathlib import Path

from . import split_addressee
from . import store as hookstore

#: What a `remove` disposition means for the derivation: the hook is GONE from the store, not
#: present-and-silent. `mute` is the opposite — still a definition, kept for its owner to see —
#: which is why only one of the three is subtractive.
REMOVING = ("remove",)

#: The curator's `dead` (D231) is NOT one of the record's dispositions: the row stays in the
#: store with its tally and its own `disposition` untouched, and the annotation lives in the
#: store's `dead` sidecar. So a disposal row keeps the definition in the derived view and does
#: NOT overwrite what the record says — otherwise this projection would serve `dead` where the
#: record says `keep`, a disposition no reader of a reminder knows.
ANNOTATING = ("dead",)


def definitions(routines_home: Path, *, routine: str = "") -> dict[str, dict]:
    """Every reminder DEFINITION the hook stream implies, by id — `{regex, description, kind,
    scope, reach, created_run, owner, disposition}`.

    `routine` narrows to the hooks a single routine HOLDS, which is the question the store
    answers: a hook is held by the routine it is addressed to (`self` meaning the origin's own
    future runs, a slug meaning a sibling set it there), never by the one that wrote the row.
    """
    out: dict[str, dict] = {}
    for hook in hookstore.read_hooks(routines_home):
        if hook.source != "reminder":
            continue
        origin = str(hook.origin.get("routine") or "")
        kind, name = split_addressee(hook.addressee)
        holder = origin if kind == "self" else name
        owner = "" if kind == "self" else origin
        if owner and owner == holder:
            # THE HOLDER ACTING ON A ROW IN ITS OWN STORE — `hookmirror.reminder_disposed`
            # records the target's say over a foreign hook with `routine` AND `target` both the
            # holder, so this row's origin names who DISPOSED of the hook, never who set it.
            # Deriving `owner` from it would project the holder as the owner of a hook a
            # sibling set, and `owner` is what decides who may remove one — so a `keep` or a
            # `mute` would silently transfer ownership away from the real setter. The
            # definition's ownership is established by the row that WROTE it; a later
            # disposition inherits it (and an unknown owner stays unknown rather than becoming
            # the holder).
            owner = str((out.get(hook.id) or {}).get("owner") or "")
        if routine and holder != routine:
            continue
        if hook.disposition in REMOVING:
            out.pop(hook.id, None)
            continue
        if hook.disposition in ANNOTATING:
            continue        # D231: the record is unchanged; the annotation is the store's own
        trigger = dict(hook.trigger)
        payload = dict(hook.payload)
        out[hook.id] = {"regex": str(trigger.get("regex") or ""),
                        "description": str(payload.get("description") or ""),
                        "kind": str(trigger.get("match") or ""),
                        "scope": str(payload.get("scope") or ""),
                        "reach": str(payload.get("reach") or ""),
                        "created_run": str(payload.get("created_run") or ""),
                        "owner": owner, "disposition": hook.disposition}
    return out

