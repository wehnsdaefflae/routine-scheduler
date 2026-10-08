"""The finish ACCOUNTING — a run's verdict on each thing it answers for, as data.

At its main finish a run carries `accounting`: one entry per line of its recipe's `## Done when`
(`donewhen.py`), one per open GOAL — what a person asked of the run (`goals.py`; a run the
operator started with a brief answers for its goals in place of the Done when) — and one per
open outcome of the routine's finish line (`finishline.py`):

    d1 met: <evidence>
    d2 unmet: <what remains and what it waits on>
    d3 not due: <how that was established>
    b1 met: <evidence>                   (any open goal; `unmet` too, except on a FINAL reply)
    g1 distance: <what remains>          (any open outcome)
    g2 met: <evidence>                   (only an outcome the run proves)

It is a field, not prose in the summary: the summary is written for the reader; verdicts
read back out of prose were lost one time in six. The gate checks the field once, at the main
finish only — a follow-up reply after the run ended answers the person, not the recipe; a
conversation's reply owes its goals only when it declares itself final — and records it where
every reader finds it: the run's status, the next run's digest, the finish line's distances,
the goals' ledger.
"""

from __future__ import annotations

import re

VERDICTS = ("met", "unmet", "not due", "distance")
_ENTRY_RE = re.compile(rf"^\s*([dgb]\d+)\s+({'|'.join(VERDICTS)})\s*:\s*(.*)$",
                       re.IGNORECASE | re.DOTALL)


def parse(entries: object) -> dict[str, tuple[str, str]]:
    """`{id: (verdict, note)}` from the finish field; an entry that does not parse is left out
    (`problems` reports the id it should have covered).
    """
    out: dict[str, tuple[str, str]] = {}
    for raw in entries if isinstance(entries, list) else []:
        m = _ENTRY_RE.match(str(raw))
        if m:
            out[m[1].lower()] = (m[2].lower(), m[3].strip())
    return out


def owed(done: list[dict], outcomes: list[dict]) -> list[str]:
    """The ids this finish must account for."""
    return [d["id"] for d in done] + [o["id"] for o in outcomes]


def problems(verdicts: dict[str, tuple[str, str]], done: list[dict],
             outcomes: list[dict], *, final: bool = False) -> dict[str, list[str]]:
    """What is wrong with an accounting: `missing` ids, `bare` entries whose verdict needs a note
    and has none, and `refused` entries that claim what their line cannot be (a `met` on an
    outcome only the operator judges, a distance on a Done-when line, an unmet goal on a reply
    declared `final`). Empty lists when sound.
    """
    judges = {o["id"]: o["judge"] for o in outcomes}
    ids = set(owed(done, outcomes))
    missing = [i for i in owed(done, outcomes) if i not in verdicts]
    bare = [i for i, (_v, note) in verdicts.items() if i in ids and not note]
    refused = []
    for i, (verdict, _note) in verdicts.items():
        if i.startswith("g") and i in judges:
            if verdict in ("unmet", "not due"):
                refused.append(f"{i}: an outcome of the finish line takes `distance` or, when "
                               "the run proves it, `met`")
            elif verdict == "met" and judges[i] != "run":
                refused.append(f"{i}: only the operator judges this outcome — report its "
                               "distance")
        elif i[0] in "db" and verdict == "distance":
            refused.append(f"{i}: a Done-when line or a goal is met, unmet or not due")
        elif final and i[0] == "b" and i in ids and verdict != "met":
            refused.append(f"{i}: a FINAL reply meets every open goal — do the work, drop the "
                           "goal on the person's word (goal verb=drop), or reply with "
                           "final: false")
    return {"missing": missing, "bare": bare, "refused": refused}


def gaps(found: dict[str, list[str]]) -> str:
    """What `problems` found, as one clause list — shared by the finish's deferral and a task
    checkpoint's refusal (engine/taskops.py), which account for Done-when lines alike.
    """
    parts = []
    if found["missing"]:
        parts.append(f"no entry for {', '.join(found['missing'])}")
    if found["bare"]:
        parts.append(f"nothing after the verdict for {', '.join(found['bare'])} — met names "
                     "its evidence, unmet and distance what remains, not due how that was "
                     "established")
    parts += found["refused"]
    return "; ".join(parts)


def deferral(found: dict[str, list[str]]) -> str:
    """The one observation a finish is set aside with when its accounting is incomplete."""
    return ("OBSERVATION (finish deferred): your `accounting` is incomplete: "
            + gaps(found) + ". Carry one entry per line you owe — each line of your "
            "recipe's Done when, each open goal (`b<n>`) and each open outcome of the finish "
            "line: `d<n> met: <evidence>`, `d<n> unmet: <what remains>`, `d<n> not due: <how "
            "established>`, `b<n> met: <evidence>`, `g<n> distance: <what remains>` — then "
            "finish again.")


def unmet_residuals(accounting: object) -> list[str]:
    """The entries a later run inherits: what an earlier finish left unmet, as its own words."""
    return [f"{i} — {note}" for i, (v, note) in parse(accounting).items()
            if v == "unmet" and note]
