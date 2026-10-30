"""One-shot boot migration: a seed rule's `assists:` block reaches the LIVE library —
MIGRATION(expires=2026-12-31).

`bootstrap.sync_seed_library_docs` is ADD-ONLY: it installs a rule the live library is missing
and never touches one it already has, because these files are user-editable and an overwrite
would discard an operator's edit. That is right for prose and wrong for a DECLARATION the
engine reads: `evidence-discipline` has declared assists in `library-seed/` since 0.471.0, the
live copy declares none, and so the hook points steps 6-8 wired (`child-exit`, `spawn`,
`run-end`) have been firing against zero candidates on this instance. A point that fires
nothing is indistinguishable from a dead one, which is the whole thing the registry exists to
make readable.

`migrate_converse_goals` carries a pattern by REPLACING the file, guarded by the hash of the
version it supersedes. That cannot work for a rule: the live prose has been revised since it
was seeded (several rules carry paragraphs the seed does not), and a whole replacement would
revert a revision that already reached every holder. So this edits the one thing it owns — it
inserts the seed's `assists:` block into the live frontmatter and leaves every other byte
alone — and it refuses a rule whose live copy already declares one, because that declaration
is the operator's and outranks the seed's.

A rule MISSING from the live library stays missing: the operator may have deleted it, and the
add-only sync already has the rule about resurrecting a deleted doc.

Runs once at daemon boot, after the seed sync, and records what it did in
`.control/migrations/rule-assists.json`; while that record exists it does nothing.
"""

from __future__ import annotations

import logging
from pathlib import Path

import yaml

from . import libgit
from .ids import now_iso
from .paths import atomic_write, atomic_write_json

log = logging.getLogger("rsched.migrate_rule_assists")

RECORD = Path(".control") / "migrations" / "rule-assists.json"
#: The rules whose seed `assists:` block this release carries. Named one by one rather than
#: swept: a migration that carried every seed declaration would also carry one a future release
#: adds, long after anyone reads this file.
RULES = ("evidence-discipline",)


def _frontmatter(text: str) -> dict | None:
    """The rule's YAML frontmatter, or None when it has none the linter would accept."""
    if not text.startswith("---"):
        return None
    try:
        meta = yaml.safe_load(text.split("---")[1])
    except yaml.YAMLError:
        return None
    return meta if isinstance(meta, dict) else None


def _block(seed_text: str) -> str:
    """The seed's `assists:` block as the exact LINES it occupies, so the live file gains the
    seed's own wording rather than a re-dump of parsed YAML (which would re-fold every line
    and make the diff unreadable to the person who has to review it).
    """
    lines = seed_text.splitlines(keepends=True)
    out: list[str] = []
    for line in lines:
        if out and line[:1] not in (" ", "-") and not line.startswith("assists:"):
            break
        if line.startswith("assists:") or out:
            out.append(line)
    return "".join(out)


def _carry(live: Path, seed: Path) -> tuple[bool, str]:
    """Insert the block. Returns (changed, why-not)."""
    try:
        live_text, seed_text = live.read_text(encoding="utf-8"), seed.read_text(encoding="utf-8")
    except OSError as exc:
        return False, f"not read: {exc}"
    meta, seed_meta = _frontmatter(live_text), _frontmatter(seed_text)
    if meta is None or seed_meta is None:
        return False, "frontmatter is not readable"
    if "assists" in meta:
        return False, "the live rule already declares assists — its declaration outranks the seed"
    if "assists" not in seed_meta:
        return False, "the seed declares none"
    block = _block(seed_text)
    if not block:
        return False, "the seed's block could not be located"
    # Before `tags:`, where every seed rule carries it — the frontmatter is a mapping, so the
    # position is cosmetic, and matching the seed's layout keeps the two readable side by side.
    marker = "\ntags:"
    if marker not in live_text:
        return False, "the live rule has no `tags:` line to insert before"
    head, rest = live_text.split(marker, 1)
    atomic_write(live, f"{head}\n{block.rstrip()}{marker}{rest}")
    return True, ""


def run_migration(server) -> dict:
    """Carry the declarations once. Returns the record (empty when it already ran)."""
    from .bootstrap import repo_root

    home = Path(server.routines_home)
    record_path = home / RECORD
    library = Path(server.libraries_home)
    if record_path.exists() or not home.is_dir() or not (library / "rules").is_dir():
        return {}
    record: dict = {"started": now_iso(), "carried": [], "left": {}}
    for name in RULES:
        rel = f"rules/{name}.md"
        live, seed = library / rel, repo_root() / "library-seed" / rel
        if not live.exists():
            record["left"][name] = "not in the live library (deleted rules stay deleted)"
            continue
        changed, why = _carry(live, seed)
        if changed:
            record["carried"].append(rel)
        else:
            record["left"][name] = why
    if record["carried"]:
        landed = libgit.commit(library, "rules: carry the seed `assists:` declarations the "
                                        "add-only seed sync cannot (hook points fired against "
                                        "no candidates without them)",
                               routines_home=home, paths=record["carried"])
        if landed.failed:   # changed on disk, not in the library's history: a person looks
            record["commit"] = landed.describe()
    record_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(record_path, record)
    log.warning("rule assists: carried %s; left %s", record["carried"] or "nothing",
                record["left"] or "nothing")
    return record
