"""The routine's state graph, derived from its stage modules — for the UI's live diagram.

A routine's stages/ modules ARE its states: decompose already maps the abstract workflow
onto task-specific module names at creation, so there is nothing to infer from prose —
every routine with stage modules has a diagram, unconditionally. Node order is main.md's
own routing: modules sort by where main.md first mentions them (its `## Run flow` list
references each one); unmentioned extras sort last, alphabetically. The CURRENT node is
the engine's live phase — the stage module the run last read (the executor stamps it into
ctx.phase → status.json → the run SSE `state` event) — so a recipe owes the diagram
nothing; its state/phase.json remains a private state file (the digest shows it), not a
UI contract.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

MAX_STATES = 16
STAGES_DIR = "stages"   # THE module dir — every routine on disk follows it (0.49.1)

# main.md's YAML frontmatter carries an ALPHABETICAL module list (scaffold provenance) —
# strip it before ranking mentions, or it would pose as every module's first mention.
_FRONTMATTER = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.DOTALL)


def norm(name: str) -> str:
    """Loose state identity: 'Gather Evidence' == 'gather-evidence'."""
    return re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")


def module_dir(routine_dir: Path) -> Path | None:
    """The routine's stage-module dir, or None when it has no stage modules."""
    d = routine_dir / STAGES_DIR
    return d if d.is_dir() else None


def module_rank(main_md: str, stem: str) -> tuple[int, int]:
    """Where main.md first references the module — the flow order without parsing any
    prose shape. Its `<stem>.md` FILE reference is the strongest signal (recipes route
    by file; intro prose only name-drops bare names), so it outranks the first word-ish
    plain mention. Word-ish boundaries keep a short stem from matching inside a longer
    one (`act` never matches `practices` or `act-apply-fixes`). Never-mentioned modules
    sort after every mentioned one.
    """
    body = _FRONTMATTER.sub("", main_md, count=1).lower()
    esc = re.escape(stem.lower())
    if m := re.search(rf"(?<![a-z0-9-]){esc}\.md", body):
        return (0, m.start())
    if m := re.search(rf"(?<![a-z0-9-]){esc}(?![a-z0-9-])", body):
        return (1, m.start())
    return (2, 0)


def _first_heading(path: Path) -> str:
    """The module's leading `# …` heading — the node's tooltip description."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    for line in text.splitlines():
        if m := _HEADING.match(line):
            return m.group(2).strip().strip("`")[:160]
        if line.strip():
            return ""   # prose before any heading — no description
    return ""


def stage_states(routine_dir: Path) -> list[dict]:
    """[{name, desc}] — one node per stage module, in main.md first-mention order."""
    d = module_dir(routine_dir)
    if d is None:
        return []
    try:
        main_md = (routine_dir / "main.md").read_text(encoding="utf-8")
    except OSError:
        main_md = ""
    files = sorted(d.glob("*.md"), key=lambda p: (module_rank(main_md, p.stem), p.stem))
    return [{"name": p.stem, "desc": _first_heading(p)} for p in files[:MAX_STATES]]


def current_phase(routine_dir: Path) -> str:
    """The latest run's recorded phase (status.json `phase` — the stage module the run
    last read). The routine-level graph's initial highlight; live transitions ride the
    run SSE `state` events, which carry the same field.
    """
    runs = routine_dir / "runs"
    try:
        latest = max((p for p in runs.iterdir()
                      if p.is_dir() and (p / "status.json").is_file()), default=None)
    except OSError:
        return ""
    if latest is None:
        return ""
    try:
        data = json.loads((latest / "status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(data.get("phase") or "") if isinstance(data, dict) else ""


def phase_stats(run_dir: Path) -> list[dict]:
    """Per-phase instrumentation from ONE run's transcript — turns, tokens (in+out),
    provider-reported cost, wall-clock seconds — in first-seen order. Each
    assistant_action event carries the phase that was active while it was produced
    (the engine stamps it); a turn's wall-clock is the gap from the previous event,
    and the tail after the last action (its dispatch) lands on the last phase. The
    empty-string phase collects turns from before any state/phase.json write.
    """
    from datetime import datetime

    from .engine.transcript import read_events

    events, _ = read_events(run_dir / "transcript.jsonl")

    def ts_of(ev) -> datetime | None:
        try:
            return datetime.fromisoformat(str(ev.get("ts") or ""))
        except ValueError:
            return None

    stats: dict[str, dict] = {}
    prev_ts = None
    last_cell = None
    for ev in events:
        t = ts_of(ev)
        if ev.get("type") == "assistant_action":
            name = str(ev.get("phase") or "")
            cell = stats.setdefault(name, {"phase": name, "turns": 0, "tokens": 0,
                                           "cost": 0.0, "elapsed_s": 0})
            usage = ev.get("usage") or {}
            cell["turns"] += 1
            cell["tokens"] += int(usage.get("in") or 0) + int(usage.get("out") or 0)
            cell["cost"] = round(cell["cost"] + float(usage.get("cost") or 0.0), 6)
            if t is not None and prev_ts is not None:
                cell["elapsed_s"] += max(0, int((t - prev_ts).total_seconds()))
            last_cell = cell
        elif last_cell is not None and t is not None and prev_ts is not None:
            # the gap after the last action (its dispatch) belongs to that action's phase
            last_cell["elapsed_s"] += max(0, int((t - prev_ts).total_seconds()))
        if t is not None:
            prev_ts = t
    return list(stats.values())


def state_graph(routine_dir: Path) -> dict:
    """{states: [{name, desc}], current: str} for one routine dir — the stage modules +
    the live phase. `current` is the raw recorded phase — the client matches it against
    states via norm().
    """
    return {"states": stage_states(routine_dir), "current": current_phase(routine_dir)}


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
    (in main.md first-mention order, unmentioned extras appended alphabetically) + trait modules,
    each with its heading outline. Purely a read-model over the routine's own files.
    """
    def _read(p: Path) -> str:
        try:
            return p.read_text(encoding="utf-8")
        except OSError:
            return ""

    main_md = _read(routine_dir / "main.md")

    def _entry(p: Path) -> dict:
        return {"path": str(p.relative_to(routine_dir)), "name": p.stem,
                "outline": outline(_read(p))}

    def _files(sub: str) -> list[Path]:
        d = routine_dir / sub
        return sorted(d.glob("*.md")) if d.is_dir() else []

    stages = module_dir(routine_dir)
    stage_files = sorted(stages.glob("*.md") if stages else [],
                         key=lambda p: (module_rank(main_md, p.stem), p.stem))
    return {
        "main": {"path": "main.md", "name": "main", "outline": outline(main_md)},
        "stages": [_entry(p) for p in stage_files],
        "traits": [_entry(p) for p in _files("traits")],
    }
