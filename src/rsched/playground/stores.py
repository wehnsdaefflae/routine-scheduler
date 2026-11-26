"""The four helper stores, read into ONE uniform shape: the CONDITION.

The proposal's claim is that utils, memory notes, reminders and rules can share a retrieval
layer. For that to be testable they must first be readable as one kind of thing, and the choice
of WHAT to read is the substantive part — not a detail of plumbing.

Each item is reduced to the text that states WHEN IT APPLIES, never its payload:

* **utils** — `summary` + the `usage` verb lines + `tags`. Not the source: a util's body is how
  it works, and a query is about what is needed.
* **memory** — the `about` line of `.memory/INDEX.md`, which the memory action contract already
  specifies as "what this note holds + WHEN TO CONSULT IT". Already a trigger condition.
* **reminders** — the `description` (the consequence to be cautioned about), never the `regex`:
  the regex is the current mechanism being compared against, so indexing it would be circular.
* **rules** — `effect.when` plus each assist's `line`. `when` is literally the rule's moment.

Everything is read-only here: the in-run layer only ever reads these stores.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Condition:
    """One stored item as the retrieval layer sees it."""

    store: str              # utils | memory | reminders | rules
    ident: str              # the item's own handle (util name, note name, rem id, rule slug)
    condition: str          # the text that says WHEN this item applies — what gets indexed
    owner: str = ""         # the routine a note/local reminder belongs to ("" = global/shared)
    detail: str = ""        # the one line a firing quotes beside the hit


def _usage_block(doc: str) -> str:
    """A util docstring's `usage:` block — every line up to the next header key.

    Mirrors how the catalog itself reads a util header (`utils_header`): the block is the
    invocation surface, and a verb named only on a continuation line is part of the condition.
    """
    lines = doc.splitlines()
    out: list[str] = []
    seen = False
    for line in lines:
        if not seen:
            if line.strip().startswith("usage:"):
                seen = True
            continue
        if re.match(r"^\s*(tags|net|fs|calls|secrets|informational-exits|requires):", line):
            break
        out.append(line.strip())
    return " ".join(x for x in out if x)


def utils(libraries_home: Path) -> list[Condition]:
    """Every global util as a condition: summary + usage verbs + tags."""
    root = Path(libraries_home) / "utils"
    if not root.is_dir():
        return []
    out: list[Condition] = []
    for main in sorted(root.glob("*/main.py")):
        try:
            text = main.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = re.search(r'"""(.*?)"""', text, re.DOTALL)
        doc = m.group(1) if m else ""
        head = doc.strip().splitlines()[0] if doc.strip() else ""
        summary = head.split("—", 1)[1].strip() if "—" in head else head
        tags = ""
        if (t := re.search(r"^\s*tags:\s*(.+)$", doc, re.MULTILINE)) is not None:
            tags = t.group(1).strip()
        usage = _usage_block(doc)
        name = main.parent.name
        out.append(Condition(
            store="utils", ident=name,
            condition=f"{name} {summary} {tags} {usage}".strip(),
            detail=summary))
    return out


def memory(routines_home: Path) -> list[Condition]:
    """Every routine's memory notes, from the engine-owned `.memory/INDEX.md`.

    One `- <name>.md: <about>` line per note (engine/memops `_memory_index_upsert`), so the
    `about` — specified as what the note holds AND when to consult it — is the condition. A
    trailing ` · run-verified <date>` provenance tail is kept out of the indexed text.
    """
    out: list[Condition] = []
    root = Path(routines_home)
    if not root.is_dir():
        return []
    for index in sorted(root.glob("*/.memory/INDEX.md")):
        owner = index.parent.parent.name
        try:
            text = index.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            m = re.match(r"^-\s+(\S+?)\.md:\s*(.+)$", line.strip())
            if not m:
                continue
            about = m.group(2).strip()
            core = about.split(" · ")[0].strip()
            out.append(Condition(
                store="memory", ident=m.group(1), owner=owner,
                condition=core, detail=about))
    return out


def reminders(libraries_home: Path, routines_home: Path) -> list[Condition]:
    """Consequence reminders from BOTH stores, each keeping its blast radius.

    `owner` is "" for a global reminder and the routine slug for a local one — a firing records
    it, because a global row taxes every routine it reaches while a local one does not.
    """
    out: list[Condition] = []

    def _one(payload: dict, owner: str) -> Condition | None:
        ident = str(payload.get("id") or "").strip()
        desc = str(payload.get("description") or "").strip()
        if not ident or not desc:
            return None
        return Condition(
            store="reminders", ident=ident, owner=owner, condition=desc, detail=desc)

    gdir = Path(libraries_home) / "reminders"
    if gdir.is_dir():
        for path in sorted(gdir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(payload, dict) and (c := _one(payload, "")) is not None:
                out.append(c)

    rroot = Path(routines_home)
    if rroot.is_dir():
        for path in sorted(rroot.glob("*/state/reminders.json")):
            owner = path.parent.parent.name
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            rows = payload if isinstance(payload, list) else payload.get("reminders") or []
            out.extend(c for row in (rows if isinstance(rows, list) else [])
                       if isinstance(row, dict) and (c := _one(row, owner)) is not None)
    return out


def rules(libraries_home: Path) -> list[Condition]:
    """Every general rule as a condition: `effect.when` + its assists' operative lines.

    The assist lines are included deliberately: a rule's assist `line` is the payload the
    engine surfaces at the moment the rule applies, so it is the most situation-shaped prose
    the document carries.
    """
    from .. import library_docs

    home = Path(libraries_home) / "rules"
    out: list[Condition] = []
    for doc in library_docs.list_docs(home):
        effect = doc.get("effect") or {}
        when = str(effect.get("when") or doc.get("summary") or "").strip()
        lines: list[str] = [
            re.sub(r"\s+", " ", str(a.get("line"))).strip()
            for a in (doc.get("assists") or [])
            if isinstance(a, dict) and a.get("line")]
        slug = str(doc.get("slug") or "")
        out.append(Condition(
            store="rules", ident=slug,
            condition=" ".join([slug.replace("-", " "), when, *lines]).strip(),
            detail=when))
    return out


def load_all(libraries_home: Path, routines_home: Path) -> list[Condition]:
    """Every store, in one list — what a unified retrieval layer would index."""
    return [*utils(libraries_home), *memory(routines_home),
            *reminders(libraries_home, routines_home), *rules(libraries_home)]
