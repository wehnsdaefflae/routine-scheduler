"""MIGRATION(expires=2026-12-01): carry the first `assists:` blocks into the LIVE rules.

The seed sync (`bootstrap.sync_seed_library_docs`) is ADD-ONLY by design — it installs a rule
the live library is missing and never overwrites one, so a local edit always wins. All 26
rules already exist live, so declaring an assist in `library-seed/rules/*.md` reaches exactly
zero instances on its own. This is the one-shot that closes that gap, in the shape
`migrate_stopping_scope` / `migrate_shell_action` established.

Idempotent and conservative in both directions: a rule that already carries an `assists:` key
is left alone (an operator's own edit outranks the seed, same as the sync's rule), and a rule
whose live body has DRIFTED from the seed is left alone too — this copies a frontmatter block,
never prose, so it must not quietly become a content sync. What it skips, it names.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("rsched.migrate")

#: The rules whose seed copy gained an assist in 0.305.0. Named explicitly rather than
#: derived from the seed: a migration that scans for "any rule with assists" would keep
#: firing for every assist added later, long after this one-shot should be gone.
RULES = ("error-recovery", "intent-inference", "decision-record")


def _block(seed_text: str) -> str | None:
    """The `assists:` block of a seed rule, verbatim — from the key to the next top-level
    frontmatter key. Returns None when the seed carries none.
    """
    lines = seed_text.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.startswith("assists:"))
    except StopIteration:
        return None
    end = start + 1
    while end < len(lines) and (lines[end].startswith((" ", "\t")) or not lines[end].strip()):
        end += 1
    return "\n".join(lines[start:end])


def migrate(rules_home: Path, seed_home: Path) -> list[str]:
    """Copy each named rule's seed `assists:` block into the live rule. Returns the log
    lines describing what happened, one per rule — nothing is silent.
    """
    notes: list[str] = []
    for slug in RULES:
        live_path = Path(rules_home) / f"{slug}.md"
        seed_path = Path(seed_home) / f"{slug}.md"
        try:
            live = live_path.read_text(encoding="utf-8")
            seed = seed_path.read_text(encoding="utf-8")
        except OSError as exc:
            notes.append(f"{slug}: skipped — {exc}")
            continue
        if "\nassists:" in f"\n{live}":
            notes.append(f"{slug}: already carries an assists: block — left alone")
            continue
        block = _block(seed)
        if block is None:
            notes.append(f"{slug}: the seed declares no assist — nothing to carry")
            continue
        # The body must still be the seed's, or this is not the rule this block was written
        # for. Compare what the block would be added TO, not the whole file.
        if _strip_assists(seed) != live:
            notes.append(f"{slug}: live prose has diverged from the seed — left alone, "
                         "add the assist by hand or on the Library tab")
            continue
        marker = "\ntags:"
        if marker not in live:
            notes.append(f"{slug}: no tags: line to anchor against — left alone")
            continue
        updated = live.replace(marker, f"\n{block}{marker}", 1)
        try:
            live_path.write_text(updated, encoding="utf-8")
        except OSError as exc:
            notes.append(f"{slug}: could not write — {exc}")
            continue
        notes.append(f"{slug}: assists: block installed")
    return notes


def _strip_assists(text: str) -> str:
    block = _block(text)
    return text if block is None else text.replace(f"{block}\n", "", 1)


def run(rules_home: Path, seed_home: Path) -> int:
    """Daemon-boot entry point. Returns the number of rules changed; logs every decision."""
    notes = migrate(rules_home, seed_home)
    changed = sum(1 for n in notes if n.endswith("installed"))
    if changed:
        log.warning("rule-assists migration: %s", "; ".join(notes))
    return changed
