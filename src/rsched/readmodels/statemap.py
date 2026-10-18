"""The routine's PHASE graph, derived from its stage modules — for the UI's live diagram.

NAMING (D193, the one rule — stated in CLAUDE.md "Phase, stage, state"): the CONCEPT is a
`phase`. That is the key the engine persists (`state/phase.json`, status.json), the field the
API serves and the word the UI says. `stages/` is the recipe's FILE layout — the modules a
phase is derived FROM — and `state` keeps its older, unrelated meanings (a run's state, the
`state/` directory, a read model's state), which is why the concept is not called that. This
module's filename is one of those older meanings and stays as it is.

A routine's stages/ modules ARE its phases: decompose already maps the abstract workflow
onto task-specific module names at creation, so there is nothing to infer from prose —
every routine with stage modules has a diagram, unconditionally. Node order is main.md's
own routing: modules sort by where main.md first mentions them (its `## Run flow` list
references each one); unmentioned extras sort last, alphabetically. The CURRENT node is
the engine's live phase — the stage module the run last read (the executor stamps it into
ctx.phase → status.json → the run tail's `state` event) — so a recipe owes the diagram
nothing; its state/phase.json remains a private state file (the digest shows it), not a
UI contract.
"""

from __future__ import annotations

import re
from pathlib import Path

from .. import paths

# Diagram cap: the rail's SVG renders one column of nodes — beyond ~16 the graph is
# unreadable and a recipe with more stages is itself the smell (decompose produces 3-8).
# A capped listing drops the LEAST-mentioned modules (files are pre-ranked by first
# mention), so the visible graph is always the recipe's spine.
MAX_PHASES = 16
STAGES_DIR = "stages"   # THE module dir — every routine on disk follows it (0.49.1)

# main.md's YAML frontmatter carries an ALPHABETICAL module list (scaffold provenance) —
# strip it before ranking mentions, or it would pose as every module's first mention.
_FRONTMATTER = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.DOTALL)


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


def stage_phases(routine_dir: Path) -> list[dict]:
    """[{name, desc}] — one phase node per stage module, in main.md first-mention order."""
    d = module_dir(routine_dir)
    if d is None:
        return []
    try:
        main_md = (routine_dir / "main.md").read_text(encoding="utf-8")
    except OSError:
        main_md = ""
    files = sorted(d.glob("*.md"), key=lambda p: (module_rank(main_md, p.stem), p.stem))
    return [{"name": p.stem, "desc": _first_heading(p)} for p in files[:MAX_PHASES]]


def stage_coverage(routine_dir: Path, entered: list[str],
                   recorded: list[str] | None = None) -> dict:
    """What a run DECLARED against what it actually entered (F521/R1681).

    `{"declared": [...], "entered": [...], "skipped": [...]}`, every list in the
    recipe's own flow order — the order `stage_phases` derives from main.md, so a
    reader sees the gap where it falls in the run rather than alphabetically.

    A recipe with no stage modules declares nothing and can skip nothing, so it gets
    three empty lists and the caller stays silent. `entered` is filtered against the
    declared set: a run that read a module which is no longer declared (a recipe edited
    mid-run) is not evidence about the current recipe, and counting it would make
    `entered` longer than `declared`.

    TWO sources of evidence, because one of them is not enough (F563). `entered` comes
    from `fileops`, which appends a stage the turn a run READS `stages/<name>.md` — so on
    its own it measures module RE-READS, not stage work, and the runs that stop re-reading
    are the ones whose recipe the model already holds. Measured on 2026-09-26: 24 of 73
    fleet runs reported >=60% of their declared stages skipped, `llmsectest-weekday`
    reporting 0 of 7 entered on three consecutive runs of 404-423 turns. Those runs did
    the work; the signal was wrong.

    So `recorded` — the phases the run WROTE for itself, its own cursor — counts as
    entering too. It is the stronger of the two signals (a deliberate declaration rather
    than an incidental read) and it costs the recipe nothing it was not already doing.
    This does NOT make that cursor drive the diagram's live `current`, which stays the
    executor-stamped phase: this function answers "was this stage worked", not "where is
    the run now".
    """
    declared = [s["name"] for s in stage_phases(routine_dir)]
    seen = set(entered) | set(recorded or ())
    visited = [name for name in declared if name in seen]
    return {"declared": declared, "entered": visited,
            "skipped": [name for name in declared if name not in seen]}


def current_phase(routine_dir: Path) -> str:
    """The latest run's recorded phase (status.json `phase` — the stage module the run
    last read). The routine-level graph's initial highlight; live transitions ride the
    run tail `state` events, which carry the same field.
    """
    runs = routine_dir / "runs"
    try:
        latest = max((p for p in runs.iterdir()
                      if p.is_dir() and (p / "status.json").is_file()), default=None)
    except OSError:
        return ""
    if latest is None:
        return ""
    data = paths.read_json(latest / "status.json")
    return str(data.get("phase") or "") if isinstance(data, dict) else ""


def phase_stats(run_dir: Path) -> list[dict]:
    """Per-phase instrumentation from ONE run's transcript — turns, tokens (in+out),
    provider-reported cost, wall-clock seconds, and `first_turn` — in first-seen order. Each
    assistant_action event carries the phase that was active while it was produced
    (the engine stamps it); a turn's wall-clock is the gap from the previous event,
    and the tail after the last action (its dispatch) lands on the last phase. The
    empty-string phase collects turns from before any state/phase.json write.

    `first_turn` is WHERE the phase begins — the turn number of its first action, which is
    what the rail's node links to in the transcript (D193). None when the transcript numbers
    no action under that phase: a link to turn 0 would land nowhere, so the UI renders a
    plain node instead.

    Memoized on the transcript's stat fingerprint — the rail polls this endpoint every
    few seconds and used to re-parse the whole transcript each tick.
    """
    from . import memo

    return memo.memoized(f"phases:{run_dir}",
                         [run_dir / "transcript.jsonl", run_dir / "transcript.jsonl.gz"],
                         lambda: _phase_stats(run_dir))


def _phase_stats(run_dir: Path) -> list[dict]:
    from ..engine.transcript import read_events
    from .stamps import instant

    events, _ = read_events(run_dir / "transcript.jsonl")
    stats: dict[str, dict] = {}
    prev_ts = None
    last_cell = None
    for ev in events:
        t = instant(ev.get("ts"))
        if ev.get("type") == "assistant_action":
            name = str(ev.get("phase") or "")
            cell = stats.setdefault(name, {"phase": name, "turns": 0, "tokens": 0,
                                           "cost": 0.0, "elapsed_s": 0, "first_turn": None})
            usage = ev.get("usage") or {}
            cell["turns"] += 1
            if cell["first_turn"] is None:
                turn = ev.get("turn")
                cell["first_turn"] = int(turn) if isinstance(turn, int) else None
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


def phase_graph(routine_dir: Path) -> dict:
    """{phases: [{name, desc}], current: str} for one routine dir — the stage modules +
    the live phase. `current` is the RAW recorded phase: the client loosens it to match a
    phase name (`static/components/phasegraph.js`), because the loosening is a rendering
    decision and doing it here would throw away what the run actually recorded.
    """
    return {"phases": stage_phases(routine_dir), "current": current_phase(routine_dir)}


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
    (in main.md first-mention order, unmentioned extras appended alphabetically),
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

    stages = module_dir(routine_dir)
    stage_files = sorted(stages.glob("*.md") if stages else [],
                         key=lambda p: (module_rank(main_md, p.stem), p.stem))
    return {
        "main": {"path": "main.md", "name": "main", "outline": outline(main_md)},
        "stages": [_entry(p) for p in stage_files],
    }
