"""One-shot boot migration: every LIVE rule gains `read_when:`, and eight gain their first
assists — MIGRATION(expires=2026-11-30).

`read_when:` is now required of a rule (`rules.read_when_problems`, run by the linter): the
line the state digest shows beside each held slug, naming the moment a run reads the rule in
full. The seed sync is ADD-ONLY, so the seed's new lines reach no live rule on their own, and
the live library has moved on from the seed besides — rules-review revised two seeded rules
and authored two the seed never had. So this does not compare bodies the way the earlier rule
migrations did: it inserts two FRONTMATTER keys and touches no prose, which is safe on a
revised rule as long as the rule does not already say something else there.

- `read_when:` comes from the seed copy, or from `LIVE_ONLY` for a rule the seed lacks. A
  rule that already carries one is left alone, and one this release has no line for is named.
- the eight rules in `NEW_ASSISTS` gain the seed's `assists:` block verbatim — unless the live
  rule already carries an `assists:` key, which an operator or rules-review wrote and which
  outranks the seed: named, with the ids to add by hand.

A change that would ADD a lint problem the rule did not have is not written. Runs once at
daemon boot, after the seed sync, commits what it changed to the library repo and records the
outcome in `.control/migrations/rule-read-when.json`; while that record exists it does nothing.
"""

from __future__ import annotations

import logging
import textwrap
from pathlib import Path

from . import libgit
from .ids import now_iso
from .library_docs import parse_lenient
from .paths import atomic_write, atomic_write_json

log = logging.getLogger("rsched.migrate_rule_read_when")

RECORD = Path(".control") / "migrations" / "rule-read-when.json"

#: The live rules the seed does not carry (authored on the instance), with their line.
LIVE_ONLY = {
    "conclusion-scepticism": ("Before a conclusion you reasoned your way to becomes the basis of "
                              "later work, a report or an outward act."),
    "resource-economy": ("Before you launch a long or large step, and when the same expensive "
                         "step has failed twice."),
}

#: The rules whose seed copy gained its first assists in this release. Named rather than
#: derived from the seed, so the next batch of assists needs its own one-shot.
NEW_ASSISTS = ("change-scope", "correspondence", "feedback-loop", "make-failure-visible",
               "verify-independently", "web-research", "work-order", "write-as-the-principal")


def run_migration(server) -> dict:
    """Carry the keys once. Returns the record (empty when it already ran)."""
    from .bootstrap import repo_root

    home = Path(server.routines_home)
    record_path = home / RECORD
    rules_home = Path(server.libraries_home) / "rules"
    if record_path.exists() or not home.is_dir() or not rules_home.is_dir():
        return {}
    seed = repo_root() / "library-seed" / "rules"
    record: dict = {"started": now_iso(), "changed": {}, "left": {}}
    for live in sorted(rules_home.glob("*.md")):
        seed_path = seed / live.name
        seed_text = seed_path.read_text(encoding="utf-8") if seed_path.is_file() else ""
        changed, left = _carry(live, seed_text)
        if changed:
            record["changed"][live.stem] = changed
        if left:
            record["left"][live.stem] = left
    if record["changed"]:
        landed = libgit.commit(Path(server.libraries_home),
                               "rules: every rule names the moment a run reads it (read_when), "
                               "and eight gain their first assists",
                               routines_home=home,
                               paths=[f"rules/{slug}.md" for slug in record["changed"]])
        if landed.failed:   # changed on disk, not in the library's history: a person must look
            record["commit"] = landed.describe()
    record_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(record_path, record)
    log.warning("rule read_when: changed %s; left %s",
                ", ".join(f"{s} ({'+'.join(c)})" for s, c in record["changed"].items()) or "none",
                "; ".join(f"{s} ({why})" for s, why in record["left"].items()) or "none")
    return record


def _carry(live: Path, seed_text: str) -> tuple[list[str], str]:
    """Insert what this rule is missing. Returns (keys added, why anything was left)."""
    from .workflows.lint import lint_rule_text

    try:
        text = live.read_text(encoding="utf-8")
    except OSError as exc:
        return [], f"unreadable: {exc}"
    meta = parse_lenient(text)[0]
    seed_meta = parse_lenient(seed_text)[0] if seed_text else {}
    added: list[str] = []
    left: list[str] = []
    new = text
    if not meta.get("read_when"):
        line = " ".join(str(seed_meta.get("read_when") or LIVE_ONLY.get(live.stem) or "").split())
        if line:
            new = _insert(new, _folded("read_when", line))
            added.append("read_when")
        else:
            left.append("this release has no read_when line for it — write one on the Library "
                        "tab")
    if live.stem in NEW_ASSISTS and (block := _block(seed_text)):
        if "assists" in meta:
            ids = [str(a.get("id")) for a in seed_meta.get("assists") or [] if isinstance(a, dict)]
            left.append(f"already carries an assists: block — add {', '.join(ids)} from the "
                        "seed by hand")
        else:
            new = _insert(new, block)
            added.append("assists")
    if new == text:
        return [], "; ".join(left)
    before = set(lint_rule_text(text, filename=live.name))
    introduced = [p for p in lint_rule_text(new, filename=live.name) if p not in before]
    if introduced:
        return [], "; ".join([*left, f"not written — it would add: {'; '.join(introduced)}"])
    try:
        atomic_write(live, new)
    except OSError as exc:
        return [], f"could not write: {exc}"
    return added, "; ".join(left)


def _insert(text: str, block: str) -> str:
    """`block` as the last frontmatter key — just above the closing `---`."""
    end = text.find("\n---\n", 3) if text.startswith("---\n") else -1
    if end == -1:
        return text
    return f"{text[:end]}\n{block}{text[end:]}"


def _folded(key: str, value: str) -> str:
    return f"{key}: >-\n" + "\n".join(f"  {ln}" for ln in textwrap.wrap(value, width=92))


def _block(seed_text: str) -> str:
    """The seed rule's `assists:` block, verbatim — from the key to the next top-level key."""
    lines = seed_text.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines) if ln.startswith("assists:"))
    except StopIteration:
        return ""
    end = start + 1
    while end < len(lines) and (lines[end].startswith((" ", "\t")) or not lines[end].strip()):
        end += 1
    return "\n".join(lines[start:end])
