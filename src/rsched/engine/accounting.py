"""The finish ACCOUNTING — a run's verdict on each thing it answers for, as data.

At its main finish a run carries `accounting`: one entry per line of its recipe's `## Done when`
(`donewhen.py`), one per open GOAL — what a person asked of the run (`goals.py`; a run the
operator started with a brief answers for its goals in place of the Done when) — and one per
open outcome of the routine's finish line (`finishline.py`):

    d1 met: <evidence>
    d2 unmet: <what remains and what it waits on>
    d3 not due: <how that was established>
    b1 met: <evidence>                   (any open goal; `unmet` too, except on a FINAL reply)
    b2 waits on operator: <his act>      (an unmet goal whose next move is someone else's)
    g1 distance: <what remains>          (any open outcome)
    g2 met: <evidence>                   (only an outcome the run proves)

What goes after the verdict is the ACCOUNT, and it has a content floor (`is_placeholder`): a
clause of fewer than two words, or one made only of words a real account never consists of
alone (`test`, `tbd`, `n/a`, `ok`, …), is refused with the entry named. The floor is about
FORM, not truth — truth is the claim verifier's question (`engine/verifier.py`).

It is a field, not prose in the summary, because verdicts read back out of prose get lost. The
gate checks it once, at the main finish only (a conversation reply owes its goals only when it
declares itself final), and records it in the run's status, the next run's digest, the finish
line's distances and the goals' ledger.
"""

from __future__ import annotations

import re

VERDICTS = ("met", "unmet", "not due", "distance")
_ENTRY_RE = re.compile(rf"^\s*([dgb]\d+)\s+({'|'.join(VERDICTS)})\s*:\s*(.*)$",
                       re.IGNORECASE | re.DOTALL)

#: Words a real account never consists of ALONE. A clause made only of these (and
#: punctuation) answers for nothing, whatever verdict it carries — `is_placeholder`.
PLACEHOLDER_WORDS = frozenset({
    "test", "tests", "tested", "tbd", "tba", "todo", "n/a", "na", "none", "nothing", "nil",
    "null", "ok", "okay", "fine", "done", "yes", "same", "placeholder", "stub", "dummy",
    "foo", "bar", "baz", "asdf", "x", "xx", "xxx", "y", "z",
})
#: Punctuation stripped from a word's EDGES before it is weighed, so `tbd.` and `test,` are
#: the words they are — and `n/a` and `state/a.txt` keep what is inside them.
_EDGE = ".,;:!?()[]{}<>\"'`*…-–—_|/\\"


#: `b<n> waits on <who>: <the act it waits on>` — an UNMET goal whose next move is not this
#: routine's. `who` is `operator`, `external` (the world) or a routine slug. It parses as
#: `unmet` everywhere; `waits` reads the addressee, which `obligations.save` keeps and
#: `daemon/owed_watch` routes. Goals only: a Done-when line is the run's own, whoever it waits on.
_WAITS_RE = re.compile(r"^\s*(b\d+)\s+waits\s+on\s+([a-z0-9][a-z0-9_.-]*)\s*:\s*(.*)$",
                       re.IGNORECASE | re.DOTALL)
OPERATOR, EXTERNAL = "operator", "external"


def parse(entries: object) -> dict[str, tuple[str, str]]:
    """`{id: (verdict, note)}` from the finish field; an entry that does not parse is left out
    (`problems` reports the id it should have covered). A `waits on` entry reads as `unmet`.
    """
    out: dict[str, tuple[str, str]] = {}
    for raw in entries if isinstance(entries, list) else []:
        if w := _WAITS_RE.match(str(raw)):
            out[w[1].lower()] = ("unmet", w[3].strip())
        elif m := _ENTRY_RE.match(str(raw)):
            out[m[1].lower()] = (m[2].lower(), m[3].strip())
    return out


def waits(entries: object) -> dict[str, str]:
    """`{goal id: who}` for every `b<n> waits on <who>:` entry — lowercased."""
    out: dict[str, str] = {}
    for raw in entries if isinstance(entries, list) else []:
        if w := _WAITS_RE.match(str(raw)):
            out[w[1].lower()] = w[2].lower()
    return out


def arrived(entries: object) -> bool:
    """Whether an `accounting` FIELD reached the engine at all (F805).

    `parse` flattens a missing key, `null`, `[]` and blanks all to `{}`; a field that never
    arrived needs a different refusal from one whose CONTENT falls short, because the usual
    author of the absence is the transport (`engine/paramsalvage.py`), not the run.
    """
    return any(str(e).strip() for e in entries) if isinstance(entries, list) else False


def is_placeholder(evidence: str) -> bool:
    """True when an evidence clause answers for NOTHING — the content floor, two tests deep.

    WORDS, not characters: legitimate `not due` accounts are nearly as short as placeholders
    (`no filing`), so fewer than two words says nothing, and so does a clause made only of
    `PLACEHOLDER_WORDS` (and punctuation). Not a judgement of TRUTH — that is the verifier's.
    """
    words = [w for w in (t.strip(_EDGE) for t in evidence.split()) if w]
    return len(words) < 2 or all(w.lower() in PLACEHOLDER_WORDS for w in words)


def owed(done: list[dict], outcomes: list[dict]) -> list[str]:
    """The ids this finish must account for."""
    return [d["id"] for d in done] + [o["id"] for o in outcomes]


def problems(verdicts: dict[str, tuple[str, str]], done: list[dict],
             outcomes: list[dict], *, final: bool = False,
             arrived: bool = True) -> dict[str, list[str]]:
    """What is wrong with an accounting: `absent` ids no field was received for at all
    (`arrived=False`; exclusive with `missing`), `missing` ids, `bare` entries whose verdict
    needs a note and has none, `placeholder` entries whose note answers for nothing (named as
    whole ENTRIES, so the refusal shows the model what it wrote), and `refused` entries that
    claim what their line cannot be (a `met` on an outcome only the operator judges, a distance
    on a Done-when line, an unmet goal on a reply declared `final`). Empty lists when sound.

    `bare` and `placeholder` are exclusive, so the deferral says one thing per entry. A
    placeholder entry is rendered from `parse` (id and verdict lowercased, clause verbatim).
    """
    judges = {o["id"]: o["judge"] for o in outcomes}
    ids = set(owed(done, outcomes))
    unseen = [i for i in owed(done, outcomes) if i not in verdicts]
    absent = [] if arrived else unseen
    missing = unseen if arrived else []
    bare = [i for i, (_v, note) in verdicts.items() if i in ids and not note]
    placeholder = [f"{i} {v}: {note}" for i, (v, note) in verdicts.items()
                   if i in ids and note and is_placeholder(note)]
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
    return {"absent": absent, "missing": missing, "bare": bare, "placeholder": placeholder,
            "refused": refused}


def gaps(found: dict[str, list[str]]) -> str:
    """What `problems` found, as one clause list — shared by the finish's deferral and a task
    checkpoint's refusal (engine/jobops.py), which account for Done-when lines alike.
    """
    parts = []
    if found.get("absent"):
        parts.append(f"nothing was received for {', '.join(found['absent'])}")
    if found["missing"]:
        parts.append(f"no entry for {', '.join(found['missing'])}")
    if found["bare"]:
        parts.append(f"nothing after the verdict for {', '.join(found['bare'])} — met names "
                     "its evidence, unmet and distance what remains, not due how that was "
                     "established")
    if found["placeholder"]:
        parts.append(f"{', '.join(found['placeholder'])} — a placeholder clause accounts for "
                     "nothing: name the observation or artefact that shows the verdict (what "
                     "you read back, what landed where, what the check said), in your own "
                     "words")
    parts += found["refused"]
    return "; ".join(parts)


#: What every accounting deferral ends with: the entries the finish owes, in their forms. Both
#: heads of `deferral` share it — they differ in the DIAGNOSIS, never in what is owed.
_OWED_TAIL = ("Carry one entry per line you owe — each line of your recipe's Done when, each "
              "open goal (`b<n>`) and each open outcome of the finish line: `d<n> met: "
              "<evidence>`, `d<n> unmet: <what remains>`, `d<n> not due: <how established>`, "
              "`b<n> met: <evidence>`, `g<n> distance: <what remains>` — then finish again.")


def deferral(found: dict[str, list[str]]) -> str:
    """The one observation a finish is set aside with when its accounting does not answer.

    Two heads, because the two cases need opposite moves (F805): a field that ARRIVED and falls
    short is about its CONTENT, and the run fixes the entries; an ABSENT field was eaten by the
    transport, so re-sending fails the same way and the head names the move that works — a
    shorter summary.
    """
    if found.get("absent"):
        return ("OBSERVATION (finish deferred): no `accounting` field arrived with this "
                "finish — it was ABSENT, so this is not a verdict on what your entries say: "
                + gaps(found) + ". If you did write them, they were lost on the way "
                "here (a parameter whose closing tag is mis-read swallows the fields written "
                "after it), so re-sending the same payload will fail the same way: finish "
                "again with a SHORTER summary and `accounting` as a plain list of strings. "
                + _OWED_TAIL)
    return ("OBSERVATION (finish deferred): your `accounting` is incomplete: "
            + gaps(found) + ". " + _OWED_TAIL)


def unmet_residuals(accounting: object) -> list[str]:
    """The entries a later run inherits: what an earlier finish left unmet, as its own words."""
    return [f"{i} — {note}" for i, (v, note) in parse(accounting).items()
            if v == "unmet" and note]
