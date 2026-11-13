"""One-shot boot migration: an INSTALLED pattern converges on its seed twin's three fixed keys.

MIGRATION(expires=2026-12-31).

THE CAUSE is the one this family keeps paying for: `bootstrap.sync_seed_library_docs` is
ADD-ONLY by design — it installs a doc the live library LACKS and never overwrites one it has,
because these files are operator-editable and an overwrite would silently discard his edits. So
F883's correction of the SEED patterns (0.534.3) reaches every instance built from then on and
never a live one. Measured on this instance 2026-11-12 and re-measured before this was written:
all FOURTEEN installed patterns read `max_total_turns: -1`, carry none of the three ladder keys
(`ladder`, `ladder_turn_interval`, `oversight_turns`) and omit `outcome-first` from `rules`.

THE SHARP HALF. `-1` is the exact value `migrate_total_turns` exists to remove — its own record
carries the operator's answer, "A migration rewrites a scaffold-written -1 to 0 on all 28
existing configs" — so every routine created from a pattern on this instance re-introduced,
past that migration's marker, the value it was built to eliminate. A one-shot that converges the
source of the value is the only thing that ends it; a second pass over routine.yaml would not.

The operator's answer, verbatim (2026-11-12, D283): *"A one-shot migration converts all fourteen
installed patterns (max_total_turns -1 → 0, the three ladder keys, outcome-first added), leaving
any list a pattern legitimately narrowed alone and recording what it changed"*.

WHAT IT MAY NOT DO, and how each clause is enforced:

* It converts only against a SEED TWIN. The seed file is what says the shipped value is, so a
  pattern the operator saved from a routine page ("Save as new pattern") has no twin and is
  left alone — its `-1` was carried from a routine that holds it deliberately.
* It converts only the DRIFT the twin names: a `max_total_turns` that is not `-1` is his own
  number and stands; a `ladder:` block he already wrote stands. The three ladder keys are
  carried per KEY and add-only, exactly as `seed_assists` carries one declaration at a time.
* **"Leaving any list a pattern legitimately narrowed alone" is the operator's clause, not a
  nicety.** `rules` is the only LIST this migration touches, and it is widened by exactly one
  entry only when the live list is the seed's minus that entry, in that order. A list that
  differs in any other way has been edited by hand — widening it back is the silent discard
  of an operator's edit the add-only rule exists to prevent — so it is left with its reason.
* It edits LINES, never a YAML round-trip. A re-dumped document reflows lists, drops the key
  order a person reads the file in and rewrites bytes nobody asked about; the decision is taken
  on the parsed document, the edit is made on the text, and a converted file is byte-identical
  to its twin where it converged and byte-identical to itself everywhere else.

Runs once at daemon boot, AFTER the add-only seed sync (on the files it left behind) and before
anything loads a pattern, and records what it changed per file in
`.control/migrations/pattern-drift.json`; while that record exists it does nothing.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml

from . import libgit
from .ids import now_iso
from .paths import atomic_write, atomic_write_json, read_yaml, repo_root

log = logging.getLogger("rsched.migrate_pattern_drift")

RECORD = Path(".control") / "migrations" / "pattern-drift.json"

#: The indent every key of a pattern's `settings:` mapping sits at (`atomic_write_yaml`'s dump).
INDENT = "  "

#: The budget key whose scaffold-written value made the run-spanning cap inert, and the value
#: `migrate_total_turns` converges every config on.
CAP, INERT, DERIVE = "max_total_turns", -1, 0

#: The one rule entry the answer names. Pinned as a constant rather than derived from the seed
#: diff: a future release adding a second rule to the seed patterns must decide for itself
#: whether a live pattern should gain it, which is a question for whoever ships it.
ADDED_RULE = "outcome-first"

#: The keys carried whole from the twin when the live file lacks them, in the seed's own order.
LADDER_KEYS = ("ladder", "ladder_turn_interval", "oversight_turns")


def _section(text: str) -> tuple[int, int]:
    """The `settings:` mapping's own span — every key this migration reads lives inside it.

    Bounded deliberately: an `asks:` entry's `question:` sits at the same two-space indent as a
    settings key, so a scan over the whole document would read one as the other.
    """
    opener = re.search(r"^settings:[^\n]*\n", text, re.MULTILINE)
    if opener is None:
        return -1, -1
    rest = re.search(r"^\S", text[opener.end():], re.MULTILINE)
    return opener.end(), opener.end() + rest.start() if rest else len(text)


def _key(body: str, key: str) -> re.Match[str] | None:
    """The settings key's own line, or None when the document does not carry it."""
    return re.search(rf"^{INDENT}{re.escape(key)}:[^\n]*\n", body, re.MULTILINE)


def _order(body: str) -> list[str]:
    """The settings keys, in the order the document writes them."""
    return re.findall(rf"^{INDENT}([A-Za-z_][A-Za-z0-9_]*):", body, re.MULTILINE)


def _block(body: str, key: str) -> str:
    """The key's own TEXT: its line plus every more deeply indented line beneath it."""
    found = _key(body, key)
    if found is None:
        return ""
    out = [found.group(0)]
    for line in body[found.end():].splitlines(keepends=True):
        if not line.startswith(INDENT + " "):      # three spaces: a child of this key
            break
        out.append(line)
    return "".join(out)


def _carry_key(body: str, seed_body: str, key: str) -> str:
    """Insert the twin's `key` block where the twin's key ORDER puts it."""
    block = _block(seed_body, key)
    order = _order(seed_body)
    for later in order[order.index(key) + 1:]:
        if (found := _key(body, later)) is not None:
            return body[:found.start()] + block + body[found.start():]
    return (body if body.endswith("\n") else body + "\n") + block


def _items(body: str, at: int) -> list[tuple[int, str, str]]:
    """The list items following a key line, each as (offset, its `- ` lead, its value)."""
    out: list[tuple[int, str, str]] = []
    pos = at
    while (found := re.match(r"(?P<lead>[ \t]*-[ \t]+)(?P<value>[^\n]*)\n", body[pos:])):
        out.append((pos, found.group("lead"), found.group("value").strip()))
        pos += found.end()
    return out


def _add_rule(body: str, rule: str) -> str | None:
    """Add one entry to the `rules:` list, in the alphabetical place the seed keeps it.

    None when the list is not a BLOCK list this can extend (an inline `rules: [a, b]`, or no
    key line at all). The caller must then record nothing: a migration whose own answer is
    "recording what it changed" may never enter a change it did not make, and returning the
    body unchanged would read exactly like a successful insert.
    """
    found = _key(body, "rules")
    if found is None:
        return None
    items = _items(body, found.end())
    if not items:
        return None
    lead = items[0][1]
    at = next((off for off, _lead, value in items if value > rule), None)
    if at is None:
        last_off, _l, _v = items[-1]
        at = last_off + len(body[last_off:].split("\n", 1)[0]) + 1
    return body[:at] + f"{lead}{rule}\n" + body[at:]


def _mapping(doc: dict, key: str) -> dict:
    """The mapping at `key`, or {} — ONE narrowing, so every reader below has a dict.

    A library file is operator-editable, so `settings:` or `budgets:` can be a scalar, a list
    or absent; narrowing at each read made the readers a tangle of ternaries mypy could not
    follow (`Item "None" of "Any | dict | None" has no attribute "get"`).
    """
    value = doc.get(key)
    return value if isinstance(value, dict) else {}


def _convert(live: Path, seed: Path) -> tuple[list[str], list[str]]:
    """Converge one pattern on its twin. Returns (what changed, what was left and why)."""
    live_doc, seed_doc = read_yaml(live, {}), read_yaml(seed, {})
    if not isinstance(live_doc, dict) or not isinstance(seed_doc, dict):
        return [], ["it is not a pattern document — left for a person"]
    live_set, seed_set = _mapping(live_doc, "settings"), _mapping(seed_doc, "settings")
    text = live.read_text(encoding="utf-8")
    start, end = _section(text)
    if start < 0:
        return [], ["it carries no `settings:` mapping — left for a person"]
    body, seed_body = text[start:end], _section_body(seed)
    changed: list[str] = []
    left: list[str] = []

    budgets, seed_budgets = _mapping(live_set, "budgets"), _mapping(seed_set, "budgets")
    if budgets.get(CAP) == INERT and seed_budgets.get(CAP) == DERIVE:
        body, hits = re.subn(rf"^(?P<lead>[ \t]*{CAP}:[ \t]*){INERT}[ \t]*$",
                             rf"\g<lead>{DERIVE}", body, flags=re.MULTILINE)
        if hits == 1:
            changed.append(f"budgets.{CAP}: {INERT} -> {DERIVE}")
        else:
            left.append(f"{hits} lines read `{CAP}: {INERT}` — left for a person")
    elif budgets.get(CAP) != DERIVE:
        left.append(f"budgets.{CAP} is {budgets.get(CAP)!r}, not the scaffold's {INERT} — his own")

    for key in LADDER_KEYS:
        if key not in seed_set:
            continue
        if key in live_set:
            left.append(f"it already carries its own `{key}`")
            continue
        body = _carry_key(body, seed_body, key)
        changed.append(key)

    live_rules, seed_rules = live_set.get("rules"), seed_set.get("rules")
    if isinstance(live_rules, list) and isinstance(seed_rules, list) \
            and ADDED_RULE in seed_rules and ADDED_RULE not in live_rules:
        if [r for r in seed_rules if r != ADDED_RULE] == live_rules:
            if (widened := _add_rule(body, ADDED_RULE)) is not None:
                body = widened
                changed.append(f"rules: + {ADDED_RULE}")
            else:
                left.append("its `rules:` is not a block list this can extend — left "
                            "for a person")
        else:
            left.append(f"its `rules:` is narrowed (or otherwise edited) against the seed, so "
                        f"adding {ADDED_RULE} would widen a deliberate choice")

    if changed:
        atomic_write(live, text[:start] + body + text[end:])
    return changed, left


def _section_body(path: Path) -> str:
    """The `settings:` body of a document on disk — the twin's text, read once per file."""
    text = path.read_text(encoding="utf-8")
    start, end = _section(text)
    return text[start:end] if start >= 0 else ""


def run_migration(server) -> dict:
    """Converge the installed patterns once. Returns the record (empty when it already ran)."""
    # Read with getattr, never as attributes: a boot migration is handed whatever
    # `load_server_config` returns, and the fresh-container boot test hands it a four-attribute
    # stand-in — so a hard access makes the WHOLE DAEMON BOOT die inside a migration that had
    # nothing to migrate (`migrate_total_turns` learned this the same way).
    home = getattr(server, "routines_home", None)
    library = getattr(server, "libraries_home", None)
    if not home or not library:
        return {}
    home, library = Path(home), Path(library)
    seed_root = repo_root() / "library-seed" / "patterns"
    if not home.is_dir() or not (library / "patterns").is_dir() or not seed_root.is_dir():
        return {}
    record_path = home / RECORD
    if record_path.exists():
        return {}      # the record is the guard, and an unreadable one is never licence to run
    record: dict = {"started": now_iso(), "converted": {}, "left": {}, "failed": {}}
    for seed in sorted(seed_root.glob("*.yaml")):
        rel = f"patterns/{seed.name}"
        live = library / "patterns" / seed.name
        if not live.exists():
            record["left"][rel] = "not in the live library (deleted patterns stay deleted)"
            continue
        try:
            changed, left = _convert(live, seed)
        except (OSError, yaml.YAMLError) as exc:
            record["failed"][rel] = f"{type(exc).__name__}: {exc}"
            continue
        if changed:
            record["converted"][rel] = changed
        if left:
            record["left"][rel] = "; ".join(left)
    for live in sorted((library / "patterns").glob("*.yaml")):
        if not (seed_root / live.name).exists():
            record["left"][f"patterns/{live.name}"] = (
                "no seed twin — it is the operator's own pattern, so nothing says what the "
                "shipped value is")
    if record["converted"]:
        landed = libgit.commit(library, "patterns: the installed copies gain the seed's "
                                        "max_total_turns, ladder keys and outcome-first — the "
                                        "add-only doc sync cannot reach a live library (D283)",
                               routines_home=home, paths=sorted(record["converted"]))
        if landed.failed:   # converted on disk, not in the library's history: a person looks
            record["commit"] = landed.describe()
    record_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(record_path, record)
    log.warning("pattern drift: converted %s; left %s", record["converted"] or "nothing",
                record["left"] or "nothing")
    return record
