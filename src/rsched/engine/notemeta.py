"""A note's EXPIRY and PROVENANCE as data — `memory_write`'s `stale_after` and `verified` (D268).

Notes wrote their own freshness into prose ("verified live 2026-07-30", in a note's body), where
nothing could read it: a run met that sentence only if it opened the note, and the one memory
surface EVERY run reads — the `.memory/` index in the state digest — said nothing about whether
what it listed was still true. These two optional fields (from the Open Knowledge Format) make it
data the engine renders and judges.

WHERE THE DATA LIVES. The engine owns `.memory/INDEX.md` — one `- <name>.md: <about>` line per
note, written by `memops._memory_index_upsert` — and that line is also the store for these two
fields, appended as ` · `-separated markers the same upsert writes and the readers below parse
back. One line per note stays the invariant every upsert relies on, and a second store cannot
drift from the first because there is no second store.

WHY THE INDEX LINE SAYS "stale after" AND THE DIGEST SAYS "stale since". The line is written ONCE,
at write time; "this note is stale" only becomes true on a later date. Writing the verdict into
the file would record a claim that is false on the day it is made, so the file carries the DATE and
the composer — which runs on the day it matters — renders the verdict under its own digest heading.

A VALUE THE ENGINE CANNOT RENDER IS IGNORED, NOT REFUSED (`from_action`). These are optional
metadata on a write whose real payload is the note; failing the turn over a malformed date would
cost the note itself. The observation names what was recorded instead, so a run that sent
`stale_after: "soon"` learns that nothing was — the one thing a silently dropped field never does.

Schema + rendering + parsing only: this module reads no file and decides nothing about a run.
`actions.KIND_FIELDS` declares the fields on the kind (undeclared, they are stripped before the
handler ever sees them), `memops` writes them, `composer` reads them back.
"""

from __future__ import annotations

import datetime as dt
import re

from .finishline import today

#: The two optional fields this module owns, in the order they render on the INDEX line.
FIELDS = ("stale_after", "verified")

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
#: `verified.by`: the run itself, or a person — `human:<who>`, whose `<who>` is the verifier.
_BY_RE = re.compile(r"^(?:run|human:[a-z][a-z0-9._-]{0,31})$")
_STALE_RE = re.compile(r" · stale after (\d{4}-\d{2}-\d{2})(?= · |$)")
_VERIFIED_RE = re.compile(r" · ([a-z][a-z0-9._-]*)-verified (\d{4}-\d{2}-\d{2})(?= · |$)")
#: One rendered marker at the END of an INDEX line — what `about_of` strips back off.
_MARK_RE = re.compile(r" · (?:stale after \d{4}-\d{2}-\d{2}"
                      r"|[a-z][a-z0-9._-]*-verified \d{4}-\d{2}-\d{2})$")

#: `memory_write`'s own field specs, held here with the rendering that reads them rather than in
#: `actionschema.py`, which is over the repo's size bar and may only shrink.
ABOUT_FIELD = {
    "type": "string",
    "description": "memory_write: one-line INDEX entry — what this note holds + when to consult "
                   "it (the engine maintains .memory/INDEX.md from it)",
}
DELETE_FIELD = {
    "type": "boolean",
    "description": "memory_write: remove the note and its INDEX line (content/about not needed)",
}
STALE_AFTER_FIELD = {
    "type": "string",
    "description": "memory_write: OPTIONAL — the date (YYYY-MM-DD) after which this note should "
                   "not be trusted unchecked (a price, a roster, a page's shape). Past it the "
                   "digest lists it under its own heading instead of among the notes to consult",
}
VERIFIED_FIELD = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "by": {"type": "string",
               "description": "`run` (this run checked it) or `human:<who>` — "
                              "`human:operator` for the user's own confirmation"},
        "at": {"type": "string", "description": "the date of that check (YYYY-MM-DD)"},
    },
    "description": "memory_write: OPTIONAL — who last checked this note against reality and "
                   "when, rendered in its INDEX line ('operator-verified 2026-10-02')",
}


def _date(raw: object) -> str:
    """The YYYY-MM-DD date `raw` names, or "" — the shape `finishline` accepts, unchanged."""
    text = str(raw or "")
    if not _DATE_RE.match(text):
        return ""
    try:
        dt.date.fromisoformat(text)
    except ValueError:
        return ""
    return text


def from_action(action: dict) -> dict:
    """The renderable metadata a `memory_write` action carries — {} when it carries none.

    What cannot be rendered is left out (see the module docstring): the note still lands and
    the observation reports what was recorded.
    """
    out: dict = {}
    if date := _date(action.get("stale_after")):
        out["stale_after"] = date
    verified = action.get("verified")
    if isinstance(verified, dict):
        by, at = str(verified.get("by") or ""), _date(verified.get("at"))
        if at and _BY_RE.match(by):
            out["verified"] = {"by": by, "at": at}
    return out


def verified_mark(verified: dict) -> str:
    """`operator-verified 2026-10-02` — who checked the note, and when."""
    by = str(verified["by"])
    who = by if by == "run" else by.split(":", 1)[1]
    return f"{who}-verified {verified['at']}"


def index_suffix(meta: dict | None) -> str:
    """The markers appended to a note's INDEX line — "" for a note carrying no metadata.

    Empty is the case that matters most: it is every note on the instance today, and it is why
    an index without these fields renders byte for byte as it did before they existed.
    """
    marks = []
    if stale := (meta or {}).get("stale_after"):
        marks.append(f"stale after {stale}")
    if verified := (meta or {}).get("verified"):
        marks.append(verified_mark(verified))
    return "".join(f" · {mark}" for mark in marks)


def about_of(line: str) -> str:
    """The `about` an INDEX line states, with any rendered markers stripped back off.

    What a revision that does not restate the subject keeps: `verified` says the note was
    checked, never that it is about something else.
    """
    body = line.split(":", 1)[1].strip() if ":" in line else ""
    while (shorter := _MARK_RE.sub("", body)) != body:
        body = shorter
    return body


def meta_of(line: str) -> dict:
    """The metadata an INDEX line already carries, read back off its rendered markers.

    This is what makes the fields MERGE rather than replace: a run revising a note's content
    does not have to restate an expiry it may not even know about, and a write that states
    only `verified` leaves the `stale_after` standing. Without it, every ordinary revision
    silently dropped whatever an earlier run had recorded.
    """
    out: dict = {}
    if found := _STALE_RE.search(line):
        out["stale_after"] = found.group(1)
    if found := _VERIFIED_RE.search(line):
        who = found.group(1)
        out["verified"] = {"by": "run" if who == "run" else f"human:{who}",
                           "at": found.group(2)}
    return out


def partition_stale(lines: list[str],
                    on: dt.date | None = None) -> tuple[list[str], list[str]]:
    """The INDEX lines still inside their `stale_after`, and those past it — order preserved.

    A note without the field is never stale, so an index carrying no metadata partitions to
    (every line, []) and the digest's memory block is unchanged.
    """
    on = on or today()
    fresh: list[str] = []
    stale: list[str] = []
    for line in lines:
        date = meta_of(line).get("stale_after", "")
        past = bool(date) and dt.date.fromisoformat(date) < on
        (stale if past else fresh).append(line)
    return fresh, stale


#: The digest heading the stale notes are listed under — the verdict, where it is true.
STALE_HEADING = (".memory/ notes PAST THEIR stale_after (the run that wrote each named the date "
                 "it stops being trustworthy — re-check it against the live thing and revise "
                 "the note, or delete it; do not rely on it as it stands):")


def stale_digest(stale: list[str]) -> str:
    """The digest block for notes past their date, one line each, "stale after" read as since."""
    lines = "\n".join(_STALE_RE.sub(lambda m: f" · stale since {m.group(1)}", line)
                      for line in stale)
    return f"{STALE_HEADING}\n{lines}"


#: How many INDEX lines the digest shows before pointing at the file (composer's own cap).
INDEX_SHOWN_MAX = 60


def digest_blocks(index_text: str) -> list[str]:
    """The digest's `.memory/` parts for an index: the notes to consult, then the stale ones.

    The whole block lives here rather than in `composer.state_digest` because that function is
    at its complexity ratchet (C901 22): partitioning inline put it at 24 and reds the quality
    gate. One function per part of the prompt is the composer's own shape anyway.
    """
    fresh, stale = partition_stale(index_text.strip().splitlines())
    blocks = []
    if fresh:
        more = (f"\n[... read .memory/INDEX.md for the full {len(fresh)} lines]"
                if len(fresh) > INDEX_SHOWN_MAX else "")
        blocks.append(".memory/ index (notes from earlier work — memory_read the relevant "
                      "topic before re-discovering anything):\n"
                      + "\n".join(fresh[:INDEX_SHOWN_MAX]) + more)
    if stale:
        blocks.append(stale_digest(stale))
    return blocks
