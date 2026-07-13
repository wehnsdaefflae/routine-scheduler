"""The routine's state graph, derived from its own recipe — for the UI's live diagram.

A materialized main.md carries its control flow as a `## Run flow` numbered list (older
recipes may use a `## Phases` bullet list) whose items lead with a **bold** state name.
`state_graph` parses those states and pairs them with the routine's CURRENT phase
(`state/phase.json`, mirrored live into status.json by the engine) so the web layer can
render a simple highlighted chain. Parsing is tolerant by design: recipes are prose owned
by the routine, not a schema — an unparseable flow yields the steps/ module names, and an
unknown current phase is simply appended as its own node client-side.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

MAX_STATES = 16

# "1. **name** — desc" / "- **name**: desc" / "2) **name**" …
_BOLD_ITEM = re.compile(r"^\s*(?:\d+[.)]|[-*])\s+\*\*(.+?)\*\*\s*[—:–-]?\s*(.*)$")
# plain "1. name — desc" (no bold) — the fallback shape inside a Run flow list
_PLAIN_ITEM = re.compile(r"^\s*\d+[.)]\s+([^—:]{1,60}?)\s*[—:]\s*(.*)$")


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
    return str(data.get("phase") or "") if isinstance(data, dict) else ""


def state_graph(routine_dir: Path) -> dict:
    """{states: [{name, desc}], current: str} for one routine/conversation dir. `current`
    is the raw recorded phase — the client matches it against states via norm()."""
    try:
        md = (routine_dir / "main.md").read_text(encoding="utf-8")
    except OSError:
        md = ""
    states = parse_states(md)
    if not states:  # recipe keeps its flow unparseable → the step modules ARE the map
        steps = routine_dir / "steps"
        if steps.is_dir():
            states = [{"name": p.stem, "desc": ""} for p in sorted(steps.glob("*.md"))][:MAX_STATES]
    return {"states": states, "current": current_phase(routine_dir)}
