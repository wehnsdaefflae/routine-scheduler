"""DONE WHEN — what one finished run leaves behind, read from the recipe's own `## Done when`.

It lives in the recipe because that is where the design of a run lives and changes: the
improver, a recipe revision and `write_recipe` all edit `main.md`; a second copy anywhere
else is a copy that drifts. One line per outcome, with a stable id and — where the recipe has
stage modules — the stage that produces it:

    ## Done when

    - d1 · gather — every due source was read, or recorded as unreadable
    - d2 · publish — the page is published and read back
    - d3 — the ledger records what was decided and why

A run accounts for every line once, at its main finish (`accounting.py`): met with its
evidence, unmet with what remains, or not due with how that was established. Nothing here
limits how much a run does.
"""

from __future__ import annotations

import re
from pathlib import Path

HEADING = "## Done when"
_HEAD_RE = re.compile(rf"^{re.escape(HEADING)}\s*$", re.IGNORECASE | re.MULTILINE)
_LINE_RE = re.compile(r"^- (d\d+)(?: · ([a-z0-9][a-z0-9_-]*))? — (\S.*)$")


def section(text: str) -> str:
    """The section's body — from its heading to the next `## ` heading — or "" when absent."""
    m = _HEAD_RE.search(text or "")
    if not m:
        return ""
    rest = text[m.end():]
    nxt = re.search(r"^## ", rest, re.MULTILINE)
    return rest[:nxt.start()] if nxt else rest


def parse(text: str) -> list[dict]:
    """`[{id, stage, text}]` in recipe order; lines that do not follow the format are left out
    (`problems` names them).
    """
    out = []
    for line in section(text).splitlines():
        m = _LINE_RE.match(line.strip())
        if m:
            out.append({"id": m[1], "stage": m[2] or "", "text": m[3].strip()})
    return out


def read(routine_dir: Path) -> list[dict]:
    main = Path(routine_dir) / "main.md"
    try:
        return parse(main.read_text(encoding="utf-8"))
    except OSError:
        return []


def problems(text: str, stages: set[str] | None = None) -> list[str]:
    """What is wrong with a recipe's `## Done when`, as sentences — the recipe linter's check.
    A recipe without the section has nothing to account for and is not a problem here.
    """
    body = section(text)
    if not body:
        return []
    out = []
    ids = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            continue
        m = _LINE_RE.match(line)
        if not m:
            out.append(f"Done when: {line[:60]!r} is not `- d<n> · <stage> — <outcome>` "
                       "(or `- d<n> — <outcome>`)")
            continue
        ids.append(m[1])
        if m[2] and stages is not None and m[2] not in stages:
            out.append(f"Done when {m[1]}: no stage module stages/{m[2]}.md")
    if ids != [f"d{i}" for i in range(1, len(ids) + 1)]:
        out.append("Done when: ids run d1, d2, … in order with no gaps")
    if not ids:
        out.append("Done when: the section lists no outcome")
    return out
