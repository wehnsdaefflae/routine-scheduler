"""The routine's state graph, derived from its own recipe — for the UI's live diagram.

A materialized main.md carries its control flow as a `## Run flow` numbered list (older
recipes may use a `## Phases` bullet list) whose items lead with a **bold** state name.
`state_graph` parses those states and pairs them with the routine's CURRENT phase
(`state/phase.json`, mirrored live into status.json by the engine) so the web layer can
render a simple highlighted chain. Parsing is tolerant by design: recipes are prose owned
by the routine, not a schema — a flow with no parseable state names yields the stages/
module names (each stage IS a node), and an unknown current phase is simply appended as
its own node client-side.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

MAX_STATES = 16

# "1. **name** — desc" / "- **name**: desc" / "2) **name**" … — the canonical shape.
_BOLD_ITEM = re.compile(r"^\s*(?:\d+[.)]|[-*])\s+\*\*(.+?)\*\*\s*[—:–-]?\s*(.*)$")
# plain "1. name — desc" (no bold), the fallback shape inside a Run flow list. The name is
# held to a short word-ish token (letters/digits/space/underscore/hyphen) so a line of PROSE
# — "1. Read `state/phase.json` (…)" — never scrapes into a junk node; such recipes fall
# through to the stages/ listing instead.
_PLAIN_ITEM = re.compile(r"^\s*\d+[.)]\s+([A-Za-z][A-Za-z0-9 _-]{0,40}?)\s*[—:]\s*(.*)$")


def norm(name: str) -> str:
    """Loose state identity: 'Gather Evidence' == 'gather-evidence'."""
    return re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")


def _section(md: str, *titles: str) -> list[str]:
    """Lines of the first `## <title>` section that exists (case-insensitive)."""
    lines = md.splitlines()
    for title in titles:
        out: list[str] | None = None
        for line in lines:
            if re.match(rf"^##\s+{re.escape(title)}\s*$", line.strip(), re.IGNORECASE):
                out = []
                continue
            if out is not None:
                if line.startswith("## "):
                    break
                out.append(line)
        if out:
            return out
    return []


def parse_states(main_md: str) -> list[dict]:
    """[{name, desc}] in flow order — from ## Run flow (or ## Phases) item leads."""
    states: list[dict] = []
    seen: set[str] = set()
    for line in _section(main_md, "Run flow", "Phases"):
        m = _BOLD_ITEM.match(line) or _PLAIN_ITEM.match(line)
        if not m:
            continue
        name = m.group(1).strip().strip("`")
        key = norm(name)
        if not key or key in seen:
            continue
        seen.add(key)
        states.append({"name": name, "desc": m.group(2).strip().rstrip(".")[:160]})
        if len(states) >= MAX_STATES:
            break
    return states


def current_phase(routine_dir: Path) -> str:
    try:
        data = json.loads((routine_dir / "state" / "phase.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    # recipes name the current-phase field "phase" (canonical), "state", or "step" — accept any
    return (str(data.get("phase") or data.get("state") or data.get("step") or "")
            if isinstance(data, dict) else "")


def state_graph(routine_dir: Path) -> dict:
    """{states: [{name, desc}], current: str} for one routine/conversation dir. `current`
    is the raw recorded phase — the client matches it against states via norm().
    """
    try:
        md = (routine_dir / "main.md").read_text(encoding="utf-8")
    except OSError:
        md = ""
    states = parse_states(md)
    if not states:  # no parseable flow leads → the stage modules ARE the map (each a node)
        stages = routine_dir / "stages"
        if stages.is_dir():
            states = [{"name": p.stem, "desc": ""}
                      for p in sorted(stages.glob("*.md"))][:MAX_STATES]
    return {"states": states, "current": current_phase(routine_dir)}


_HEADING = re.compile(r"^(#{1,4})\s+(.+?)\s*$")


def outline(md_text: str) -> list[dict]:
    """[{level, text}] for the ## / ### / #### headings of a recipe file — the file's own
    structure, for the routine page's navigable tree. Headings inside ``` fenced blocks
    (a pattern's Python, say) are skipped so a `# comment` never poses as a section.
    """
    out: list[dict] = []
    in_fence = False
    for line in md_text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _HEADING.match(line)
        if m and 2 <= len(m.group(1)) <= 4:
            out.append({"level": len(m.group(1)), "text": m.group(2).strip().strip("`")})
    return out


def recipe_tree(routine_dir: Path) -> dict:
    """The routine's recipe as a navigable tree for the routine page: main.md + its stage modules
    (in ## Run flow order, any extras appended alphabetically) + trait modules, each with its
    heading outline. Purely a read-model over the routine's own files.
    """
    def _read(p: Path) -> str:
        try:
            return p.read_text(encoding="utf-8")
        except OSError:
            return ""

    main_md = _read(routine_dir / "main.md")
    order = [norm(s["name"]) for s in parse_states(main_md)]

    def _rank(p: Path) -> int:
        try:
            return order.index(norm(p.stem))
        except ValueError:
            return len(order) + 1  # extras (no flow entry) sort after the flow, then alphabetically

    def _entry(p: Path) -> dict:
        return {"path": str(p.relative_to(routine_dir)), "name": p.stem,
                "outline": outline(_read(p))}

    def _files(sub: str) -> list[Path]:
        d = routine_dir / sub
        return sorted(d.glob("*.md")) if d.is_dir() else []

    stage_files = sorted(_files("stages"), key=lambda p: (_rank(p), p.stem))
    return {
        "main": {"path": "main.md", "name": "main", "outline": outline(main_md)},
        "stages": [_entry(p) for p in stage_files],
        "traits": [_entry(p) for p in _files("traits")],
    }
