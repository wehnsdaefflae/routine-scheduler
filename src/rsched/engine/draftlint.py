"""Does a routine DRAFT name a path the engine owns? — D281, answered at creation.

The gap this closes, measured: `drawerrr-irreversible-actions` was drafted with its obligations
register at `state/obligations.json` — 11 occurrences across three stage files — and met the
engine's refusal on its FIRST run while seeding state. That is the worst possible moment: there
is no prior state to fall back on, and the recipe's own `## Done when` lines then pointed at a
file that cannot exist. The run filed it as a template defect (R3327); it was not one — all 15
installed templates leave the register's filename to the routine, correctly, so the path was
authored by the drafting run itself and the fix propagated nowhere.

The operator's answer names where the check belongs: *"At creation: `create_routine` lints the
draft's prose against the engine's reserved-path list and refuses or warns before the routine
exists"*. So it runs on the DRAFT, where a recipe can still be reworded for free.

**WARN, never refuse — and that is the whole design judgement here.** The draft step exists to
be shown to the user and corrected; a refusal would make the engine the author of a recipe's
filenames, and the paths are reserved for a reason the author can satisfy another way (a
register at `state/obligations-register.json` is fine; the seal is on the exact path). A warning
that rides the preview reaches the drafting run AND the user who reads it one screen later,
which is every reader who can act on it. The refusal at write time stays exactly as it was: this
is an earlier reading of the same list, not a second gate.

**One list, read from the module that owns it.** The paths come from `engine/storeseal`, whose
`stores(ctx)` answers for a live run. A draft has no run, so the names are taken from the same
constants `stores` composes and the dial-dependent one is included unconditionally — at draft
time nothing says whether that routine will keep jobs, and naming `state/jobs.json` in a recipe
is worth a word either way. A second hand-written list here would be the real defect: it would
go stale the first time a store was added, and nothing would say so.
"""

from __future__ import annotations

import re

#: The `.memory/` seal and the two config files are reserved by a different mechanism than
#: `storeseal`'s per-file map (the memory actions own one, the user owns the others), so they are
#: named here — with the action or owner that does own them, because a warning that does not say
#: what to do instead costs the reader the same turn the refusal would have.
_OTHER_RESERVED: dict[str, str] = {
    ".memory/": ("the memory store is reached only through the `memory_read` / `memory_write` "
                 "actions — `read_file` and `write_file` are refused on those paths"),
    "routine.yaml": ("a routine's config is the user's: a run proposes a change with "
                     "`ask_user` + `config_patch`, it never writes this file"),
}


def _reserved() -> dict[str, str]:
    """Every path a generic file action may not write, each with what owns it instead.

    Composed from `engine/storeseal`'s own constants rather than restated, so a store added
    there is screened here without anyone remembering to. `storeseal.stores` itself needs a run
    context and cannot serve a draft, which is why this reads the pieces.
    """
    from .. import runledger
    from ..jobs import JOBS_FILE
    from . import obligations, storeseal

    return {str(obligations.FILE): obligations.REFUSAL,
            runledger.FILE: runledger.REFUSAL,
            runledger.ARCHIVE_DIR: runledger.ARCHIVE_REFUSAL,
            JOBS_FILE: storeseal.JOBS_REFUSAL,
            **_OTHER_RESERVED}


def _mentions(text: str, path: str) -> int:
    """How many times `text` names `path`, counting a mention however it is written.

    A recipe writes a path in prose and in backticks, with or without a leading `./`, and
    `.memory/` is a directory whose mentions are of files beneath it. So the match is on the
    path as a substring at a word-ish boundary rather than on an exact token: the cost of a
    false positive is one sentence in a preview, and the cost of a miss is a routine's first run.
    """
    pattern = re.escape(path.rstrip("/"))
    return len(re.findall(rf"(?<![\w/.-]){pattern}", text))


def findings(*parts: object) -> list[dict]:
    """The reserved paths this draft names, with the count and what owns each.

    `parts` are whatever carries the draft's prose — the instruction, the `done_when` lines, the
    `never` lines, a generated pattern body. Each is stringified and scanned as one text, because
    a path named in `done_when` is exactly as broken as one named in the instruction: that line
    is accounted for at every finish.

    Returns `[]` for a clean draft, which is the common case and must cost nothing to read.
    """
    text = "\n".join(str(p) for p in parts if p)
    if not text:
        return []
    out: list[dict] = []
    for path, owner in _reserved().items():
        if count := _mentions(text, path):
            out.append({"path": path, "mentions": count, "owned_by": owner})
    return out


def preview_keys(draft: dict) -> dict:
    """The keys this lint contributes to the draft/preview observation — `{}` when it is clean.

    Shaped as keys to SPLICE rather than as a call the preview makes conditionally, because
    `engine/create_routine.py` sits exactly at its size-ratchet ceiling: `**preview_keys(draft)`
    costs that file no line at all, and a clean draft adds nothing to the observation either.

    Both halves ride: `reserved_paths` for anything reading the observation as data, and
    `reserved_warning` as the sentence a model actually reads.
    """
    found = findings(draft.get("instruction"), draft.get("done_when"), draft.get("never"),
                     draft.get("setup"), draft.get("finish_line"))
    return {"reserved_paths": found, "reserved_warning": warning(found)} if found else {}


def warning(found: list[dict]) -> str:
    """The sentence the preview carries — what was named, and what to do about it.

    It addresses the drafting run, because that is who can still reword the recipe, and it says
    the engine will NOT stop the creation: a warning read as a veto would send the run looking
    for an override instead of for a filename.
    """
    if not found:
        return ""
    named = ", ".join(f"{f['path']} ({f['mentions']}x)" for f in found)
    return (f"RESERVED PATHS NAMED IN THIS DRAFT: {named}. The engine owns these, and a generic "
            "`write_file` / `edit_file` / `delete` on one is REFUSED at run time — which a new "
            "routine discovers on its FIRST run, while seeding state, with no prior state to "
            "fall back on and its own Done when pointing at a file that cannot exist (D281). "
            "Nothing is blocked here: reword the recipe before you confirm, or confirm as it "
            "stands and the routine will meet the refusal. Each path's owner: "
            + " · ".join(f"{f['path']} — {f['owned_by']}" for f in found))
