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

Called from the TESTS alone — no `src/` module imports it, and the codemap flags it as an orphan
for that reason. That is the design, not neglect: this module is the CHECK that the store is a
derived view, not a step in the write path.

So the DEFINITIONS are derived here and the tallies stay where they are written. `definitions`
is what makes "derived view" a claim a test can check rather than a sentence in a design:
fold the stream, read the store, and the definition of every hook must agree. A disagreement
means something wrote a reminder without going through the write path — the one thing the step
set out to make impossible.

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
        if routine and holder != routine:
            continue
        if hook.disposition in REMOVING:
            out.pop(hook.id, None)
            continue
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


def stored_definitions(routine_dir: Path) -> dict[str, dict]:
    """The same shape, read from the routine's OWN store — the other side of the comparison.

    Deliberately not a second fold: it reads through `reminders.load_local`, so what is
    compared is what a run would actually hold, not a re-parse of the file.
    """
    from ..reminders import load_local

    held, _ = load_local(routine_dir)
    return {r.id: {"regex": r.regex, "description": r.description, "kind": r.kind,
                   "scope": r.scope, "reach": r.reach, "created_run": r.created_run,
                   "owner": r.owner, "disposition": r.disposition}
            for r in held}


def drift(routines_home: Path, routine_dir: Path) -> dict[str, tuple[dict | None, dict | None]]:
    """Where the derived view and the store DISAGREE, by id — `(from_the_stream, in_the_store)`.

    Empty is the campaign's claim for the reminder half. A non-empty answer names exactly which
    hook was written without going through the write path, which is worth more than a boolean:
    it points at the writer, and a writer outside the one path is what step 4 removes.

    Local scope only, because that is what a routine's own store holds; a curated reminder
    lives in the library and is read from there by every routine that it reaches.
    """
    derived = {k: v for k, v in definitions(routines_home,
                                            routine=routine_dir.name).items()
               if v["scope"] == "local"}
    stored = {k: v for k, v in stored_definitions(routine_dir).items()
              if v["scope"] == "local"}
    return {rid: (derived.get(rid), stored.get(rid))
            for rid in set(derived) | set(stored)
            if derived.get(rid) != stored.get(rid)}
