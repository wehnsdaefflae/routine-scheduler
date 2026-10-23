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

What goes after the verdict is the ACCOUNT, and it has a content floor (`is_placeholder`): a
clause of fewer than two words, or one made only of words a real account never consists of
alone (`test`, `tbd`, `n/a`, `ok`, …), is refused with the entry named. The floor is about
FORM, not truth — whether a `met` is actually supported is the claim verifier's question
(`engine/verifier.py`), asked of the run's own transcript.

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


def is_placeholder(evidence: str) -> bool:
    """True when an evidence clause answers for NOTHING — the content floor, two tests deep.

    MEASURED over every routine's transcripts for the 14 days to 2026-10-23: 286 finishes
    carried 2 093 entries, and the clause after the verdict ran p0.5 = 4 chars, p1 = 9, p2 = 50,
    median 240. Every entry under 9 characters was one run's `test` (`birthday-admin:
    20261022-080004`, F748, which finished `ok` on `d1 met: test`). But the shortest LEGITIMATE
    entries are only a hair longer — `no filing` (9), `no letter due`, `no letter to archive`
    — so a character floor anywhere near the placeholders refuses real `not due` accounts. The
    floor is therefore WORDS, not characters: fewer than two says nothing, and a clause made
    only of `PLACEHOLDER_WORDS` (and punctuation) says nothing however many words it has.

    Deliberately NOT a judgement about whether the evidence is TRUE: that is the claim
    verifier's job (`engine/verifier.py`), and it reads the run's own transcript to do it.
    """
    words = [w for w in (t.strip(_EDGE) for t in evidence.split()) if w]
    return len(words) < 2 or all(w.lower() in PLACEHOLDER_WORDS for w in words)


def owed(done: list[dict], outcomes: list[dict]) -> list[str]:
    """The ids this finish must account for."""
    return [d["id"] for d in done] + [o["id"] for o in outcomes]


def problems(verdicts: dict[str, tuple[str, str]], done: list[dict],
             outcomes: list[dict], *, final: bool = False) -> dict[str, list[str]]:
    """What is wrong with an accounting: `missing` ids, `bare` entries whose verdict needs a note
    and has none, `placeholder` entries whose note answers for nothing (`is_placeholder` —
    named as whole ENTRIES, not ids, because the refusal has to show the model what it wrote),
    and `refused` entries that claim what their line cannot be (a `met` on an outcome only the
    operator judges, a distance on a Done-when line, an unmet goal on a reply declared
    `final`). Empty lists when sound.

    `bare` and `placeholder` are exclusive — the empty clause and the hollow one are one
    finding each, so the deferral says one thing about an entry. A placeholder entry is
    rendered back from `parse`, which lowercases id and verdict: the clause is the model's own
    text, the lead-in is normalised.
    """
    judges = {o["id"]: o["judge"] for o in outcomes}
    ids = set(owed(done, outcomes))
    missing = [i for i in owed(done, outcomes) if i not in verdicts]
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
    return {"missing": missing, "bare": bare, "placeholder": placeholder, "refused": refused}


def gaps(found: dict[str, list[str]]) -> str:
    """What `problems` found, as one clause list — shared by the finish's deferral and a task
    checkpoint's refusal (engine/jobops.py), which account for Done-when lines alike.
    """
    parts = []
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
