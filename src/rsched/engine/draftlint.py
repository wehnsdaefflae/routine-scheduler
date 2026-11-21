"""Does a routine DRAFT name a path the engine owns? (D281)

Otherwise a new routine meets the write refusal on its FIRST run, while seeding state, with its
own Done-when lines pointing at a file that cannot exist. Run on the DRAFT, where the recipe can
still be reworded for free.

**WARN, never refuse:** the draft exists to be shown and corrected, and the author can satisfy
the seal another way (the seal is on the exact path). The warning reaches the drafting run and
the user; the write-time refusal is unchanged — this is an earlier reading, not a second gate.

**One list, from the module that owns it** (`engine/storeseal`'s constants; `stores(ctx)` needs
a run). The jobs file is included unconditionally, since a draft cannot know the dial.
"""

from __future__ import annotations

import re

#: Paths reserved outside `storeseal`'s map, each with the action or owner that does own it.
_OTHER_RESERVED: dict[str, str] = {
    ".memory/": ("the memory store is reached only through the `memory_read` / `memory_write` "
                 "actions — `read_file` and `write_file` are refused on those paths"),
    "routine.yaml": ("a routine's config is the user's: a run proposes a change with "
                     "`ask_user` + `config_patch`, it never writes this file"),
}


def _reserved() -> dict[str, str]:
    """Every path a generic file action may not write, each with what owns it instead.

    Composed from the owning modules' constants, never restated.
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

    A substring match at a word-ish boundary (not an exact token): a false positive costs one
    preview sentence, a miss costs a routine's first run.
    """
    pattern = re.escape(path.rstrip("/"))
    return len(re.findall(rf"(?<![\w/.-]){pattern}", text))


def findings(*parts: object) -> list[dict]:
    """The reserved paths this draft names, with the count and what owns each.

    `parts` carry the draft's prose (instruction, `done_when`, `never`, …), scanned as one text.
    Returns `[]` for a clean draft.
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

    Spliced into the preview: `reserved_paths` as data, `reserved_warning` as the sentence a
    model reads.
    """
    found = findings(draft.get("instruction"), draft.get("done_when"), draft.get("never"),
                     draft.get("setup"), draft.get("finish_line"))
    return {"reserved_paths": found, "reserved_warning": warning(found)} if found else {}


def warning(found: list[dict]) -> str:
    """The sentence the preview carries — what was named, and what to do about it.

    It addresses the drafting run and says creation is NOT blocked, so it is not read as a veto.
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
