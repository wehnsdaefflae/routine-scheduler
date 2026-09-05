"""MIGRATION(expires=2026-12-01): carry the card-heading instruction into the LIVE `status-page`
rule.

That rule tells every holder what to publish as its hub card's `tab`, so the instruction has to
name something a run can actually produce: its prompt gives it the shared store it belongs to plus
the routines named beside it, so the heading is a name those members keep in that store. The seed
sync (`bootstrap.sync_seed_library_docs`) is ADD-ONLY by design — it
installs a rule the live library is missing and never overwrites one — so rewriting
`library-seed/rules/status-page.md` reaches exactly zero instances on its own. This is the
one-shot that closes that gap, in the shape `migrate_rule_assists` established.

Narrow on purpose. It replaces the rule's closing section and nothing else, so a revision
`rules-review` made anywhere else in the prose survives. A closing section that is not the text
this was written against is somebody's own edit: left alone and named in the log rather than
passed over quietly.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("rsched.migrate")

SLUG = "status-page"

#: Everything from this heading to the end of the file moves together: the card's one paragraph
#: and the tab above it are one instruction about one card.
HEADING = "## Say what is true on the card"

INSTALLED = "card section installed"
CONVERGED = "the card section is already the current one — nothing to do"

#: The live text this was written against, verbatim. An exact match is what separates an
#: untouched library copy from one somebody has revised; only the first is safe to replace.
OLD_SECTION = """## Say what is true on the card

The hub shows one card per project and sorts by what is waiting on him. The count of things
awaiting his decision is COUNTED from your state — the gate and the open question, nothing else —
so it cannot be overstated and is not yours to write.

What is yours is the card's one paragraph, and it carries the whole weight: in your own voice, in
the second person, saying what changed since he last looked and what now waits on him. Not a
summary of your run. The answer to "do I need to open this today".

The card's `tab` is the name of the ROUTINE GROUP you belong to — exactly as the scheduler spells
it, punctuation and all. Not a category you invent: the grouping already exists and is the one he
reasons about, so a second taxonomy for the hub just gives the same set two names and gets one of
them wrong. If you are moved to another group, your tab moves with you at your next run.
"""


def _section(text: str) -> str | None:
    """A rule's card section — from the heading to the end of the file, or None when the rule
    carries no such heading.
    """
    idx = text.find(HEADING)
    return None if idx < 0 else text[idx:]


def migrate(rules_home: Path, seed_home: Path) -> str:
    """Replace the live rule's card section with the seed's and return the one line saying
    what happened. Idempotent: a live copy that already carries the seed's section is left as
    it is.
    """
    live_path = Path(rules_home) / f"{SLUG}.md"
    seed_path = Path(seed_home) / f"{SLUG}.md"
    try:
        live = live_path.read_text(encoding="utf-8")
        seed = seed_path.read_text(encoding="utf-8")
    except OSError as exc:
        return f"{SLUG}: skipped — {exc}"
    seed_section = _section(seed)
    if seed_section is None:
        return f"{SLUG}: the seed rule has no '{HEADING}' section — nothing to carry"
    live_section = _section(live)
    if live_section is None:
        return f"{SLUG}: the live rule has no '{HEADING}' section — left alone"
    if live_section == seed_section:
        return f"{SLUG}: {CONVERGED}"
    if live_section != OLD_SECTION:
        return (f"{SLUG}: the live card section has been edited — left alone; apply the new "
                "tab paragraph by hand or on the Library tab")
    updated = live.removesuffix(live_section) + seed_section
    try:
        live_path.write_text(updated, encoding="utf-8")
    except OSError as exc:
        return f"{SLUG}: could not write — {exc}"
    return f"{SLUG}: {INSTALLED}"


def run(rules_home: Path, seed_home: Path) -> int:
    """Daemon-boot entry point. Returns 1 when the live rule was rewritten and 0 otherwise.
    Logs the decision either way — a skip an operator has to act on is a warning; a converged
    instance is not.
    """
    note = migrate(rules_home, seed_home)
    changed = note.endswith(INSTALLED)
    (log.info if note.endswith(CONVERGED) else log.warning)("status-page rule: %s", note)
    return int(changed)
