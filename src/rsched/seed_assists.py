"""Carry the seed rules' `assists:` DECLARATIONS into the live library — add-only, per entry.

`bootstrap.sync_seed_library_docs` is add-only at the FILE level: it installs a rule the live
library lacks and never touches one it already has, because these files are user-editable and an
overwrite would discard an operator's edit. That is right for prose and wrong for a DECLARATION
the engine reads — a rule's `assists:` block is not prose, it is the list of candidates a hook
point evaluates, and a point with no candidates is indistinguishable from a dead one.

What that cost, measured 2026-10-30: `evidence-discipline` had declared assists in
`library-seed/` since 0.471.0 and the live copy declared none, so the hook points steps 6-8
wired (`child-exit`, `spawn`, `run-end`) fired against ZERO candidates on this instance for
three releases. A green gate cannot see it: `tests/test_assists.py` reads the seed directory,
never the live library.

## Why this replaces the one-shot migration that fixed that

0.477.0 shipped `migrate_rule_assists.py`, which inserted the seed's whole block once and
refused a rule whose live copy already declared one. Both properties were right for the gap it
closed and both make it unable to close the NEXT one: it has run on this instance (its record
is on disk), and the live rule now declares assists — so a declaration added by a later release
could never reach the live library again, and the next hook point would ship dead exactly as
those three did. A per-release one-shot for each new entry is the alternative, and it is the
shape that produced three migrations of the same kind already.

So this is a RECURRING sync at the granularity the problem actually has: the ENTRY. It carries
a declaration the live rule is missing and never edits one it has.

## What it refuses to do

* **Overwrite an entry.** An id the live rule already carries is left exactly as it is: that
  text is the operator's, and his declaration outranks the seed's (the rule `migrate_rule_assists`
  held at the file level, kept here at the entry level).
* **Resurrect a removed entry.** An id that this rule's git history once carried and no longer
  does was REMOVED by somebody, and add-only mechanisms that cannot see a deletion is how a
  deleted doc comes back at every restart (`libgit.path_was_deleted`, the same rule for files).
* **Create a rule.** A rule missing from the live library stays missing — the operator may have
  deleted it, and the file sync already owns that question.
* **Touch the prose.** It edits the frontmatter's `assists:` block and nothing else, inserting
  the seed's own LINES rather than re-dumping parsed YAML, so the diff stays readable to whoever
  reviews it.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from . import libgit
from .paths import atomic_write, repo_root

log = logging.getLogger("rsched.seed_assists")


def _frontmatter(text: str) -> dict | None:
    """The rule's YAML frontmatter, or None when it has none the linter would accept."""
    if not text.startswith("---"):
        return None
    try:
        meta = yaml.safe_load(text.split("---")[1])
    except yaml.YAMLError:
        return None
    return meta if isinstance(meta, dict) else None


def _ids(meta: dict) -> list[str]:
    """The declaration ids a parsed frontmatter carries, in order."""
    block = meta.get("assists")
    if not isinstance(block, list):
        return []
    return [str(item.get("id") or "") for item in block if isinstance(item, dict)]


def _entry_lines(seed_text: str, wanted: set[str]) -> str:
    """The seed's own LINES for the wanted entry ids — the text, not a re-dump of the YAML.

    An entry starts at its `  - ` item line and runs to the next one (or to the end of the
    block), so a folded `line: >-` scalar comes across exactly as the seed wrote it.
    """
    lines = seed_text.splitlines(keepends=True)
    try:
        start = next(i for i, line in enumerate(lines) if line.startswith("assists:"))
    except StopIteration:
        return ""
    out: list[str] = []
    keep = False
    for line in lines[start + 1:]:
        if line[:1] not in (" ", "-", "\t"):
            break                                   # the block ended (`tags:` or the `---`)
        if line.startswith("  - "):
            keep = any(f"id: {name}" in line for name in wanted)
        if keep:
            out.append(line)
    return "".join(out)


def _block_end(text: str) -> int | None:
    """The character offset just past the whole `assists:` block, or None when there is none."""
    at = text.find("\nassists:")
    if at < 0:
        return None
    cursor = text.index("\n", at + 1) + 1
    for line in text[cursor:].splitlines(keepends=True):
        if line[:1] not in (" ", "-", "\t"):
            break
        cursor += len(line)
    return cursor


def _inserted(live_text: str, entries: str) -> str | None:
    """The live rule with `entries` added to its `assists:` block, or None when it has no place
    to put them.

    A rule that already declares assists gains the entries at the END of its block, after the
    operator's own. A rule with no block at all gains one before `tags:`, where every seed rule
    carries it — the frontmatter is a mapping, so the position is cosmetic, and matching the
    seed's layout keeps the two readable side by side.
    """
    end = _block_end(live_text)
    if end is not None:
        return live_text[:end] + entries + live_text[end:]
    marker = "\ntags:"
    if marker not in live_text:
        return None
    head, rest = live_text.split(marker, 1)
    return f"{head}\nassists:\n{entries.rstrip()}{marker}{rest}"


def _removed_ids(library: Path, rel: str, ids: set[str]) -> set[str]:
    """The ids this rule's history carried and no longer does — somebody removed them.

    `git log -S<id>` asks whether the string's occurrence count ever CHANGED in this file, which
    for a declaration id means it was added or removed at some commit. Combined with "it is not
    there now", that is a removal. Fails open to the empty set (no repo, a git error): the cost
    of missing a removal is one entry the operator deletes again, while refusing to carry
    anything on a git hiccup would leave a point permanently dead.
    """
    out: set[str] = set()
    for name in ids:
        try:
            r = libgit.git(library, "log", "--format=%h", "-S", f"id: {name}", "--", rel)
        except OSError:
            continue
        if r.returncode == 0 and r.stdout.strip():
            out.add(name)
    return out


def _carry(live: Path, seed: Path, library: Path, rel: str) -> tuple[list[str], str]:
    """Insert the entries this live rule is missing. Returns (carried ids, why-not)."""
    try:
        live_text, seed_text = live.read_text(encoding="utf-8"), seed.read_text(encoding="utf-8")
    except OSError as exc:
        return [], f"not read: {exc}"
    meta, seed_meta = _frontmatter(live_text), _frontmatter(seed_text)
    if meta is None or seed_meta is None:
        return [], "frontmatter is not readable"
    seed_ids = _ids(seed_meta)
    if not seed_ids:
        return [], "the seed declares none"
    missing = [name for name in seed_ids if name and name not in set(_ids(meta))]
    if not missing:
        return [], ""
    removed = _removed_ids(library, rel, set(missing))
    missing = [name for name in missing if name not in removed]
    if not missing:
        return [], "every missing declaration was removed from this rule deliberately"
    entries = _entry_lines(seed_text, set(missing))
    if not entries:
        return [], "the seed's entries could not be located"
    updated = _inserted(live_text, entries)
    if updated is None:
        return [], "the live rule has neither an `assists:` block nor a `tags:` line"
    atomic_write(live, updated)
    return missing, ""


def sync_seed_rule_assists(libraries_home: Path, *, routines_home: Path) -> list[str]:
    """Carry every missing seed declaration into the live rules. Returns the rels it changed.

    Runs at every daemon boot, after `sync_seed_library_docs` installs whatever rule FILES are
    missing — so a rule that arrived in this boot already carries its declarations and this
    finds nothing to do for it.
    """
    library, seed_root = Path(libraries_home), repo_root() / "library-seed" / "rules"
    if not (library / "rules").is_dir() or not seed_root.is_dir():
        return []
    changed: list[str] = []
    for seed in sorted(seed_root.glob("*.md")):
        rel = f"rules/{seed.name}"
        live = library / rel
        if not live.exists():
            continue            # deleted rules stay deleted; the file sync owns that question
        carried, why = _carry(live, seed, library, rel)
        if carried:
            changed.append(rel)
            log.warning("seed assists: carried %s into %s", ", ".join(carried), rel)
        elif why:
            log.info("seed assists: %s left alone — %s", rel, why)
    if changed:
        landed = libgit.commit(library, "rules: carry the seed `assists:` declarations the "
                                        "add-only file sync cannot (a hook point with no "
                                        "candidates is indistinguishable from a dead one)",
                               routines_home=routines_home, paths=changed)
        if landed.failed:       # changed on disk, not in the library's history: a person looks
            log.warning("seed assists: commit did not land: %s", landed.describe())
    return changed
