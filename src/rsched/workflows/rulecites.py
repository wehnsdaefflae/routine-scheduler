"""Rule CITATIONS that outlived their rule — a routine's prose naming a rule the library retired.

A recipe or a memory note that names a rule makes a claim about the shared library that the
routine cannot check: nothing tells a holder that a rule it cites was retired. Its runs find
out by spending a turn on a failed `read_rule` and improvising a substitute. The library's
2026-09-30 consolidation deleted 24 rules in one pass, and nine days later 40 citations in 26
files across 13 routines still named them (routine-improver's sweep-58, R2349/R2366) — found
only because two routines reported their own instance.

So `rsched validate` reports them. Only the LIBRARY knows what it retired, so the check reads
the library repo's history (`retired_rules`) — a rule deleted there and not carried now — and
never guesses from shape: an arbitrary backticked kebab word near the word "rule" is far more
often a util or a file than a rule. Two forms are read, learnt from the sweep:

- an explicit `read_rule` naming a slug the library does not carry — whether or not it ever
  did, since the action itself says it is a rule;
- a RETIRED slug in backticks or bold, with a citation cue near it: a rule word within
  `CUE_WINDOW` characters either side, or a reading verb in the `READ_WINDOW` just before it
  ("Read `ai-writing-tells` and … before composing" names no rule word at all, and the short
  window keeps "the `error-recovery` helper script" from reading as one).

Every row is a REPORT, like the recipe notes beside it: a citation is cheap to read and
sometimes deliberate (a note recording that a rule is gone), so it never turns the command red.
Memory notes are scanned too, because that is where most citations were — but they are the
routine's own store, so the row says whose move it is.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from .. import libgit, library_docs
from ..readmodels.statemap import STAGES_DIR

CITE_CUE = re.compile(r"\brules?\b|\bRegel\b|read_rule|binds this routine|standing practice",
                      re.IGNORECASE)
CUE_WINDOW = 240
READ_CUE = re.compile(r"\b(?:read|lies|consult|befolge|siehe)\b", re.IGNORECASE)
READ_WINDOW = 60
_EXPLICIT = re.compile(r"read_rule\W{1,8}(?:name\s*[=:]\s*)?[`'\"]?([a-z0-9]+(?:-[a-z0-9]+)+)")


def retired_rules(libraries_home: Path) -> set[str]:
    """Rule slugs the library repo's history deleted and its tree does not carry now — empty
    when the history cannot be read, so a missing repo reports nothing rather than everything.
    """
    try:
        out = libgit.git(libraries_home, "log", "--diff-filter=D", "--name-only", "--format=",
                         "--", "rules/")
    except (OSError, subprocess.SubprocessError):
        return set()
    if out.returncode != 0:
        return set()
    deleted = {Path(line.strip()).stem for line in out.stdout.splitlines()
               if line.strip().startswith("rules/") and line.strip().endswith(".md")}
    return deleted - set(library_docs.slugs(libraries_home / "rules"))


def citations(text: str, retired: set[str], live: set[str]) -> list[tuple[int, str]]:
    """`(line, slug)` per citation of a rule the library does not carry, in line order."""
    hits: set[tuple[int, str]] = set()
    for m in _EXPLICIT.finditer(text):
        if m.group(1) not in live:
            hits.add((text.count("\n", 0, m.start()) + 1, m.group(1)))
    for slug in retired:
        for m in re.finditer(rf"`{re.escape(slug)}`|\*\*{re.escape(slug)}\*\*", text):
            around = text[max(0, m.start() - CUE_WINDOW):m.end() + CUE_WINDOW]
            before = text[max(0, m.start() - READ_WINDOW):m.start()]
            if CITE_CUE.search(around) or READ_CUE.search(before):
                hits.add((text.count("\n", 0, m.start()) + 1, slug))
    return sorted(hits)


def citation_notes(routine_dir: Path, retired: set[str], live: set[str]) -> list[str]:
    """One row per FILE of the recipe (`main.md`, `stages/`) and the memory notes (`.memory/`)
    that cites a rule the library does not carry, naming each slug with its lines — empty for a
    routine whose prose names only live rules. Per file and not per citation: a note ABOUT a
    retirement names a dozen gone rules, and a dozen rows would bury the one recipe line that
    sends a run to a rule that is not there.
    """
    paths = [routine_dir / "main.md", *sorted((routine_dir / STAGES_DIR).glob("*.md")),
             *sorted((routine_dir / ".memory").glob("*.md"))]
    notes: list[str] = []
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        found: dict[str, list[int]] = {}
        for line, slug in citations(text, retired, live):
            found.setdefault(slug, []).append(line)
        if not found:
            continue
        rel = path.relative_to(routine_dir).as_posix()
        named = ", ".join(f"`{slug}` (line {', '.join(map(str, lines))})"
                          for slug, lines in found.items())
        whose = (" (a memory note — the routine's own store: its run or you repoint it)"
                 if rel.startswith(".memory/") else "")
        notes.append(f"{rel}: cites {named}, which the library no longer carries — point "
                     f"each at whatever now holds what it said, or drop it{whose}")
    return notes
