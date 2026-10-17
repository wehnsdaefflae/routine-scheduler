"""A one-shot migration's own deadline: who may declare one, and who is told before it bites.

The delete-after-convergence policy (CLAUDE.md) asks every one-shot migration to carry a
`MIGRATION(expires=YYYY-MM-DD)` marker beside its code, and the suite FAILS once that date
passes — so a "temporary" migration can never quietly become permanent. This module is the
GRAMMAR and the one READER behind that policy, because three readers have to agree about what is
declared: the hard check and the 21-day warning (`tests/test_policy.py`), and the daemon watch
that files the warning where a person actually looks (`daemon/marker_watch`).

**Narration is not declaration** (R2596, the operator's ruling of 2026-10-16). 0.423.1 deleted
the `trigger_*` appliers and left a tombstone comment that SPELLED the marker it was describing —
"their MIGRATION(expires=…) came due" — and on the morning after that date the check read the
sentence as a live declaration. `main` itself went red and every release on the host was blocked
by a comment about a migration that no longer existed.

A declaration is INTRODUCED, never referred to: the marker opens its own sentence. So what
precedes it on its line may be comment or docstring punctuation (`#`, `\"\"\"`, an em dash, an
opening bracket) or nothing at all, and a marker that follows a WORD is prose about one. One
rule, no second spelling to remember, and deliberately conservative in the safe direction: a
sentence that happens to introduce a marker mid-prose still reads as a declaration and reds the
gate — visible, and one edit away — while the opposite error is the silent permanence the policy
exists to prevent.

It cannot excuse a real migration either, because the second half of the check mirrors the
first: a file holding migration-shaped code with no DECLARED marker is flagged for missing one.
A migration whose marker is written as narration is therefore still reported.

**Compatibility shims are policed too** (F699). `jobs_compat.py` carried a deprecation with no
expiry, and the check could not have noticed: its code is not migrate-named, so nothing in
`SHAPED` matches it. The policed shape is the one a back-compat layer actually has here — an
ALIAS TABLE, module constants that keep an old spelling readable (`LEGACY_FILE`,
`SETTING_ALIAS`) — and not the filename, which is a worse signal than it looks: measured over
`src/`, the only two `*_compat.py` files are this deprecation and `endpoints/openai_compat.py`,
a protocol adapter that deprecates nothing and would be flagged forever.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import NamedTuple

#: The marker as it is WRITTEN. Two things decide whether an occurrence counts: this text, and
#: where on its line it sits (`declares`).
MARKER = re.compile(r"MIGRATION\(expires=(\d{4}-\d{2}-\d{2})\)")

#: What makes an occurrence NARRATION: a word, then nothing but blanks, then the marker.
_PROSE_LEAD = re.compile(r"[0-9A-Za-z][ \t]*$")

#: Code that LOOKS like a one-shot migration: a migrate-named def/class, a `migrate-` CLI
#: command, or a migrate-named shell function in a deploy script.
SHAPED = re.compile(r"def \w*migrat\w*|class \w*[Mm]igrat\w*|[\"']migrate-"
                    r"|^\s*\w*migrat\w*\s*\(\)\s*\{", re.MULTILINE)

#: The NAME an alias constant carries in this repo: `LEGACY_FILE`, `SETTING_ALIAS`.
ALIAS_NAME = re.compile(r"\b(?:LEGACY_[A-Z0-9_]+|[A-Z0-9_]+_ALIAS)\b")

#: A module-level constant assigned a STRING — an alias table holds the old spelling itself, so
#: the value's shape is part of the rule: this module's own `ALIAS_NAME = re.compile(…)` carries
#: an alias-shaped name and is a tool, not a deprecation, and policing names alone flags it.
_CONST_STR = re.compile(r"^(?P<targets>[A-Z0-9_][A-Z0-9_, \t]*?)=[ \t]*[\"']", re.MULTILINE)

#: How much notice a declaration gives before its hard expiry (F628). The hard fail is unchanged
#: and still the enforcement; this is LEAD TIME. The signal used to be binary and arrived the
#: morning it was already too late, on the gate every release must pass — and for whoever
#: happened to ship that day rather than whoever wrote the migration.
WARN_DAYS = 21


class Declared(NamedTuple):
    """One declared expiry: the file that carries it and the date it is overdue after."""

    path: Path
    expires: str

    def days_left(self, today: date) -> int | None:
        """Days until the deadline — None when the date is malformed (the hard check's business,
        so a typo can never be read here as "not due").
        """
        try:
            return (date.fromisoformat(self.expires) - today).days
        except ValueError:
            return None


def declares(text: str, at: int) -> bool:
    """True when the marker found at offset `at` DECLARES an expiry rather than narrating one.

    The whole grammar, in one place: see the module note for why this is the rule and why it
    errs toward reading an occurrence as a declaration.
    """
    line_start = text.rfind("\n", 0, at) + 1
    return not _PROSE_LEAD.search(text[line_start:at])


def has_alias_table(text: str) -> bool:
    """True when the module keeps a pre-rename SPELLING alive in a constant — the shape a
    back-compat layer has here, and the one `SHAPED` cannot see (F699).
    """
    return any(ALIAS_NAME.search(m.group("targets")) for m in _CONST_STR.finditer(text))


def policed_paths(repo: Path | None = None, package: Path | None = None) -> list[Path]:
    """Every file the policy reads: the package's Python, and the deploy scripts, which migrate
    the state they keep (`backup.sh`'s snapshot layout).

    `tests/` is deliberately NOT read. A test's prose about a migration is not a declaration —
    which the grammar above now says on its own merits — and widening the scan would only
    double-count the deadlines the `src/` modules already declare.
    """
    pkg = Path(package) if package else Path(__file__).resolve().parent
    root = Path(repo) if repo else pkg.parent.parent
    return [*sorted(pkg.rglob("*.py")), *sorted((root / "deploy").glob("*.sh"))]


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:                 # a vanished or unreadable file is not a policy breach
        return ""


def declarations(paths: list[Path] | None = None) -> list[Declared]:
    """Every DECLARED expiry in the policed tree. One reader for the hard check, the warning and
    the daemon watch, so the three can never disagree about what is declared.
    """
    found: list[Declared] = []
    for path in paths if paths is not None else policed_paths():
        text = _read(path)
        found.extend(Declared(path, m.group(1)) for m in MARKER.finditer(text)
                     if declares(text, m.start()))
    return found


def undeclared(paths: list[Path] | None = None) -> list[Path]:
    """Files that must declare an expiry and do not — migration-shaped code (`SHAPED`) and the
    back-compat alias tables that code shape cannot see (`has_alias_table`).
    """
    missing: list[Path] = []
    for path in paths if paths is not None else policed_paths():
        text = _read(path)
        if not text:
            continue
        if not (SHAPED.search(text) or has_alias_table(text)):
            continue
        if not any(declares(text, m.start()) for m in MARKER.finditer(text)):
            missing.append(path)
    return missing


def expired(today: date, paths: list[Path] | None = None) -> list[Declared]:
    """Declarations whose date has passed: the migration has converged and its code is overdue
    for deletion. A malformed date is not expired — it is the hard check's own problem.
    """
    return [d for d in declarations(paths)
            if (left := d.days_left(today)) is not None and left < 0]


def due_soon(today: date, within: int = WARN_DAYS,
             paths: list[Path] | None = None) -> list[tuple[int, Declared]]:
    """Declarations inside the warning window, soonest first, as (days left, declaration)."""
    soon = [(left, d) for d in declarations(paths)
            if (left := d.days_left(today)) is not None and 0 <= left <= within]
    return sorted(soon, key=lambda pair: (pair[0], str(pair[1].path)))
