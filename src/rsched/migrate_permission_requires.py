"""One-shot boot migration: a permission's `requires:` names the LIVE util —
MIGRATION(expires=2026-12-31).

THE SYMPTOM, reported from outside: every run of `predatory-publisher-tarpit` opens with two
FAIL rows, and the routine cannot fix them from inside itself. Its words, through
`rules-review` (R3081): *"the same consolidation left my `outbound-mail` permission
`requires:` naming `fau-mail` and `gmail`, which the engine now reports as two FAIL rows at
the start of every one of my runs."* Two standing FAIL rows at every run start is worse than
a dead link: it teaches a run to skim its own preflight.

THE CAUSE is one a rename keeps reproducing. The `fau-mail`/`gmail` → `mail` consolidation
reached every CALLER's code and nothing reached the stores that hold util names as STRINGS.
Five symptoms of that one cause have now been found weeks apart by different routines: four
consequence-reminder regexes (R3040-R3047), four task notebooks (`fau`, R3054), and this
permission. Measured here before writing this: of the 18 seed permissions and the 20 live
ones, `outbound-mail` is the ONLY file whose `requires:` names a util the 57-util catalog no
longer holds.

AND IT REACHES A SECOND STORE, which the repo's own test found the moment the permission was
corrected: four settings PATTERNS (`PATTERNS` below) reserve the same two dead verbs and
nothing else. `floor_capabilities` keeps a reserved util only while a HELD doc requires it, so
the corrected permission would have floored `fau-mail:send`/`gmail:send` straight back out of
every routine created from those patterns — a send reservation that vanishes at the first save.
`tests/test_patterns.py::test_a_shipped_patterns_capabilities_survive_its_own_permissions` is
what caught it, and it is the sixth symptom of this one cause found by a different reader: the
lesson is that a util rename must sweep the stores that hold util names as STRINGS, and that a
partial correction of them can be worse than none.

What this migration does NOT touch: the six live routine.yaml files that still name the two
dead verbs beside the live one. Those are the operator's config (every one already carries
`mail:send`, so nothing is blocked), and the `absent util` rows they produce now offer the
performable remedy — drop the dead name — instead of "install a util that will never exist",
which is the whole visible effect of correcting the permission.

WHY A MIGRATION AND NOT JUST THE SEED EDIT. `bootstrap.sync_seed_library_docs` is ADD-ONLY by
design — it installs a doc the live library LACKS and never overwrites one it has, because
these files are operator-editable and an overwrite would discard his wording. So the corrected
seed reaches a fresh instance and never this one. The repo's own rule (CLAUDE.md: "a seed doc
whose text must change on a live instance is converted by a one-shot migration instead") is
what this file is.

It edits ONLY the `requires:` block, exactly as `seed_assists` carries only an `assists:`
declaration: the body prose below the frontmatter is the operator's and is left byte for
byte. A live file that does NOT name a dead util is left alone, so a hand-correction he already
made is never reverted, and a permission MISSING from the live library stays missing (he may
have deleted it; the add-only sync already has that rule).

Runs once at daemon boot, after the seed sync, recorded in
`.control/migrations/permission-requires.json`; while that record exists it does nothing.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from . import libgit
from .ids import now_iso
from .paths import atomic_write, atomic_write_json

log = logging.getLogger("rsched.migrate_permission_requires")

RECORD = Path(".control") / "migrations" / "permission-requires.json"

#: permission name -> (the dead `utils:` list as the live file carries it, its replacement).
#: Named one by one: a migration that swept every seed declaration would also carry one a
#: future release adds, long after anyone reads this file.
REQUIRES = {
    "outbound-mail": ("[gmail:send, fau-mail:send]", "[mail:send]"),
}

#: The settings PATTERNS that reserve the same two dead verbs — found by the repo's own
#: `tests/test_patterns.py::test_a_shipped_patterns_capabilities_survive_its_own_permissions`
#: the moment the permission above was corrected, which is the whole reason they are here:
#: `floor_capabilities` keeps a reserved util only while a HELD doc requires it, so a pattern
#: naming only the dead verbs would hand every routine created from it a send reservation that
#: the first save then floors away. The patterns are read LIVE (creation, the routine page, the
#: recommender), so the live copies matter as much as the seed.
PATTERNS = ("radar", "project-steward", "mailbox-service", "personal-steward")

#: The two dead list items, as the live YAML carries them, and the one live verb replacing them.
DEAD_ITEMS = re.compile(r"^(\s+)- fau-mail:send\n\s+- gmail:send\n", re.MULTILINE)
LIVE_ITEM = r"\1- mail:send\n"


def _carry(live: Path, dead: str, live_form: str) -> tuple[bool, str]:
    """Replace the one `utils:` list. Returns (changed, why-not)."""
    try:
        text = live.read_text(encoding="utf-8")
    except OSError as exc:
        return False, f"not read: {exc}"
    pattern = re.compile(r"(^\s*utils:\s*)" + re.escape(dead) + r"\s*$", re.MULTILINE)
    if not pattern.search(text):
        return False, f"its `requires:` no longer names {dead} — already corrected by hand"
    atomic_write(live, pattern.sub(lambda m: m.group(1) + live_form, text, count=1))
    return True, ""


def _carry_pattern(live: Path) -> tuple[bool, str]:
    """Replace the two dead reserved verbs in one pattern's `utils:` list."""
    try:
        text = live.read_text(encoding="utf-8")
    except OSError as exc:
        return False, f"not read: {exc}"
    new, n = DEAD_ITEMS.subn(LIVE_ITEM, text, count=1)
    if not n:
        return False, "no longer reserves fau-mail:send/gmail:send — already corrected by hand"
    atomic_write(live, new)
    return True, ""


def run_migration(server) -> dict:
    """Correct the lists once. Returns the record (empty when it already ran)."""
    home = Path(server.routines_home)
    record_path = home / RECORD
    library = Path(server.libraries_home)
    if record_path.exists() or not home.is_dir() or not (library / "permissions").is_dir():
        return {}
    record: dict = {"started": now_iso(), "carried": [], "left": {}}
    for name, (dead, live_form) in REQUIRES.items():
        rel = f"permissions/{name}.md"
        live = library / rel
        if not live.exists():
            record["left"][name] = "not in the live library (deleted permissions stay deleted)"
            continue
        changed, why = _carry(live, dead, live_form)
        if changed:
            record["carried"].append(rel)
        else:
            record["left"][name] = why
    for name in PATTERNS:
        rel = f"patterns/{name}.yaml"
        live = library / rel
        if not live.exists():
            record["left"][rel] = "not in the live library (deleted patterns stay deleted)"
            continue
        changed, why = _carry_pattern(live)
        if changed:
            record["carried"].append(rel)
        else:
            record["left"][rel] = why
    if record["carried"]:
        landed = libgit.commit(library, "permissions + patterns: name the live `mail:send` — "
                                        "the fau-mail/gmail consolidation left two dead names "
                                        "FAILing at every run start of every holder, and four "
                                        "patterns reserving verbs the floor would strip (R3081)",
                               routines_home=home, paths=record["carried"])
        if landed.failed:   # changed on disk, not in the library's history: a person looks
            record["commit"] = landed.describe()
    record_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(record_path, record)
    log.warning("permission requires: carried %s; left %s", record["carried"] or "nothing",
                record["left"] or "nothing")
    return record
