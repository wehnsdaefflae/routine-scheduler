"""A release that declares its own remainder must ADDRESS it to someone — the docs/limits.md gate.

Around 0.444.0 (2026-10-23) release entries began declaring their own incompleteness explicitly,
which is better practice than the silence before it. But a declaration is not a route. Of the
nine remainders declared between 0.444.0 and 0.445.2, the ONE given a report id — R2844,
`_OPENING_STOP` splitting on ASCII sentence stops only — was fixed in 0.447.2 the next day; two
more named a route (a `docs/designs.md` entry, the question id `q-20260912-121159-40`); six
carried no route at all, and two of those six went wrong SILENTLY: a credential watch covering
one provider of three looks like a credential watch, and a command "never run against a live
instance" is the one artefact whose whole purpose is to be run against a live instance. A search
for `Known limit` across the tree found the changelog and nothing else: no doc, no test, no
module comment. The mechanism that turns a known limit into a fix is being ADDRESSED TO SOMEONE,
and the changelog addresses nobody — it is a record, not an inbox, by design and rightly.

So a limit clause ships with ONE of three dispositions, chosen on the spot:

1. **a report** — `reported as R2844`: a defect someone owns (the path that worked in 24 hours);
2. **a decision or a question** — `raised as D219`, `Still open: q-20260912-121159-40`, or a
   `docs/designs.md` entry when the work is already decided (that file has its own reader and its
   own test): a judgement call does not belong in a defect queue;
3. **an entry in `docs/limits.md`** — known, undecided and unbuilt, with the evidence and one
   line on what would make it matter; or `Accepted permanently: <reason>` written into the clause
   itself, when the honest answer is that nobody will ever build it.

Prose alone is the failure this repo has measured twice: `docs/designs.md` had no reader for 33
days, and the ≤350-line bar "was advice, and the tree drifted" until `tests/test_file_size.py`.
Hence a check rather than a sentence.

Two things are deliberately NOT policed, and both are stated rather than silent. A clause quoted
as a `code token` is prose ABOUT this convention, not a declaration — R2596's lesson, where a
tombstone comment quoting its own marker reddened `main` itself. And a remainder declared in
words this file does not know is not caught: the vocabulary is closed, it is written out in
`docs/limits.md`, and using it is how you declare a limit.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parents[1]
CHANGELOG_PATH = ROOT / "CHANGELOG.md"
LIMITS_PATH = ROOT / "docs" / "limits.md"
DESIGNS_DOC = "docs/designs.md"

#: The release header, and the join key between a clause and its entry. Line numbers move — the
#: changelog grows at its head — so an entry cites the RELEASE that declared the limit.
_RELEASE = re.compile(r"^## \[(\d+\.\d+\.\d+)\] — (\d{4}-\d{2}-\d{2})$", re.MULTILINE)

#: The declaration vocabulary. An OPENER must begin a clause (a line, or a finished sentence);
#: `is NOT built` is a phrase and needs no anchor. `Known gaps in this release` is a section
#: heading whose every bullet is one clause.
_OPENERS = ("Known limits", "Known limit", "Still open:", "Not fixed", "Not measured")
_INLINE = ("is NOT built",)
_MARKER = re.compile("|".join(re.escape(m) for m in (*_OPENERS, *_INLINE)))
_GAPS_SECTION = re.compile(r"^#{2,4} Known gaps\b[^\n]*$", re.MULTILINE)
_HEADING = re.compile(r"^#{1,4} ", re.MULTILINE)
_BULLET = re.compile(r"^- ", re.MULTILINE)
#: A clause runs to the end of its block: a blank line, the next bullet, or the next heading.
_BLOCK_END = re.compile(r"\n[ \t]*\n|\n- |\n#{1,4} ")

#: The ids a disposition can name: a report, a decision, a question.
_ID = r"(?:R\d{2,5}|D\d{2,5}|q-\d{8}-\d{6}-\d{1,3})"
#: An id is a route only when the clause says it was ADDRESSED somewhere — "reported as R2844",
#: "filed as R3028", "Still open: q-…". An id merely MENTIONED is the item the release was about,
#: which is how a check like this goes vacuous: nearly every entry names its own item.
_ROUTE = re.compile(r"(?:reported|filed|raised|asked|tracked|opened|routed|carried)"
                    r"(?:\s+(?:to|with|at)\s+\S+)?\s+as\s+[`*_]*(" + _ID + r")"
                    r"|Still open:[*_\s]*`?(" + _ID + r")")
_ACCEPTED = re.compile(r"Accepted permanently:\s*(.+)")
_VERSION = re.compile(r"\d+\.\d+\.\d+")

#: A permanent acceptance without a reason is a shrug with a marker on it; a `Clause:` fragment
#: too short to be unique joins an entry to whatever it happens to appear in.
MIN_REASON_CHARS = 25
MIN_FRAGMENT_CHARS = 25

#: What an entry in the store must carry (docs/limits.md states the same shape in prose).
REQUIRED_FIELDS = ("Not covered", "Evidence", "Declared", "Clause", "Would matter when",
                   "Disposition")
_FIELD = re.compile(r"^- \*\*([A-Za-z ]+):\*\*[ ]*(.*?)(?=\n- \*\*|\n\n|\Z)",
                    re.MULTILINE | re.DOTALL)
_ENTRY = re.compile(r"^## (.+)$", re.MULTILINE)

TEACH = (
    "A release entry declares a limit and routes it NOWHERE. Give each clause below one of the "
    "three dispositions, on the spot:\n"
    "  (a) a defect someone owns -> file a report and say so in the clause: "
    "'Not fixed, reported as R2844' (that one was fixed the next day);\n"
    "  (b) a judgement rather than a defect -> raise it as a decision or a question and say so "
    "('raised as D219', 'Still open: q-20260912-121159-40'), or write a docs/designs.md entry "
    "when the work is already decided;\n"
    "  (c) known, undecided and unbuilt -> add an entry to docs/limits.md (its header states the "
    "shape: what is not covered, the evidence, the release, what would make it matter, its "
    "disposition), or accept it for good IN the clause with "
    "'Accepted permanently: <reason>'.")


class Clause(NamedTuple):
    """One declared remainder: where it was declared, and the words that declare it."""

    release: str
    date: str
    line: int
    marker: str
    text: str


class Entry(NamedTuple):
    """One `docs/limits.md` entry, with the fields its contract requires."""

    heading: str
    line: int
    fields: dict[str, str]

    @property
    def declared(self) -> str:
        found = _VERSION.search(self.fields.get("Declared", ""))
        return found.group(0) if found else ""

    @property
    def fragment(self) -> str:
        return _normalised(self.fields.get("Clause", "")).strip("\"“”' ")


def _normalised(text: str) -> str:
    """One space everywhere — these entries wrap mid-sentence, so a fragment spans line breaks."""
    return " ".join(text.split())


def _opens_a_clause(before: str) -> bool:
    """True when `before` (the marker's line up to it) leaves the marker starting a clause."""
    head = before.rstrip(" \t*_-#>").rstrip()
    return head == "" or head.endswith((".", ";", ":", "—", "(", "!", "?"))


def _marker_hits(text: str) -> list[tuple[int, str]]:
    hits = []
    for found in _MARKER.finditer(text):
        line_start = text.rfind("\n", 0, found.start()) + 1
        before = text[line_start:found.start()]
        if before.count("`") % 2 == 1:
            continue                      # inside a code span: prose about the convention
        if found.group(0) in _OPENERS and not _opens_a_clause(before):
            continue
        hits.append((found.start(), found.group(0)))
    return hits


def _block_end(text: str, pos: int) -> int:
    found = _BLOCK_END.search(text, pos)
    return found.start() if found else len(text)


def _releases(text: str) -> list[tuple[int, str, str]]:
    return [(m.start(), m.group(1), m.group(2)) for m in _RELEASE.finditer(text)]


def _release_at(releases: list[tuple[int, str, str]], pos: int) -> tuple[str, str]:
    """The nearest release header ABOVE `pos` — the release that shipped the clause."""
    owner = ("unreleased", "")
    for start, version, date in releases:
        if start > pos:
            break
        owner = (version, date)
    return owner


def _gap_bullets(text: str) -> list[tuple[int, str]]:
    """Every bullet under a `Known gaps in this release` heading — each one is a clause."""
    found = []
    for section in _GAPS_SECTION.finditer(text):
        after = _HEADING.search(text, section.end())
        body_end = after.start() if after else len(text)
        found.extend((bullet.start(), "Known gaps")
                     for bullet in _BULLET.finditer(text, section.end(), body_end))
    return found


def clauses(changelog_text: str) -> list[Clause]:
    """Every limit clause a release declares, in file order."""
    releases = _releases(changelog_text)
    hits = sorted([*_marker_hits(changelog_text), *_gap_bullets(changelog_text)])
    found = []
    consumed = -1
    for pos, marker in hits:
        # A clause runs to the end of its BLOCK, and a marker INSIDE one is part of that clause
        # rather than a new one. Measured 2026-11-14: truncating at the next marker split
        # "Known limit: it covers one provider. Not fixed, reported as R9001: the other two."
        # into an unrouted half and a routed half — so a clause that states its own disposition
        # in the same breath read as addressed to nobody, which is the one thing this check
        # exists to detect and therefore the one place a false positive is fatal to it. The
        # disposition vocabulary deliberately overlaps the declaration vocabulary (`Not fixed`,
        # `Still open:` are both), so that collision is the common case, not an edge.
        if pos <= consumed:
            continue
        end = _block_end(changelog_text, pos)
        consumed = end
        version, date = _release_at(releases, pos)
        found.append(Clause(version, date, changelog_text.count("\n", 0, pos) + 1, marker,
                            _normalised(changelog_text[pos:end])))
    return found


def entries(limits_text: str) -> list[Entry]:
    heads = list(_ENTRY.finditer(limits_text))
    found = []
    for index, head in enumerate(heads):
        end = heads[index + 1].start() if index + 1 < len(heads) else len(limits_text)
        fields = {name.strip(): value for name, value in _FIELD.findall(limits_text[head.end():end])}
        found.append(Entry(head.group(1).strip(),
                           limits_text.count("\n", 0, head.start()) + 1, fields))
    return found


def disposition(clause_text: str) -> str:
    """What the clause itself says it did about the limit, or "" when it says nothing."""
    route = _ROUTE.search(clause_text)
    if route:
        return f"routed as {route.group(1) or route.group(2)}"
    if DESIGNS_DOC in clause_text:
        return f"decided work, carried in {DESIGNS_DOC}"
    accepted = _ACCEPTED.search(clause_text)
    if accepted and len(accepted.group(1).strip()) >= MIN_REASON_CHARS:
        return "accepted permanently, with a reason"
    return ""


def undisposed(changelog_text: str, limits_text: str) -> list[str]:
    """One sentence per limit clause that is addressed to nobody."""
    filed = entries(limits_text)
    problems = []
    for clause in clauses(changelog_text):
        if disposition(clause.text):
            continue
        if any(entry.fragment and entry.fragment in clause.text
               and entry.declared == clause.release for entry in filed):
            continue
        problems.append(f"CHANGELOG.md:{clause.line} ({clause.release}) '{clause.marker}' — "
                        f"{clause.text[:140]}")
    return problems


def entry_problems(limits_text: str, changelog_text: str) -> list[str]:
    """An entry that is incomplete, or that matches no clause any more."""
    known = {version for _s, version, _d in _releases(changelog_text)}
    by_release: dict[str, list[Clause]] = {}
    for clause in clauses(changelog_text):
        by_release.setdefault(clause.release, []).append(clause)
    problems = []
    for entry in entries(limits_text):
        missing = [field for field in REQUIRED_FIELDS if not entry.fields.get(field, "").strip()]
        if missing:
            problems.append(f"docs/limits.md:{entry.line} '{entry.heading}' — no {missing}")
            continue
        if entry.declared not in known:
            problems.append(f"docs/limits.md:{entry.line} '{entry.heading}' — Declared names "
                            f"{entry.declared or entry.fields['Declared']!r}, which is no release")
            continue
        if len(entry.fragment) < MIN_FRAGMENT_CHARS:
            problems.append(f"docs/limits.md:{entry.line} '{entry.heading}' — the Clause "
                            f"fragment is too short to identify anything: {entry.fragment!r}")
            continue
        if not any(entry.fragment in clause.text
                   for clause in by_release.get(entry.declared, [])):
            problems.append(f"docs/limits.md:{entry.line} '{entry.heading}' — no limit clause in "
                            f"{entry.declared} contains its Clause fragment {entry.fragment!r}. "
                            "The limit was reworded, shipped or deleted: graduate the entry to "
                            "docs/designs.md if it was decided, delete it if it stopped being "
                            "true, or re-point the fragment.")
    return problems


def _changelog() -> str:
    return CHANGELOG_PATH.read_text(encoding="utf-8")


def _limits() -> str:
    return LIMITS_PATH.read_text(encoding="utf-8") if LIMITS_PATH.is_file() else ""


